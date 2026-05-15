"""Anomaly endpoints — list (paginated/filtered) and full detail view."""
import logging

from flask import Blueprint, current_app, g, jsonify, request

from services.masking import mask_cr, mask_row
from services.rbac import requires_auth

log = logging.getLogger(__name__)

bp = Blueprint("anomalies", __name__, url_prefix="/api/anomalies")


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _parse_int(name, default, minimum=1, maximum=10_000):
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, v))


@bp.route("", methods=["GET"])
@requires_auth
def list_anomalies():
    """Paginated, filterable list of anomalies.

    Query params:
      page         default 1
      page_size    default 25, max 100
      flag_level   comma-separated subset of LOW,MEDIUM,HIGH,NORMAL (default: LOW,MEDIUM,HIGH)
      year_month   string YYYY-MM
      trade_type   import | export | re-export
      search       free-text across item_id / cr / hs / countries
      sort         allowlist: final_score, year_month, item_id, declaration_date
      dir          asc | desc
    """
    store = current_app.config["DATA_STORE"]
    role = g.current_user["role"]

    flag_raw = request.args.get("flag_level")
    flag_levels = None
    if flag_raw:
        flag_levels = [s.strip().upper() for s in flag_raw.split(",")
                       if s.strip().upper() in {"LOW", "MEDIUM", "HIGH", "NORMAL"}]

    trade_type = request.args.get("trade_type")
    if trade_type and trade_type not in {"import", "export", "re-export", "Import", "Export", "Re-export"}:
        trade_type = None

    result = store.list_anomalies(
        page=_parse_int("page", 1),
        page_size=_parse_int("page_size", 25, minimum=1, maximum=100),
        flag_levels=flag_levels,
        year_month=request.args.get("year_month"),
        trade_type=trade_type,
        search=request.args.get("search"),
        sort_by=request.args.get("sort", "final_score"),
        sort_dir=request.args.get("dir", "desc"),
    )
    result["items"] = [mask_row(r, role) for r in result["items"]]
    return jsonify(result)


@bp.route("/<item_id>", methods=["GET"])
@requires_auth
def get_anomaly(item_id):
    store = current_app.config["DATA_STORE"]
    role = g.current_user["role"]
    row = store.get_anomaly(item_id)
    if row is None:
        return jsonify({"error": "not_found", "item_id": item_id}), 404

    row = mask_row(row, role)
    if row.get("_cr_llm_raw"):
        row["_cr_llm_raw"] = dict(row["_cr_llm_raw"])
        if "active_cr" in row["_cr_llm_raw"]:
            row["_cr_llm_raw"]["active_cr"] = mask_cr(row["_cr_llm_raw"]["active_cr"], role)
    if row.get("_llm_raw"):
        row["_llm_raw"] = dict(row["_llm_raw"])
        if "active_cr" in row["_llm_raw"]:
            row["_llm_raw"]["active_cr"] = mask_cr(row["_llm_raw"]["active_cr"], role)

    current_app.config["AUDIT_LOG"].write(
        "record.view", g.current_user["sub"], _client_ip(),
        {"item_id": item_id, "role": role},
    )
    return jsonify(row)
