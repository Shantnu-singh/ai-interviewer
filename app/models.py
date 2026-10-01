"""Database models and the status state machine."""

from __future__ import annotations

import enum
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from app.db import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class InterviewStatus(enum.StrEnum):
    PRE = "pre"
    IN_PROGRESS = "in_progress"
    EVALUATING = "evaluating"
    DONE = "done"
    FAILED = "failed"


class Role(enum.StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


def _enum(e: type[enum.Enum], name: str) -> Enum:
    # Stored as VARCHAR + CHECK so it works the same on Postgres and SQLite.
    return Enum(
        e,
        name=name,
        native_enum=False,
        length=16,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


class Interview(Base):
    __tablename__ = "interviews"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    github_username: Mapped[str] = mapped_column(String(39))
    # None = not fetched yet.
    github_metadata: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    status: Mapped[InterviewStatus] = mapped_column(
        _enum(InterviewStatus, "interview_status"), default=InterviewStatus.PRE, index=True
    )
    call_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    evaluation: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_ip_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    messages: Mapped[list[Message]] = relationship(
        back_populates="interview",
        order_by="Message.created_at",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("interview_id", "item_id", name="uq_messages_interview_item"),
        Index("ix_messages_interview_created", "interview_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    interview_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("interviews.id", ondelete="CASCADE")
    )
    role: Mapped[Role] = mapped_column(_enum(Role, "message_role"))
    content: Mapped[str] = mapped_column(Text)
    # Legacy dedup key from the old OpenAI Realtime sideband (conversation item id). Unused by
    # the Deepgram/OpenAI/Sarvam pipeline, which always writes NULL here; kept to avoid a
    # migration, since the unique constraint still allows multiple NULLs per interview.
    item_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    interview: Mapped[Interview] = relationship(back_populates="messages")


def transition(
    session: Session,
    interview_id: uuid.UUID,
    from_states: Iterable[InterviewStatus],
    to_state: InterviewStatus,
    **values: Any,
) -> bool:
    """Atomically move an interview between states. Returns False if another actor won."""
    result = session.execute(
        update(Interview)
        .where(Interview.id == interview_id, Interview.status.in_(list(from_states)))
        .values(status=to_state, **values)
        .execution_options(synchronize_session=False)
    )
    session.commit()
    return result.rowcount == 1
