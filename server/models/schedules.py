from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, TIMESTAMP, Boolean, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import GUID, Base

if TYPE_CHECKING:
    from server.models.llm_connections import LLMConnection
    from server.models.notebooks import Notebook
    from server.models.user import User


def generate_uuid() -> UUID:
    return uuid4()


MONITOR_STATUSES = {"draft", "active", "paused", "auto_paused"}
NOTIFY_POLICIES = {"always", "on_alert", "on_change"}
ORIGINS = {"ui", "notebook_chat", "slack"}
RUN_TRIGGERS = {"scheduled", "manual", "catchup"}
RUN_OUTCOMES = {"ok", "alert", "no_change"}


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    notebook_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("notebooks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(100), nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    slack_channel_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    slack_thread_ts: Mapped[str | None] = mapped_column(String(50), nullable=True)
    slack_workspace_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("slack_workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    broadcast_to_channel: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False, default="ui")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    notify_policy: Mapped[str] = mapped_column(String(20), nullable=False, default="always")
    paused_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    running_since: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    llm_connection_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("llm_connections.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    instruction: Mapped[str | None] = mapped_column(Text, nullable=True)

    next_run_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True, index=True)
    is_running: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    manual_trigger_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    manual_slack_target: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    notebook: Mapped[Notebook] = relationship("Notebook", foreign_keys=[notebook_id])
    creator: Mapped[User | None] = relationship("User", foreign_keys=[created_by])
    llm_connection: Mapped[LLMConnection | None] = relationship("LLMConnection", foreign_keys=[llm_connection_id])
    runs: Mapped[list[ScheduleRun]] = relationship(back_populates="schedule", cascade="all, delete-orphan")


class ScheduleRun(Base):
    __tablename__ = "schedule_runs"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=generate_uuid)
    schedule_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False, index=True
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    queries_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queries_succeeded: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queries_failed: Mapped[int | None] = mapped_column(Integer, nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)

    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    headline: Mapped[str | None] = mapped_column(String(500), nullable=True)
    findings: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    deliveries: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")

    slack_channel_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    slack_message_ts: Mapped[str | None] = mapped_column(String(50), nullable=True)

    schedule: Mapped[Schedule] = relationship("Schedule", back_populates="runs")
