"""Optional password gate for a public demo.

Off by default: local development needs no login. Set ``DEMO_PASSWORD`` in the
environment to require a password on a hosted copy. Uses only the standard
library: a signed, time-limited cookie, with no server-side session store and
no dependency on the LLM or device backend.

The password itself is never written to the repository -- it is read from the
environment so the hosting platform (systemd / PaaS) owns the secret.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

COOKIE = "fsd_session"
SESSION_TTL = 24 * 60 * 60  # seconds
ENV_PASSWORD = "DEMO_PASSWORD"


def password() -> str:
    return (os.environ.get(ENV_PASSWORD) or "").strip()


def enabled() -> bool:
    return bool(password())


def _secret() -> bytes:
    # Derive the signing key from the password. Rotating the password
    # invalidates every existing cookie, which is the desired behaviour.
    return hashlib.sha256(("fsd-demo|" + password()).encode("utf-8")).digest()


def issue_token() -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(time.time()) + SESSION_TTL}).encode("utf-8")
    ).decode("ascii").rstrip("=")
    sig = hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    payload, _, sig = token.rpartition(".")
    expected = hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        pad = "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload + pad))
        return int(data.get("exp", 0)) > time.time()
    except (ValueError, TypeError, OSError):
        return False


def check(pw: str) -> bool:
    """Constant-time comparison; only meaningful when auth is enabled."""
    return enabled() and hmac.compare_digest(str(pw), password())


def cookie_header(token: str, secure: bool = False) -> str:
    parts = [f"{COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Lax",
             f"Max-Age={SESSION_TTL}"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie_header() -> str:
    return f"{COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
