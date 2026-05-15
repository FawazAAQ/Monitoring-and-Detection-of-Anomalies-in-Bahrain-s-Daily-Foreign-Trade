"""CR analysis endpoints.

    GET /api/cr/top                  Top recurring CRs (sortable by count or HIGH count)
    GET /api/cr/<cr>                 Full breakdown for one CR

CRs are masked for non-admin roles (***last3) in both list and detail.
"""
import logging

from flask import Blueprint, current_app, g, jsonify, request

from services.masking import mask_cr
from services.rbac import requires_auth

log = logging.getLogger(__name__)

bp = Blueprint("cr", __name__, url_prefix="/api/cr")


def _mask_top_row(row: dict, role: str) -> dict:
    out = dict(row)
    out["active_cr_full"] = out.get("active_cr")  # admin has full; we mask below for non-admin
    out["active_cr"] = mask_cr(out["active_cr"], role)
    if role != "admin":
        out["active_cr_full"] = out["active_cr"]
    return out


@bp.route("/top", methods=["GET"])
@requires_auth
def top_crs():
    """Sortable list of top recurring CRs.

    Query:
        sort   count | high   (default count)
        limit  default 20, max 100
    """
    store = current_app.config["DATA_STORE"]
    role = g.current_user["role"]

    sort = request.args.get("sort", "count")
    if sort not in {"count", "high"}:
        sort = "count"
    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(100, limit))

    rows = store.top_crs(sort=sort, limit=limit)
    rows = [_mask_top_row(r, role) for r in rows]
    return jsonify({"sort": sort, "rows": rows})


@bp.route("/<path:cr>", methods=["GET"])
@requires_auth
def cr_detail(cr):
    """Full breakdown for a single CR — partners, HS codes, all flagged items."""
    store = current_app.config["DATA_STORE"]
    role = g.current_user["role"]

    detail = store.cr_detail(cr)
    if detail is None:
        return jsonify({"error": "not_found", "cr": cr}), 404

    detail["active_cr"] = mask_cr(detail["active_cr"], role)
    return jsonify(detail)
