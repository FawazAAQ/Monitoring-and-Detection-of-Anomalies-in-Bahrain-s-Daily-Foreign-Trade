"""Security primitives for the dashboard backend.

Functions exposed:
- hash_password / verify_password   -> bcrypt, cost factor 12
- make_session_hash                 -> short SHA256 digest shown in dashboard header
- make_device_fingerprint           -> SHA256 of user-agent + remote IP
- issue_token / decode_token        -> JWT HS256, 8-hour expiry

notes:
- We issue JWTs rather than server-side sessions so the backend stays
  stateless. The React SPA stores the token in memory (not localStorage)
  to reduce XSS exposure, and sends it on every request as a Bearer token.
- Device fingerprint is captured at login and baked into the JWT. Every
  authenticated request re-computes the fingerprint and rejects on mismatch,
  which blocks token theft across machines.
- Session hash is cosmetic but real — it gives the analyst something to
  visually verify in the header, which is part of the security UX story.
"""
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional

import bcrypt
import jwt
from flask import Request

log = logging.getLogger(__name__)

TOKEN_TTL_SECONDS = 8 * 60 * 60  # 8 hours — long enough for a work session


def hash_password(password: str) -> str:
    """Bcrypt-hash a password. Returns UTF-8 string for JSON storage."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Constant-time bcrypt verification."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def make_session_hash(employee_id: str, issued_at: int, secret: str) -> str:
    """Short deterministic digest shown in the dashboard header.
    Not used for auth — purely for the 'session integrity' visual in the UI.
    """
    raw = f"{employee_id}|{issued_at}|{secret}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def make_device_fingerprint(request: Request) -> str:
    """SHA256 of user-agent + remote IP. Captured at login, verified on each request."""
    ua = request.headers.get("User-Agent", "")
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    # Take first IP if proxy chain
    ip = ip.split(",")[0].strip()
    raw = f"{ua}|{ip}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_token(
    employee_id: str,
    role: str,
    device_fp: str,
    secret: str,
) -> tuple[str, int, str]:
    """Issue a JWT. Returns (token, issued_at_epoch, session_hash)."""
    now = int(time.time())
    payload = {
        "sub": employee_id,
        "role": role,
        "device_fp": device_fp,
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    session_hash = make_session_hash(employee_id, now, secret)
    return token, now, session_hash


def decode_token(token: str, secret: str) -> Optional[dict]:
    """Decode and validate a JWT. Returns payload dict or None on any failure."""
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        log.info("Token rejected: expired")
    except jwt.InvalidTokenError as e:
        log.info(f"Token rejected: {e}")
    return None


def load_users(users_path: Path) -> dict:
    """Load users.json into a dict keyed by employee_id."""
    if not users_path.exists():
        log.error(f"users.json not found at {users_path} — run generate_users.py first")
        return {}
    with open(users_path, "r", encoding="utf-8") as f:
        users = json.load(f)
    return {u["employee_id"]: u for u in users}
