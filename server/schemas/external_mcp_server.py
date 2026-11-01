from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

TRANSPORTS = ("stdio", "sse", "streamable_http")
RESERVED_NAMES = {"byaan", "byaan_tools"}
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$")
SECRET_HEADER_KEYS = {"authorization", "x-api-key", "api-key", "token"}


def _mask_headers(headers: dict[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    masked: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in SECRET_HEADER_KEYS or "secret" in key.lower() or "token" in key.lower():
            masked[key] = "***"
        else:
            masked[key] = value
    return masked


def _mask_env(env: dict[str, str] | None) -> dict[str, str]:
    if not env:
        return {}
    return {
        k: ("***" if any(s in k.upper() for s in ("TOKEN", "KEY", "SECRET", "PASSWORD")) else v) for k, v in env.items()
    }


class StdioConfig(BaseModel):
    command: str = Field(..., min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class HttpConfig(BaseModel):
    url: str = Field(..., min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, ge=1, le=300)


class ExternalMCPServerBase(BaseModel):
    name: str = Field(..., min_length=3, max_length=64)
    transport: Literal["stdio", "sse", "streamable_http"]
    config: dict[str, Any]
    enabled: bool = True
    tool_filter: list[str] | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v_lower = v.lower()
        if v_lower in RESERVED_NAMES:
            raise ValueError(f"'{v}' is a reserved name")
        if not NAME_PATTERN.match(v_lower):
            raise ValueError("name must be lowercase alphanumeric with hyphens/underscores, 3-64 chars")
        return v_lower

    @model_validator(mode="after")
    def validate_config(self) -> ExternalMCPServerBase:
        if self.transport == "stdio":
            StdioConfig.model_validate(self.config)
        else:
            HttpConfig.model_validate(self.config)
        return self


class ExternalMCPServerCreate(ExternalMCPServerBase):
    pass


class ExternalMCPServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=64)
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    tool_filter: list[str] | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v_lower = v.lower()
        if v_lower in RESERVED_NAMES:
            raise ValueError(f"'{v}' is a reserved name")
        if not NAME_PATTERN.match(v_lower):
            raise ValueError("name must be lowercase alphanumeric with hyphens/underscores, 3-64 chars")
        return v_lower


class ExternalMCPServerResponse(BaseModel):
    id: UUID
    name: str
    transport: str
    config: dict[str, Any]
    enabled: bool
    tool_filter: list[str] | None = None
    decrypt_error: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_model(
        cls,
        model_id: UUID,
        name: str,
        transport: str,
        decrypted_config: dict[str, Any],
        enabled: bool,
        tool_filter: list[str] | None,
        created_at: datetime,
        updated_at: datetime,
    ) -> ExternalMCPServerResponse:
        masked = dict(decrypted_config)
        if transport == "stdio":
            masked["env"] = _mask_env(masked.get("env"))
        else:
            masked["headers"] = _mask_headers(masked.get("headers"))
        return cls(
            id=model_id,
            name=name,
            transport=transport,
            config=masked,
            enabled=enabled,
            tool_filter=tool_filter,
            created_at=created_at,
            updated_at=updated_at,
        )


class ExternalMCPServerListResponse(BaseModel):
    items: list[ExternalMCPServerResponse]
    total: int


class TestConnectionResponse(BaseModel):
    success: bool
    tools: list[str] = Field(default_factory=list)
    error: str | None = None


class TestConfigRequest(BaseModel):
    transport: Literal["stdio", "sse", "streamable_http"]
    config: dict[str, Any]
