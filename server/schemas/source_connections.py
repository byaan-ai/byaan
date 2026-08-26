from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

SourceProvider = Literal["local_files", "web", "feishu", "sql_databases", "volcengine_tos", "databricks"]
SourceAuthMode = Literal["oauth", "access_key", "connection_string", "none"]


class ConnectorDefinitionRead(BaseModel):
    id: str
    provider: str
    category: str
    family: str
    display_name: str
    icon: str
    auth_mode: str
    capabilities: list[str]
    limitations: list[str] = Field(default_factory=list)
    required_scopes: list[str] = Field(default_factory=list)
    config_schema: dict[str, Any]
    resource_picker_schema: dict[str, Any]
    resource_picker_type: str = "none"
    supported_resource_types: list[str]
    availability: str
    status: str
    readiness_gates: list[dict[str, str]] = Field(default_factory=list)
    modeling_modes: list[str] = Field(default_factory=list)
    description: str = ""
    entry_kind: str = "connector_backed"


class SourceConnectionCreate(BaseModel):
    provider: SourceProvider
    auth_mode: SourceAuthMode
    display_name: str
    credentials: dict[str, Any] = Field(default_factory=dict)
    external_account_id: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)


class SourceConnectionRead(BaseModel):
    id: UUID
    provider: str
    auth_mode: str
    external_account_id: str | None = None
    display_name: str
    status: str
    capabilities: dict[str, Any]
    token_expires_at: datetime | None = None
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime
