from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SkillSuggestionApproveRequest(BaseModel):
    final_instructions: str | None = None


class SkillSuggestionRejectRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class SkillSuggestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    skill_id: UUID | None = None
    skill_name: str | None = None
    suggestion_type: str
    title: str
    rationale: str
    evidence: dict | None = None
    patch: dict | None = None
    proposed_instructions: str | None = None
    confidence: str
    status: str
    source: dict | None = None
    reviewed_by: UUID | None = None
    reviewed_via: str | None = None
    reviewer_slack_user_id: str | None = None
    reviewer_display_name: str | None = None
    review_note: str | None = None
    reviewed_at: datetime | None = None
    slack_channel_id: str | None = None
    slack_message_ts: str | None = None
    created_at: datetime
    updated_at: datetime


class SkillVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    changed_by: str
    suggestion_id: UUID | None = None
    created_at: datetime
