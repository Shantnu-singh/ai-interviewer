"""JSON API used by the browser."""

from __future__ import annotations

import logging
import uuid

from flask import Blueprint, abort, jsonify, request
from pydantic import ValidationError

from app import extensions as ext
from app.models import Interview, InterviewStatus, transition, utcnow
from app.schemas import CreateInterviewBody

log = logging.getLogger(__name__)
bp = Blueprint("api", __name__, url_prefix="/api/v1")


def _json_body() -> dict:
    # Requiring application/json also forces a CORS preflight for cross-site requests,
    # which (with no CORS headers served) blocks cross-site POSTs using our cookie.
    if not request.is_json:
        abort(415, "Content-Type must be application/json")
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        abort(400, "Body must be a JSON object")
    return body


def _load(interview_id: uuid.UUID) -> Interview:
    ext.require_owner(interview_id)
    interview = ext.db().get(Interview, interview_id)
    if interview is None:
        abort(404)
    return interview


@bp.post("/interviews")
@ext.limiter.limit(lambda: f"{ext.settings().max_interviews_per_ip_per_day}/day")
def create_interview():  # type: ignore[no-untyped-def]
    try:
        body = CreateInterviewBody.model_validate(_json_body())
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "Invalid input").removeprefix("Value error, ")
        return jsonify(error=message), 422

    if not ext.tasks().reserve_daily_slot(ext.settings().max_interviews_per_day):
        return jsonify(error="Daily interview capacity reached. Please try again tomorrow."), 503

    s = ext.db()
    interview = Interview(github_username=body.github, client_ip_hash=ext.client_ip_hash())
    s.add(interview)
    s.commit()

    ext.tasks().fetch_github(interview.id)
    ext.remember_interview(interview.id)
    return jsonify(id=str(interview.id), status=interview.status.value), 201


@bp.get("/interviews/<uuid:interview_id>")
def get_interview(interview_id: uuid.UUID):  # type: ignore[no-untyped-def]
    interview = _load(interview_id)
    return jsonify(
        id=str(interview.id),
        status=interview.status.value,
        ready=interview.status == InterviewStatus.PRE and interview.github_metadata is not None,
        github_username=interview.github_username,
        error=interview.error,
        max_call_seconds=ext.settings().max_call_seconds,
    )


@bp.post("/interviews/<uuid:interview_id>/session")
@ext.limiter.limit("5/minute")
def create_session(interview_id: uuid.UUID):  # type: ignore[no-untyped-def]
    """Return the WebSocket URL for the voice session.

    The browser connects to this URL directly; the voice session worker (not Flask) claims
    the interview (PRE -> IN_PROGRESS) at that point, to avoid a race between two separate
    processes both trying to start the same call.
    """
    interview = _load(interview_id)
    if interview.status != InterviewStatus.PRE or interview.github_metadata is None:
        abort(409, "Interview is not ready to start or has already started")

    ws_path = f"/voice/{interview.id}"
    public_base = ext.settings().voice_ws_public_url
    ws_url = f"{public_base}{ws_path}" if public_base else None
    return jsonify(ws_path=ws_path, ws_url=ws_url), 201


@bp.post("/interviews/<uuid:interview_id>/end")
def end_interview(interview_id: uuid.UUID):  # type: ignore[no-untyped-def]
    _json_body()
    interview = _load(interview_id)
    s = ext.db()
    if interview.status == InterviewStatus.IN_PROGRESS:
        # The voice session worker closes the call, then moves the interview to `evaluating`.
        ext.tasks().signal_end(interview.id)
    elif interview.status == InterviewStatus.PRE:
        transition(
            s,
            interview.id,
            [InterviewStatus.PRE],
            InterviewStatus.FAILED,
            error="Interview was ended before it started.",
            ended_at=utcnow(),
        )
    s.refresh(interview)
    return jsonify(status=interview.status.value), 202


@bp.get("/interviews/<uuid:interview_id>/result")
def get_result(interview_id: uuid.UUID):  # type: ignore[no-untyped-def]
    interview = _load(interview_id)
    evaluation = interview.evaluation or {}
    return jsonify(
        status=interview.status.value,
        score=interview.score,
        feedback=interview.feedback,
        strengths=evaluation.get("strengths", []),
        improvements=evaluation.get("improvements", []),
        error=interview.error,
        transcript=[
            {"role": m.role.value, "content": m.content, "created_at": m.created_at.isoformat()}
            for m in interview.messages
        ],
    )
