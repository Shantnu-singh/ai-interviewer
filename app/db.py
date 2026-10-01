"""SQLAlchemy engine/session management usable from Flask and from workers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str) -> None:
        if url.startswith("sqlite"):
            # Used by tests: one shared in-memory connection across threads.
            self.engine = create_engine(
                url, connect_args={"check_same_thread": False}, poolclass=StaticPool
            )
        else:
            self.engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
        self._factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def new_session(self) -> Session:
        return self._factory()

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._factory()
        try:
            yield s
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()
