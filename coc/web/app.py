"""Flask application factory.

The web front-end is a thin shell: it serves one HTML page that boots the 3D
world, a JSON API, and file downloads.  All of the actual logic lives in
:class:`coc.service.Workspace`, which the API calls into — so the browser can
never reach state that was changed without a custody entry.
"""

from __future__ import annotations

import os
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, g, render_template

from coc import TOOL_NAME, __version__
from coc.service import ServiceError, Workspace
from coc.storage import StorageError
from coc.web import security

DEFAULT_WORKSPACE = "workspace"
ENV_FILENAME = ".env"

#: 512 MiB.  Disk images get far bigger than this; the CLI is the right tool
#: for those, and the limit keeps a browser upload from filling the disk.
DEFAULT_MAX_UPLOAD_BYTES = 512 * 1024 * 1024


class ConfigurationError(RuntimeError):
    """Raised when the application cannot start safely."""


def load_env_file(path: str | Path = ENV_FILENAME) -> dict[str, str]:
    """Read a minimal ``KEY=value`` file into a dict, ignoring comments."""
    path = Path(path)
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def resolve_secret_key(*, env_path: str | Path = ENV_FILENAME, allow_generate: bool = True) -> str:
    """Find the key that signs session cookies, generating one on first run.

    Session cookies carry the identity that every custody entry is attributed
    to, so a forgeable cookie would mean forgeable attribution.  The key is
    therefore never allowed to fall back to a hardcoded default: it is either
    supplied, or freshly generated and written to ``.env`` once.
    """
    key = os.environ.get("COC_SECRET_KEY") or load_env_file(env_path).get("COC_SECRET_KEY")
    if key:
        return key

    if not allow_generate:
        raise ConfigurationError(
            "COC_SECRET_KEY is not set. Generate one with:\n"
            '  python -c "import secrets; print(secrets.token_hex(32))"'
        )

    key = secrets.token_hex(32)
    path = Path(env_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n# Signs session cookies. Generated automatically on first run.\n"
            "# Keep it out of version control; changing it just logs everyone out.\n"
            f"COC_SECRET_KEY={key}\n"
        )
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - platform dependent
        pass
    print(f"[{TOOL_NAME}] generated a new COC_SECRET_KEY and wrote it to {path}")
    return key


def create_app(
    workspace_root: str | Path | None = None,
    *,
    secret_key: str | None = None,
    testing: bool = False,
) -> Flask:
    app = Flask(__name__, instance_relative_config=False)

    root = Path(
        workspace_root
        or os.environ.get("KEENEYE_WORKSPACE")
        or load_env_file().get("KEENEYE_WORKSPACE")
        or DEFAULT_WORKSPACE
    )

    app.config.update(
        SECRET_KEY=secret_key or (secrets.token_hex(32) if testing else resolve_secret_key()),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(int(os.environ.get("COC_COOKIE_SECURE", "0"))),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=int(
            os.environ.get("COC_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES)
        ),
        WORKSPACE_ROOT=str(root),
        TESTING=testing,
        CSRF_EXEMPT_ENDPOINTS=set(),
        JSON_SORT_KEYS=False,
    )

    workspace = Workspace.initialize(root)
    app.extensions["keeneye"] = workspace

    @app.before_request
    def _before_request():
        g.csp_nonce = secrets.token_urlsafe(16)
        return security.check_csrf()

    @app.after_request
    def _after_request(response):
        return security.apply_security_headers(response)

    @app.teardown_appcontext
    def _teardown(_exception):  # pragma: no cover - nothing to release per request
        return None

    from coc.web.api import api
    from coc.web.views import views

    app.register_blueprint(api)
    app.register_blueprint(views)

    # Registered on the application, not only the API blueprint, so the file
    # routes report a refused operation as a refusal rather than a 500.
    @app.errorhandler(ServiceError)
    def _refused(error):
        return {"error": "refused", "message": str(error)}, 400

    @app.errorhandler(StorageError)
    def _storage(error):
        return {"error": "storage", "message": str(error)}, 400

    @app.errorhandler(413)
    def _too_large(_error):
        limit = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        return (
            {
                "error": "too_large",
                "message": f"Upload exceeds the {limit} MiB limit. Use the CLI for larger images.",
            },
            413,
        )

    @app.errorhandler(404)
    def _not_found(error):
        from flask import request

        if request.path.startswith("/api/"):
            return {"error": "not_found", "message": "No such resource."}, 404
        return render_template("app.html", version=__version__, tool=TOOL_NAME), 200

    return app


def main() -> int:  # pragma: no cover - convenience entry point
    """``python -m coc.web`` — run the development server."""
    app = create_app()
    app.run(
        host=os.environ.get("COC_HOST", "127.0.0.1"),
        port=int(os.environ.get("COC_PORT", "5000")),
        debug=bool(int(os.environ.get("COC_DEBUG", "0"))),
    )
    return 0
