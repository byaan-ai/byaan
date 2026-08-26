"""Slack integration schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SlackConfigCreate(BaseModel):
    bot_token: str = Field(..., min_length=1, description="Slack Bot User OAuth Token")
    signing_secret: str = Field(..., min_length=1, description="Slack app signing secret")
    default_llm_connection_id: UUID | None = Field(None, description="Default LLM connection for Slack")
    default_model: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        description="Model string used for Slack responses (must belong to the connection's provider)",
    )


class SlackConfigUpdate(BaseModel):
    bot_token: str | None = Field(None, min_length=1, description="Slack Bot User OAuth Token")
    signing_secret: str | None = Field(None, min_length=1, description="Slack app signing secret")
    default_llm_connection_id: UUID | None = None
    default_model: str | None = Field(None, max_length=255)
    is_active: bool | None = None


class SlackConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slack_team_id: str
    slack_team_name: str | None
    is_active: bool
    default_llm_connection_id: UUID | None
    default_model: str | None
    created_at: datetime


class SlackEventPayload(BaseModel):
    """Slack Event API payload."""

    token: str | None = None
    team_id: str | None = None
    type: str
    challenge: str | None = None
    event: dict | None = None
    event_id: str | None = None
    event_time: int | None = None
