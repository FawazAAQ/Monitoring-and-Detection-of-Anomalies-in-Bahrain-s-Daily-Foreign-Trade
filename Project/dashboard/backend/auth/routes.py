"""Authentication endpoints.

Routes:
    POST   /api/auth/login     { employee_id, password } -> token + session info
    POST   /api/auth/logout    -> audit-log the event (token remains valid until expiry;
                                  stateless JWT cannot be server-revoked without a blocklist)
    GET    /api/auth/session   -> echo the current session hash + expiry (requires auth)
    GET    /api/auth/me        -> current user profile (requires auth)
"""
import logging
import time

from flask import Blueprint, current_app, g, jsonify, request

from services.rbac import requires_auth
from services.security import (
    issue_token,
    make_device_fingerprint,
    make_session_hash,
    verify_password,
)

log = logging.getLogger(__name__)

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


@bp.route("/login", methods=["POST"])
def login():
    body = request.get_json(silent=True) or {}
    employee_id = (body.get("employee_id") or "").strip()
    password = body.get("password") or ""
    ip = _client_ip()
    audit = current_app.config["AUDIT_LOG"]

    if not employee_id or not password:
        audit.write("auth.login_failure", employee_id or None, ip, {"reason": "missing_credentials"})
        return jsonify({"error": "missing_credentials"}), 400

    users = current_app.config["USERS"]
    user = users.get(employee_id)

    # Always run a bcrypt check to keep timing uniform, even when user is missing.
    # Using a fixed dummy hash avoids leaking user existence via response time.
    dummy_hash = "$2b$12$CwTycUXWue0Thq9StjUM0uJ8.c1P8bQ1j8IUqOjKVHVh8vjTd7bjW"
    if user is None:
        verify_password(password, dummy_hash)
        audit.write("auth.login_failure", employee_id, ip, {"reason": "unknown_user"})
        return jsonify({"error": "invalid_credentials"}), 401

    if not verify_password(password, user["password_hash"]):
        audit.write("auth.login_failure", employee_id, ip, {"reason": "wrong_password"})
        return jsonify({"error": "invalid_credentials"}), 401

    device_fp = make_device_fingerprint(request)
    token, issued_at, session_hash = issue_token(
        employee_id=employee_id,
        role=user["role"],
        device_fp=device_fp,
        secret=current_app.config["SECRET_KEY"],
    )

    audit.write("auth.login_success", employee_id, ip, {"role": user["role"]})

    return jsonify({
        "token": token,
        "session_hash": session_hash,
        "issued_at": issued_at,
        "expires_at": issued_at + 8 * 60 * 60,
        "user": {
            "employee_id": employee_id,
            "name": user["name"],
            "role": user["role"],
        },
    })


@bp.route("/logout", methods=["POST"])
@requires_auth
def logout():
    ip = _client_ip()
    actor = g.current_user["sub"]
    current_app.config["AUDIT_LOG"].write("auth.logout", actor, ip)
    return jsonify({"status": "logged_out"})


@bp.route("/session", methods=["GET"])
@requires_auth
def session():
    """Return the session hash + expiry for the dashboard header display."""
    payload = g.current_user
    session_hash = make_session_hash(
        payload["sub"], payload["iat"], current_app.config["SECRET_KEY"]
    )
    now = int(time.time())
    return jsonify({
        "session_hash": session_hash,
        "issued_at": payload["iat"],
        "expires_at": payload["exp"],
        "seconds_remaining": max(0, payload["exp"] - now),
        "device_fp_short": payload["device_fp"][:12],
    })


@bp.route("/me", methods=["GET"])
@requires_auth
def me():
    payload = g.current_user
    user = current_app.config["USERS"].get(payload["sub"], {})
    return jsonify({
        "employee_id": payload["sub"],
        "role": payload["role"],
        "name": user.get("name"),
    })
