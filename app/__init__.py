"""Flask application factory."""

from __future__ import annotations

import logging
from datetime import timedelta

from flask import Flask, g, jsonify, request
from redis import Redis
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import Settings
from app.db import Database
from app.extensions import limiter
from app.services.tasks import Tasks

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'; object-src 'none'"
)


def create_app(
    settings: Settings | None = None,
    *,
    redis_client: Redis | None = None,
    tasks: Tasks | None = None,
) -> Flask:
    settings = settings or Settings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=settings.secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=settings.session_cookie_secure,
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
        MAX_CONTENT_LENGTH=64 * 1024,
        RATELIMIT_STORAGE_URI=settings.ratelimit_storage_uri,
        RATELIMIT_HEADERS_ENABLED=True,
    )
    if settings.trust_proxy:
        # Exactly one trusted proxy (nginx) in front of gunicorn.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[method-assign]

    redis_client = redis_client or Redis.from_url(settings.redis_url)
    app.extensions["settings"] = settings
    app.extensions["database"] = Database(settings.database_url)
    app.extensions["redis"] = redis_client
    app.extensions["tasks"] = tasks or Tasks(redis_client)
    limiter.init_app(app)

    from app.blueprints.api import bp as api_bp
    from app.blueprints.pages import bp as pages_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp)

    from app.cli import register_cli

    register_cli(app)

    @app.teardown_appcontext
    def _close_db(_exc: BaseException | None) -> None:
        s = g.pop("db_session", None)
        if s is not None:
            s.close()

    @app.after_request
    def _security_headers(resp):  # type: ignore[no-untyped-def]
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Permissions-Policy", "microphone=(self), camera=()")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        if request.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException):  # type: ignore[no-untyped-def]
        if request.path.startswith("/api/") or request.path == "/healthz":
            return jsonify(error=exc.description or exc.name), exc.code
        return exc

    return app
