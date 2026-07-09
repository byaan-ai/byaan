from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SkillScope(str, Enum):
    USER = "user"
    ORG = "org"


class CredentialFieldSchema(BaseModel):
    key: str
    label: str
    placeholder: str = ""
    help: str = ""
    optional: bool = False
    type: str = "text"
    options: list[dict[str, str]] = Field(default_factory=list)
    default: str = ""
    depends_on: dict[str, str] = Field(default_factory=dict)


class SkillCredentialCreate(BaseModel):
    credentials: dict = Field(..., description="API credentials (e.g., {'api_key': '...'})")
    scope: SkillScope = Field(default=SkillScope.USER, description="Scope: 'user' or 'org'")


class SkillCredentialResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    skill_name: str
    scope: str
    is_configured: bool = True
    created_at: datetime
    updated_at: datetime


class DomainToggleRequest(BaseModel):
    active: bool
    scope: SkillScope = SkillScope.USER


class SkillStatusResponse(BaseModel):
    skill_name: str
    display_name: str
    description: str
    is_configured: bool
    required_credentials: list[str]
    credential_fields: list[CredentialFieldSchema] = Field(default_factory=list)
    emoji: str = ""
    homepage: str = ""
    domain: str = ""
    scopes_configured: list[SkillScope] = Field(default_factory=list)
    user_scope_created_by: UUID | None = None
    org_scope_created_by: UUID | None = None
    org_scope_created_by_name: str | None = None
    domain_active: bool = True
