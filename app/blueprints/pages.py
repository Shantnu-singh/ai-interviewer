"""HTML pages and health check."""

from __future__ import annotations

import uuid

from flask import Blueprint, abort, jsonify, render_template
from sqlalchemy import text

from app import extensions as ext
from app.models import Interview

bp = Blueprint("pages", __name__)


@bp.get("/")
def index():  # type: ignore[no-untyped-def]
    return render_template("form.html")


@bp.get("/interview/<uuid:interview_id>")
def interview(interview_id: uuid.UUID):  # type: ignore[no-untyped-def]
    ext.require_owner(interview_id)
    if ext.db().get(Interview, interview_id) is None:
        abort(404)
    return render_template("interview.html", interview_id=interview_id)


@bp.get("/result/<uuid:interview_id>")
def result(interview_id: uuid.UUID):  # type: ignore[no-untyped-def]
    ext.require_owner(interview_id)
    if ext.db().get(Interview, interview_id) is None:
        abort(404)
    return render_template("result.html", interview_id=interview_id)


@bp.get("/healthz")
@ext.limiter.exempt
def healthz():  # type: ignore[no-untyped-def]
    checks = {"db": False, "redis": False}
    try:
        ext.db().execute(text("SELECT 1"))
        checks["db"] = True
    except Exception:  # noqa: BLE001, S110 - health check reports, never raises
        pass
    try:
        checks["redis"] = bool(ext.redis().ping())
    except Exception:  # noqa: BLE001, S110
        pass
    ok = all(checks.values())
    return jsonify(status="ok" if ok else "degraded", **checks), 200 if ok else 503
