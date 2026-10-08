"""RQ jobs: GitHub fetch and transcript evaluation.

Run with: `rq worker default --url $REDIS_URL`
"""

from __future__ import annotations

import json
import logging
import time
import uuid

import httpx

from app.models import Interview, InterviewStatus, Role, transition, utcnow
from app.services.evaluator import EvaluationError, format_transcript, get_evaluator
from app.services.github import GitHubUserNotFound, fetch_profile
from worker.context import WorkerContext, get_context

log = logging.getLogger(__name__)

GITHUB_CACHE_TTL = 3600
MIN_USER_WORDS = 15


def fetch_github(interview_id: str, ctx: WorkerContext | None = None) -> None:
    ctx = ctx or get_context()
    iid = uuid.UUID(interview_id)
    with ctx.database.session() as s:
        interview = s.get(Interview, iid)
        if interview is None or interview.github_metadata is not None:
            return
        username = interview.github_username.lower()
        cache_key = f"github:profile:{username}"

        cached = ctx.redis.get(cache_key)
        if cached:
            interview.github_metadata = json.loads(cached)
            s.commit()
            return

        data = None
        for attempt in range(3):
            try:
                data = fetch_profile(username, ctx.settings.github_token)
                break
            except GitHubUserNotFound:
                transition(
                    s, iid, [InterviewStatus.PRE], InterviewStatus.FAILED,
                    error="GitHub user not found.",
                )
                return
            except httpx.HTTPError as exc:
                log.warning("github fetch %s attempt %d failed: %s", username, attempt + 1, exc)
                time.sleep(2 * (attempt + 1))
        if data is None:
            transition(
                s, iid, [InterviewStatus.PRE], InterviewStatus.FAILED,
                error="Could not load the GitHub profile. Please try again later.",
            )
            return

        ctx.redis.set(cache_key, json.dumps(data), ex=GITHUB_CACHE_TTL)
        interview.github_metadata = data
        s.commit()


def evaluate(interview_id: str, ctx: WorkerContext | None = None) -> None:
    ctx = ctx or get_context()
    iid = uuid.UUID(interview_id)
    with ctx.database.session() as s:
        interview = s.get(Interview, iid)
        if interview is None or interview.status != InterviewStatus.EVALUATING:
            return  # already evaluated, or not ready

        messages = list(interview.messages)
        user_words = sum(len(m.content.split()) for m in messages if m.role == Role.USER)
        if user_words < MIN_USER_WORDS:
            transition(
                s, iid, [InterviewStatus.EVALUATING], InterviewStatus.DONE,
                score=0,
                feedback="Not enough of your answers were captured to evaluate this interview. "
                "Check your microphone and try again.",
                evaluation={"strengths": [], "improvements": []},
            )
            return

        transcript = format_transcript(messages)
        evaluator = get_evaluator(ctx.settings)
        result = None
        for attempt in range(2):
            try:
                result = evaluator.evaluate(transcript, interview.github_metadata)
                break
            except EvaluationError as exc:
                log.warning("evaluation %s attempt %d failed: %s", iid, attempt + 1, exc)
        if result is None:
            transition(
                s, iid, [InterviewStatus.EVALUATING], InterviewStatus.FAILED,
                error="We couldn't evaluate this interview. Please try again later.",
            )
            return

        transition(
            s, iid, [InterviewStatus.EVALUATING], InterviewStatus.DONE,
            score=result.score,
            feedback=result.feedback,
            evaluation={"strengths": result.strengths, "improvements": result.improvements},
            ended_at=interview.ended_at or utcnow(),
        )
