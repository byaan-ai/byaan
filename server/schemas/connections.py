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
    access_token: str
    http_path: str | None = None


class DatabricksCatalog(BaseModel):
    name: str
    schemas: list[str]


class DatabricksWarehouse(BaseModel):
    id: str
    name: str | None = None
    state: str | None = None
    size: str | None = None
    http_path: str


class DatabricksDiscoverResponse(BaseModel):
    catalogs: list[DatabricksCatalog]
    warehouses: list[DatabricksWarehouse] = []


class DatabricksOAuthStartRequest(BaseModel):
    server_hostname: str


class DatabricksOAuthStartResponse(BaseModel):
    auth_url: str
    state: str
    redirect_uri: str


class DatabricksOAuthCancelRequest(BaseModel):
    state: str


class DatabricksOAuthTokens(BaseModel):
    access_token: str
    refresh_token: str | None = None
    expires_at: int
    scope: str | None = None
    server_hostname: str


class DatabricksOAuthResultResponse(BaseModel):
    status: str
    tokens: DatabricksOAuthTokens | None = None


class DatabricksWarehousesRequest(BaseModel):
    server_hostname: str
    access_token: str


class DatabricksOAuthSettingsRequest(BaseModel):
    client_id: str
    client_secret: str


class DatabricksOAuthSettingsResponse(BaseModel):
    client_id: str
    client_secret_configured: bool
    redirect_uri: str
