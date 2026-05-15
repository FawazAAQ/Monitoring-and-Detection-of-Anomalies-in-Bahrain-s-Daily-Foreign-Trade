"""Standalone explanation endpoint — pulls one row from LLM_Explainability.csv."""
import logging

from flask import Blueprint, current_app, jsonify

from services.rbac import requires_auth

log = logging.getLogger(__name__)

bp = Blueprint("explanations", __name__, url_prefix="/api/explanations")


@bp.route("/<item_id>", methods=["GET"])
@requires_auth
def get_explanation(item_id):
    store = current_app.config["DATA_STORE"]
    raw = store.llm_for(item_id)
    if raw is None:
        return jsonify({"error": "not_found", "item_id": item_id}), 404
    return jsonify(raw)
