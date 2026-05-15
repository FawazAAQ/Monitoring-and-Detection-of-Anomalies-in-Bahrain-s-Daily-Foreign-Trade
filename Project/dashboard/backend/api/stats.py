"""Stats endpoints — KPIs + donut + timeseries for the dashboard."""
import logging

from flask import Blueprint, current_app, g, jsonify, request

from services.rbac import requires_auth, requires_role

log = logging.getLogger(__name__)

bp = Blueprint("stats", __name__, url_prefix="/api/stats")


@bp.route("/summary", methods=["GET"])
@requires_auth
def summary():
    store = current_app.config["DATA_STORE"]
    return jsonify(store.summary_stats())


@bp.route("/donut", methods=["GET"])
@requires_auth
def donut():
    """HIGH/MEDIUM/LOW counts shaped for the donut on the summary page."""
    store = current_app.config["DATA_STORE"]
    return jsonify(store.donut_data())


@bp.route("/timeseries", methods=["GET"])
@requires_auth
def timeseries():
    """Daily flag counts for one month.
    Query: ?year_month=YYYY-MM (default: month of latest data_date).
    """
    store = current_app.config["DATA_STORE"]
    ym = request.args.get("year_month")
    return jsonify(store.timeseries(year_month=ym))


@bp.route("/reload", methods=["POST"])
@requires_role("admin")
def reload_data():
    store = current_app.config["DATA_STORE"]
    store.reload()
    ip = (request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
          .split(",")[0].strip())
    current_app.config["AUDIT_LOG"].write(
        "data.reload", g.current_user["sub"], ip,
        {"rows": int(len(store.merged)), "data_date": store.data_date},
    )
    return jsonify({"status": "reloaded", "rows": int(len(store.merged)),
                    "data_date": store.data_date})
