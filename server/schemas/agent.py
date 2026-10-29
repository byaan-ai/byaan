from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class AgentRequest(BaseModel):
    message: str
    attachments: list[dict[str, str]] | None = None
    notebook_id: str | UUID | None = None
    llm_connection_id: str | UUID | None = None
    model: str | None = None
    db_type: str | None = None
    current_version: int | None = None
    datasource_ids: list[str | UUID] | None = None
    create_notebook: bool = False
    is_preview: bool = False
    plan_mode: bool = False
    scheduled_run: bool = False
    scheduled_run_schedule_id: str | UUID | None = None
    browser_timezone: str | None = None
