"""Snapshot endpoints.

    GET  /api/snapshots                  List archived snapshots (history)
    POST /api/snapshots/refresh          Check for new data; archive current; reload
    GET  /api/snapshots/<id>             Snapshot metadata + summary

The dashboard's "Refresh" button hits POST /refresh. If the source CSVs
have changed since the in-memory state was loaded, the current state is
archived to outputs/_snapshots/<data_date>/ and the new files are loaded.
"""
import logging

from flask import Blueprint, current_app, g, jsonify, request, send_file

from services.rbac import requires_auth, requires_role

log = logging.getLogger(__name__)

bp = Blueprint("snapshots", __name__, url_prefix="/api/snapshots")


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


@bp.route("", methods=["GET"])
@requires_auth
def list_snapshots():
    snaps = current_app.config["SNAPSHOTS"]
    return jsonify({"snapshots": snaps.list_snapshots()})


@bp.route("/refresh", methods=["POST"])
@requires_auth
def refresh():
    """Check for new data. If found, archive current and reload.

    Response shape:
      {
        "status": "refreshed" | "already_current",
        "previous_data_date": "YYYY-MM-DD" | null,
        "new_data_date": "YYYY-MM-DD" | null,
        "archived_snapshot_id": "<id>" | null
      }
    """
    store = current_app.config["DATA_STORE"]
    snaps = current_app.config["SNAPSHOTS"]
    audit = current_app.config["AUDIT_LOG"]
    actor = g.current_user["sub"]
    ip = _client_ip()

    source_mtime = store.latest_source_mtime()
    loaded_mtime = store.loaded_mtime()
    previous_data_date = store.data_date
    previous_summary = store.summary_stats()

    if source_mtime <= loaded_mtime:
        audit.write("snapshots.refresh_noop", actor, ip,
                    {"data_date": previous_data_date})
        return jsonify({
            "status": "already_current",
            "previous_data_date": previous_data_date,
            "new_data_date": previous_data_date,
            "archived_snapshot_id": None,
        })

    # Source files have changed — archive the previous state, then reload
    archived = snaps.archive_current(
        data_date=previous_data_date,
        archived_by=actor,
        summary_stats=previous_summary,
    )
    store.reload()
    new_data_date = store.data_date

    audit.write("snapshots.refresh", actor, ip, {
        "previous_data_date": previous_data_date,
        "new_data_date": new_data_date,
        "archived_snapshot_id": archived["id"] if archived else None,
    })

    return jsonify({
        "status": "refreshed",
        "previous_data_date": previous_data_date,
        "new_data_date": new_data_date,
        "archived_snapshot_id": archived["id"] if archived else None,
    })


@bp.route("/<snapshot_id>", methods=["GET"])
@requires_auth
def get_snapshot(snapshot_id):
    snaps = current_app.config["SNAPSHOTS"]
    entry = snaps.find_snapshot(snapshot_id)
    if entry is None:
        return jsonify({"error": "not_found", "id": snapshot_id}), 404
    return jsonify(entry)


@bp.route("/<snapshot_id>/download/<filename>", methods=["GET"])
@requires_auth
@requires_role("admin")
def download_snapshot_file(snapshot_id, filename):
    """Download a specific CSV from an archived snapshot. Admin only."""
    snaps = current_app.config["SNAPSHOTS"]
    snap_dir = snaps.snapshot_dir(snapshot_id)
    if snap_dir is None:
        return jsonify({"error": "not_found", "id": snapshot_id}), 404

    ALLOWED_FILES = {"master_anomalies.csv", "LLM_Explainability.csv", "CR_LLM.csv"}
    if filename not in ALLOWED_FILES:
        return jsonify({"error": "forbidden", "message": "File not available for download"}), 403

    file_path = snap_dir / filename
    if not file_path.exists():
        return jsonify({"error": "not_found", "message": f"{filename} not in snapshot {snapshot_id}"}), 404

    current_app.config["AUDIT_LOG"].write(
        "snapshot.download", g.current_user["sub"], _client_ip(),
        {"snapshot_id": snapshot_id, "filename": filename},
    )

    return send_file(
        file_path,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"{snapshot_id}_{filename}",
    )
