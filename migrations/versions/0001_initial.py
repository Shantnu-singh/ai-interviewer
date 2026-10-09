"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-30
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
STATUSES = ("pre", "in_progress", "evaluating", "done", "failed")
ROLES = ("user", "assistant")


def upgrade() -> None:
    op.create_table(
        "interviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("github_username", sa.String(39), nullable=False),
        sa.Column("github_metadata", JSON, nullable=True),
        sa.Column(
            "status",
            sa.Enum(*STATUSES, name="interview_status", native_enum=False, length=16,
                    create_constraint=True),
            nullable=False,
        ),
        sa.Column("call_id", sa.String(128), nullable=True, unique=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("evaluation", JSON, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("client_ip_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_interviews_status", "interviews", ["status"])
    op.create_index("ix_interviews_created_at", "interviews", ["created_at"])

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "interview_id",
            sa.Uuid(),
            sa.ForeignKey("interviews.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.Enum(*ROLES, name="message_role", native_enum=False, length=16,
                    create_constraint=True),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("item_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("interview_id", "item_id", name="uq_messages_interview_item"),
    )
    op.create_index("ix_messages_interview_created", "messages", ["interview_id", "created_at"])


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("interviews")
