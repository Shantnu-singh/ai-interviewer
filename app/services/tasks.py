"""Background task dispatch (RQ for short jobs) plus the voice-session end signal.

The browser connects directly to the voice session WebSocket worker (see
`worker/voice_session.py`), so there is no "start" queue here -- only the end-of-call signal
and short-lived jobs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from redis import Redis
from rq import Queue

JOB_QUEUE = "default"


def end_key(interview_id: uuid.UUID | str) -> str:
    return f"voice:end:{interview_id}"


def daily_slot_key() -> str:
    return f"interviews:day:{datetime.now(UTC):%Y-%m-%d}"


class Tasks:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self.queue = Queue(JOB_QUEUE, connection=redis)

    def fetch_github(self, interview_id: uuid.UUID) -> None:
        self.queue.enqueue("worker.jobs.fetch_github", str(interview_id), job_timeout=60)

    def evaluate(self, interview_id: uuid.UUID) -> None:
        self.queue.enqueue("worker.jobs.evaluate", str(interview_id), job_timeout=180)

    def signal_end(self, interview_id: uuid.UUID) -> None:
        self.redis.set(end_key(interview_id), "1", ex=3600)

    def reserve_daily_slot(self, cap: int) -> bool:
        """Global per-day interview cap (cost guard)."""
        key = daily_slot_key()
        n = self.redis.incr(key)
        if n == 1:
            self.redis.expire(key, 2 * 86400)
        return n <= cap
