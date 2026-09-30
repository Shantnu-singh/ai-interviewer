"""Shared Flask extensions and per-request accessors."""

from __future__ import annotations

import hashlib
import hmac
import uuid

from flask import abort, current_app, g, request, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from redis import Redis
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Database
from app.services.tasks import Tasks

limiter = Limiter(key_func=get_remote_address)

_OWNED_KEY = "interviews"
_MAX_OWNED = 10


def settings() -> Settings:
    return current_app.extensions["settings"]


def db() -> Session:
    if "db_session" not in g:
        database: Database = current_app.extensions["database"]
        g.db_session = database.new_session()
    return g.db_session


def redis() -> Redis:
    return current_app.extensions["redis"]


def tasks() -> Tasks:
    return current_app.extensions["tasks"]


def client_ip_hash() -> str:
    ip = request.remote_addr or "unknown"
    return hmac.new(settings().secret_key.encode(), ip.encode(), hashlib.sha256).hexdigest()


# Ownership: the signed session cookie lists the interviews this browser created.
def remember_interview(interview_id: uuid.UUID) -> None:
    owned = [i for i in session.get(_OWNED_KEY, []) if i != str(interview_id)]
    owned.append(str(interview_id))
    session[_OWNED_KEY] = owned[-_MAX_OWNED:]
    session.permanent = True


def require_owner(interview_id: uuid.UUID) -> None:
    if str(interview_id) not in session.get(_OWNED_KEY, []):
        abort(404)
