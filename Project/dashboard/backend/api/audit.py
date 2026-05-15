"""Audit log endpoint — read-only, admin only."""
import json
import logging

from flask import Blueprint, current_app, jsonify, request, g

from services.rbac import requires_auth, requires_role

log = logging.getLogger(__name__)

bp = Blueprint("audit", __name__, url_prefix="/api/audit")


@bp.route("", methods=["GET"])
@requires_auth
@requires_role("admin")
def list_audit():
    """Return audit log entries, newest first. Admin only.

    Query params:
      limit    default 100, max 500
    """
    limit = min(int(request.args.get("limit", 100)), 500)
    audit_path = current_app.config["AUDIT_LOG"].path

    entries = []
    if audit_path.exists():
        with open(audit_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    # Newest first
    entries.reverse()
    entries = entries[:limit]

    return jsonify({"entries": entries, "total": len(entries)})
