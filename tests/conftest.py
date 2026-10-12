from __future__ import annotations

import uuid
from dataclasses import replace

import fakeredis
import pytest

from app import create_app
from app.config import Settings
from app.db import Base
from app.services.tasks import Tasks
from worker.context import WorkerContext

TEST_SETTINGS = Settings(
    secret_key="test-secret-key-that-is-long-enough-123",
    database_url="sqlite://",
    redis_url="redis://unused",
    openai_api_key="sk-test",
    deepgram_api_key="dg-test",
    sarvam_api_key="sarvam-test",
    session_cookie_secure=False,
    trust_proxy=False,
    ratelimit_storage_uri="memory://",
    max_interviews_per_ip_per_day=3,
    max_interviews_per_day=100,
)


class FakeTasks(Tasks):
    """Real Redis-backed counters, but records job dispatch instead of enqueueing."""

    def __init__(self, redis):  # type: ignore[no-untyped-def]
        super().__init__(redis)
        self.calls: list[tuple] = []

    def fetch_github(self, interview_id: uuid.UUID) -> None:
        self.calls.append(("fetch_github", interview_id))

    def evaluate(self, interview_id: uuid.UUID) -> None:
        self.calls.append(("evaluate", interview_id))

    def signal_end(self, interview_id: uuid.UUID) -> None:
        super().signal_end(interview_id)
        self.calls.append(("signal_end", interview_id))


@pytest.fixture
def settings() -> Settings:
    return TEST_SETTINGS


@pytest.fixture
def redis_client():  # type: ignore[no-untyped-def]
    return fakeredis.FakeRedis()


@pytest.fixture
def fake_tasks(redis_client) -> FakeTasks:  # type: ignore[no-untyped-def]
    return FakeTasks(redis_client)


@pytest.fixture
def app(settings, redis_client, fake_tasks):  # type: ignore[no-untyped-def]
    app = create_app(settings, redis_client=redis_client, tasks=fake_tasks)
    app.config["TESTING"] = True
    Base.metadata.create_all(app.extensions["database"].engine)
    return app


@pytest.fixture
def client(app):  # type: ignore[no-untyped-def]
    return app.test_client()


@pytest.fixture
def database(app):  # type: ignore[no-untyped-def]
    return app.extensions["database"]


@pytest.fixture
def worker_ctx(settings, database, redis_client, fake_tasks) -> WorkerContext:  # type: ignore[no-untyped-def]
    return WorkerContext(settings, database, redis_client, fake_tasks)


@pytest.fixture
def make_settings():  # type: ignore[no-untyped-def]
    return lambda **kw: replace(TEST_SETTINGS, **kw)
