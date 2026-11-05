from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    TIMESTAMP,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import GUID, Base

HANDOFF_STATUSES = {
    "proposed",
    "queued",
    "preparing",
    "running",
    "waiting_on_human",
    "pushing",
    "ci_running",
    "fixing",
    "done",
    "dismissed",
    "cancelled",
    "failed",
    "ci_failed",
    "taken_over",
}
WORKING_STATUSES = ("preparing", "running", "waiting_on_human", "pushing", "fixing")
ACTIVE_STATUSES = (*WORKING_STATUSES, "queued", "ci_running")
TERMINAL_STATUSES = ("done", "dismissed", "cancelled", "failed", "ci_failed", "taken_over")
HANDOFF_ORIGINS = {"slack", "notebook"}
EVENT_SOURCES = {"executor", "agent", "github", "human", "system"}
EVENT_KINDS = {"status", "tool", "progress", "question", "answer", "result", "ci", "review", "notify", "error"}

_WORKING_SQL = "status IN ('preparing', 'running', 'waiting_on_human', 'pushing', 'fixing')"


class Handoff(Base):
    __tablename__ = "handoffs"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    github_repository_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("github_repositories.id", ondelete="SET NULL"), nullable=True
    )
    repo_full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_by: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    origin: Mapped[str] = mapped_column(String(10), nullable=False)
    slack_conversation_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("slack_conversations.id", ondelete="SET NULL"), nullable=True
    )
    slack_card_ts: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notebook_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("notebooks.id", ondelete="SET NULL"), nullable=True
    )
    schedule_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True
    )
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    brief: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="proposed")

    base_branch: Mapped[str] = mapped_column(String(200), nullable=False)
    base_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    head_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pushed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    next_check_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    untested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    ci_fix_round: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))

    claude_session_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    workspace_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    __table_args__ = (
        Index("ix_handoffs_tenant_status", "tenant_id", "status"),
        Index("ix_handoffs_schedule_fingerprint", "schedule_id", "fingerprint"),
        Index("ix_handoffs_status_next_check", "status", "next_check_at"),
        Index(
            "ux_handoffs_one_active_per_repo",
            "github_repository_id",
            unique=True,
            sqlite_where=text(_WORKING_SQL),
            postgresql_where=text(_WORKING_SQL),
        ),
    )


class HandoffEvent(Base):
    __tablename__ = "handoff_events"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    handoff_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("handoffs.id", ondelete="CASCADE"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())

    __table_args__ = (UniqueConstraint("handoff_id", "seq", name="uq_handoff_events_handoff_seq"),)


class HandoffMessage(Base):
    __tablename__ = "handoff_messages"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    handoff_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("handoffs.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author_user_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    author_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    question_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())

    __table_args__ = (Index("ix_handoff_messages_pending", "handoff_id", "delivered_at"),)
