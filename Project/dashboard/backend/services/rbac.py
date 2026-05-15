"""Role-based access control decorators.

Usage:
    @bp.route("/protected")
    @requires_auth
    def protected():
        user = g.current_user  # payload dict: sub, role, device_fp, iat, exp
        ...

    @bp.route("/admin-only")
    @requires_role("admin")
    def admin_only():
        ...
"""
import logging
from functools import wraps

from flask import current_app, g, jsonify, request

from services.security import decode_token, make_device_fingerprint

log = logging.getLogger(__name__)


def _extract_token() -> str | None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return auth[7:].strip()


def requires_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _extract_token()
        if not token:
            return jsonify({"error": "missing_token"}), 401

        payload = decode_token(token, current_app.config["SECRET_KEY"])
        if payload is None:
            return jsonify({"error": "invalid_or_expired_token"}), 401

        # Device fingerprint check — blocks token reuse across machines
        current_fp = make_device_fingerprint(request)
        if payload.get("device_fp") != current_fp:
            log.warning(f"Device fingerprint mismatch for user {payload.get('sub')} — token rejected")
            return jsonify({"error": "device_mismatch"}), 401

        g.current_user = payload
        return fn(*args, **kwargs)

    return wrapper


def requires_role(*allowed_roles: str):
    """Stack on top of requires_auth. Pass any role that may access the route."""
    def decorator(fn):
        @wraps(fn)
        @requires_auth
        def wrapper(*args, **kwargs):
            role = g.current_user.get("role")
            if role not in allowed_roles:
                log.info(f"Access denied for {g.current_user.get('sub')}: role={role}, required={allowed_roles}")
                return jsonify({"error": "forbidden", "required_roles": list(allowed_roles)}), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator
