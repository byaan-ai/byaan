from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, model_validator

from server.constants.models import resolve_model


class NotebookCreate(BaseModel):
    notebook_name: str
    description: str | None = None


class NotebookUpdate(BaseModel):
    notebook_name: str | None = None
    description: str | None = None
    last_used_provider: str | None = None
    last_used_model: str | None = None


class NotebookRead(BaseModel):
    id: UUID
    notebook_name: str
    description: str | None = None
    last_used_provider: str | None = None
    last_used_model: str | None = None
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime
    source: str = "app"
    slack_thread_title: str | None = None

    model_config = {
        "from_attributes": True,
    }

    @model_validator(mode="after")
    def _upgrade_retired_model(self) -> NotebookRead:
        self.last_used_model = resolve_model(self.last_used_model, self.last_used_provider)
        return self


class NotebookListResponse(BaseModel):
    items: list[NotebookRead]
    total: int | None = None
