from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ScheduleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    cron_expression: str = Field(..., min_length=1, max_length=100)
    timezone: str = Field(default="UTC", max_length=50)
    is_enabled: bool = True
    webhook_url: str | None = None
    slack_channel_id: str | None = None
    slack_thread_ts: str | None = Field(None, max_length=50)
    broadcast_to_channel: bool = False
    origin: str = Field(default="ui", max_length=20)
    status: str = Field(default="active", max_length=20)
    notify_policy: str = Field(default="always", max_length=20)
    llm_connection_id: UUID | None = None
    model: str | None = Field(None, max_length=255)
    instruction: str | None = None


class ScheduleUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    cron_expression: str | None = Field(None, min_length=1, max_length=100)
    timezone: str | None = Field(None, max_length=50)
    is_enabled: bool | None = None
    webhook_url: str | None = None
    slack_channel_id: str | None = None
    slack_thread_ts: str | None = Field(None, max_length=50)
    broadcast_to_channel: bool | None = None
    status: str | None = Field(None, max_length=20)
    notify_policy: str | None = Field(None, max_length=20)
    paused_until: datetime | None = None
    llm_connection_id: UUID | None = None
    model: str | None = Field(None, max_length=255)
    instruction: str | None = None


class ScheduleRead(BaseModel):
    id: UUID
    notebook_id: UUID
    notebook_name: str | None = None
    name: str
    cron_expression: str
    timezone: str
    is_enabled: bool
    webhook_url: str | None = None
    slack_channel_id: str | None = None
    slack_thread_ts: str | None = None
    broadcast_to_channel: bool = False
    origin: str = "ui"
    status: str = "active"
    notify_policy: str = "always"
    paused_until: datetime | None = None
    consecutive_failures: int = 0
    llm_connection_id: UUID | None = None
    model: str | None = None
    instruction: str | None = None
    next_run_at: datetime | None = None
    is_running: bool
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ScheduleRunRead(BaseModel):
    id: UUID
    schedule_id: UUID
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None
    queries_total: int | None = None
    queries_succeeded: int | None = None
    queries_failed: int | None = None
    error_message: str | None = None
    message_id: UUID | None = None
    outcome: str | None = None
    headline: str | None = None
    findings: list[dict[str, Any]] | None = None
    deliveries: list[dict[str, Any]] | None = None
    trigger: str = "scheduled"

    model_config = {"from_attributes": True}


class ScheduleTestResult(BaseModel):
    success: bool
    summary: str | None = None
    error: str | None = None
    queries_total: int | None = None
    queries_succeeded: int | None = None
    queries_failed: int | None = None
