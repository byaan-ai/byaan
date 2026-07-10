from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class SkillLoopRunNowRequest(BaseModel):
    notebook_id: UUID | None = None


class SkillLoopSettingsUpdate(BaseModel):
    enabled: bool | None = None
    digest_enabled: bool | None = None
    digest_hour: int | None = Field(default=None, ge=0, le=23)
    slack_reviewers_channel_id: str | None = None


class SkillLoopSettingsResponse(BaseModel):
    enabled: bool
    digest_enabled: bool
    digest_hour: int
    slack_reviewers_channel_id: str | None = None
    slack_workspace_connected: bool
    loop_globally_enabled: bool
