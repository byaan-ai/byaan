from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class ConnectionCreate(BaseModel):
    type: str
    name: str | None = None
    connection_obj: dict[str, Any]
    is_public: bool | None = None


class ConnectionRead(BaseModel):
    id: UUID
    type: str
    name: str | None = None
    created_at: datetime
    schema_updated_at: datetime | None = None
    connection_obj: dict[str, Any] | None = None  # Safe display fields only

    model_config = {
        "from_attributes": True,
    }


class ConnectionUpdateResponse(BaseModel):
    id: UUID
    type: str
    name: str | None = None
    created_at: datetime
    schema_updated_at: datetime | None = None
    connection_obj: dict[str, Any] | None = None
    database_schema: dict[str, Any] | None = None

    model_config = {
        "from_attributes": True,
    }


class ConnectionListResponse(BaseModel):
    items: list[ConnectionRead]
    total: int | None = None


class ConnectionListItem(BaseModel):
    id: UUID
    name: str
    host: str
    type: str
    created_at: datetime


class ConnectionListSimpleResponse(BaseModel):
    items: list[ConnectionListItem]
    total: int


class DatabricksDiscoverRequest(BaseModel):
    server_hostname: str
    http_path: str
    access_token: str


class DatabricksCatalog(BaseModel):
    name: str
    schemas: list[str]


class DatabricksDiscoverResponse(BaseModel):
    catalogs: list[DatabricksCatalog]
