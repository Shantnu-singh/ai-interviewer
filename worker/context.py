"""Shared worker context (settings, DB, Redis) built lazily from the environment."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv
from redis import Redis

from app.config import Settings
from app.db import Database
from app.services.tasks import Tasks


@dataclass
class WorkerContext:
    settings: Settings
    database: Database
    redis: Redis
    tasks: Tasks


@lru_cache(maxsize=1)
def get_context() -> WorkerContext:
    load_dotenv()
    settings = Settings.from_env()
    redis = Redis.from_url(settings.redis_url)
    return WorkerContext(settings, Database(settings.database_url), redis, Tasks(redis))
