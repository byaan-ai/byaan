from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class SemanticModelCreate(BaseModel):
    slug: str | None = None
    name: str
    domain: str = ""
    owner: str = ""
    description: str = ""
    datasource_id: str | None = None
    datasource_name: str | None = None
    datasource_kind: str = "source"
    manifest: dict[str, Any] = Field(default_factory=dict)
    source_resource_ids: list[UUID] = Field(default_factory=list)
    source_snapshot_ids: list[UUID] = Field(default_factory=list)
    consumer_summary: dict[str, Any] = Field(default_factory=dict)


class SemanticModelPatch(BaseModel):
    expected_revision: int | None = None
    expectedRevision: int | None = None
    name: str | None = None
    domain: str | None = None
    owner: str | None = None
    description: str | None = None
    datasource_id: str | None = None
    datasource_name: str | None = None
    datasource_kind: str | None = None
    manifest: dict[str, Any] | None = None
    source_resource_ids: list[UUID] | None = None
    source_snapshot_ids: list[UUID] | None = None
    consumer_summary: dict[str, Any] | None = None

    def revision_guard(self) -> int | None:
        return self.expected_revision if self.expected_revision is not None else self.expectedRevision
