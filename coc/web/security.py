"""Session handling, CSRF, and response hardening.

The interesting constraint here is that this application serves files that
other people uploaded.  An SVG or an HTML file admitted as evidence is
attacker-controlled content sitting on the same origin as the examiner's
session, so it gets handled as hostile by default: served as a download, with
sniffing disabled and a sandboxing header of its own.  Only a short allow-list
of raster image types is ever rendered inline.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import Response, current_app, g, jsonify, request, session

SESSION_USER_KEY = "examiner_id"
SESSION_CSRF_KEY = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"

#: Types safe to hand to an <img> tag.  SVG is deliberately absent: it is a
#: document format that can carry script, not an image format.
INLINE_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'nonce-{nonce}'; "
    "style-src 'self'; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "worker-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)

#: Applied to responses that carry uploaded bytes.  Nothing in an evidence file
#: is allowed to load, execute, or reach the network.
UPLOADED_CONTENT_POLICY = "default-src 'none'; sandbox; style-src 'unsafe-inline'"


def csrf_token() -> str:
    """The current session's CSRF token, minted on first use."""
    token = session.get(SESSION_CSRF_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[SESSION_CSRF_KEY] = token
    return token


def check_csrf() -> Response | None:
    """Reject a state-changing request that does not echo the session token."""
    if request.method in SAFE_METHODS:
        return None
    if request.endpoint in current_app.config.get("CSRF_EXEMPT_ENDPOINTS", set()):
        return None

    expected = session.get(SESSION_CSRF_KEY)
    presented = request.headers.get(CSRF_HEADER) or request.form.get("csrf_token")
    if not expected or not presented or not secrets.compare_digest(expected, presented):
        return jsonify({"error": "csrf_failed", "message": "Stale session token. Reload the page."}), 403
    return None


def apply_security_headers(response: Response) -> Response:
    """Harden every response leaving the application."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")

    if "Content-Security-Policy" not in response.headers:
        nonce = getattr(g, "csp_nonce", "")
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY.format(nonce=nonce)
    return response


def harden_uploaded_response(response: Response, *, inline: bool) -> Response:
    """Extra headers for a response whose body is user-supplied content."""
    response.headers["Content-Security-Policy"] = UPLOADED_CONTENT_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    if not inline:
        response.headers["Content-Disposition"] = response.headers.get(
            "Content-Disposition", "attachment"
        )
    return response


def current_examiner():
    """The authenticated examiner for this request, or ``None``."""
    if "examiner" in g:
        return g.examiner
    user_id = session.get(SESSION_USER_KEY)
    if user_id is None:
        g.examiner = None
    else:
        g.examiner = current_app.extensions["keeneye"].get_user(int(user_id))
        if g.examiner is None:
            session.pop(SESSION_USER_KEY, None)
    return g.examiner


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapper(*args: Any, **kwargs: Any):
        if current_examiner() is None:
            return jsonify({"error": "unauthenticated", "message": "Sign in to continue."}), 401
        return view(*args, **kwargs)

    return wrapper


def admin_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapper(*args: Any, **kwargs: Any):
        examiner = current_examiner()
        if examiner is None:
            return jsonify({"error": "unauthenticated", "message": "Sign in to continue."}), 401
        if not examiner.is_admin:
            return jsonify({"error": "forbidden", "message": "Administrator role required."}), 403
        return view(*args, **kwargs)

    return wrapper


def sign_in(examiner) -> None:
    session.clear()
    session[SESSION_USER_KEY] = examiner.id
    session.permanent = True
    csrf_token()
    g.examiner = examiner


def sign_out() -> None:
    session.clear()
    g.examiner = None
