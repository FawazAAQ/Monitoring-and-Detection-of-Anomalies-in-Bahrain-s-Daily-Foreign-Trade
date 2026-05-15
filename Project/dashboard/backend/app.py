"""Flask app factory."""
import logging
from pathlib import Path

from flask import Flask, jsonify
from flask_cors import CORS

from config import Config
from services.audit import AuditLog
from services.data_loader import DataStore
from services.security import load_users
from services.snapshots import SnapshotService

from auth.routes import bp as auth_bp
from api.stats import bp as stats_bp
from api.anomalies import bp as anomalies_bp
from api.explanations import bp as explanations_bp
from api.cr import bp as cr_bp
from api.snapshots import bp as snapshots_bp
from api.audit import bp as audit_bp


def create_app(config_class=Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    logging.basicConfig(
        level=config_class.LOG_LEVEL,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    log = logging.getLogger(__name__)
    log.info(f"Starting backend — data dir: {config_class.DATA_DIR}")

    CORS(app, origins=config_class.CORS_ORIGINS, supports_credentials=True)

    store = DataStore(data_dir=config_class.DATA_DIR)
    app.config["DATA_STORE"] = store
    app.config["SNAPSHOTS"] = SnapshotService(config_class.DATA_DIR)

    users_path = Path(__file__).parent / "users.json"
    users = load_users(users_path)
    app.config["USERS"] = users
    log.info(f"Loaded {len(users)} users")

    app.config["AUDIT_LOG"] = AuditLog(Path(__file__).parent / "audit.log")

    app.register_blueprint(auth_bp)
    app.register_blueprint(stats_bp)
    app.register_blueprint(anomalies_bp)
    app.register_blueprint(explanations_bp)
    app.register_blueprint(cr_bp)
    app.register_blueprint(snapshots_bp)
    app.register_blueprint(audit_bp)

    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "service": "trade-anomaly-backend"})

    @app.route("/")
    def dashboard():
        html_path = Path(__file__).parent.parent / "dashboard.html"
        if html_path.exists():
            return html_path.read_text(encoding="utf-8")
        return "Dashboard HTML not found. Expected at: " + str(html_path), 404

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=True)
