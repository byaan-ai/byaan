from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

SourceResourceType = Literal[
    "file",
    "pdf",
    "web",
    "feishu_doc",
    "feishu_wiki",
    "feishu_sheet",
    "feishu_base",
    "tos_bucket",
    "tos_prefix",
    "tos_object",
    "database_catalog",
    "database_schema",
    "database_table",
    "databricks_catalog",
    "databricks_schema",
    "databricks_table",
]
SourceResourceStatus = Literal[
    "pending",
    "beta",
    "syncing",
    "understanding",
    "authorization_required",
    "reauthorization_required",
    "blocked",
    "source_unavailable",
    "permission_lost",
    "needs_confirmation",
    "ready",
    "failed",
]


class SourceResourceCreate(BaseModel):
    resource_type: SourceResourceType
    name: str
    external_id: str | None = None
    source_url: str | None = None
    parent_external_id: str | None = None
    source_connection_id: UUID | None = None
    connection_id: UUID | None = None
    visibility: str = "workspace"
    sync_mode: Literal["manual", "scheduled"] = "manual"
    selection_config: dict[str, Any] = Field(default_factory=dict)
    sync_config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    content: str | None = Field(
        default=None,
        description="Optional caller-supplied content used to record a governed snapshot. No external connector runtime is invoked.",
    )
    external_revision: str | None = None
    parser_version: str | None = None
    raw_storage_uri: str | None = None


class SourceResourceSyncRequest(BaseModel):
    content: str | None = None
    external_revision: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    parser_version: str | None = None
    raw_storage_uri: str | None = None


class SourceSnapshotRead(BaseModel):
    id: UUID
    resource_id: UUID
    external_revision: str | None = None
    content_hash: str
    raw_storage_uri: str
    captured_at: datetime
    parser_version: str | None = None
    metadata_json: dict[str, Any] | None = None
    status: str
    error_json: dict[str, Any] | None = None


class SourceResourceRead(BaseModel):
    id: UUID
    connection_id: UUID | None = None
    source_connection_id: UUID | None = None
    source_connection: dict[str, Any] | None = None
    resource_type: str
    name: str
    external_id: str | None = None
    source_url: str | None = None
    parent_external_id: str | None = None
    selection_config_json: dict[str, Any] | None = None
    visibility: str
    sync_mode: str
    sync_config_json: dict[str, Any] | None = None
    status: SourceResourceStatus
    latest_snapshot_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    latest_snapshot: SourceSnapshotRead | None = None
