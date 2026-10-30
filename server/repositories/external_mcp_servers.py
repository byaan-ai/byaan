from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import select

from server.models.external_mcp_server import ExternalMCPServer
from server.repositories.base import AsyncCRUDRepository
from server.services.crypto_service import CryptoService


class ExternalMCPServerRepository(AsyncCRUDRepository[ExternalMCPServer]):
    def __init__(self, session):
        super().__init__(session, ExternalMCPServer)
        self._session = session

    async def create(self, data: dict[str, Any]) -> ExternalMCPServer:  # type: ignore[override]
        cfg = data.pop("config_dict", None)
        if isinstance(cfg, dict):
            data["config"] = await CryptoService.encrypt_config(cfg, self._session)
        tool_filter = data.pop("tool_filter_list", None)
        if tool_filter is not None:
            data["tool_filter"] = json.dumps(tool_filter)
        return await super().create(data)

    async def update(self, pk: Any, data: dict[str, Any]) -> ExternalMCPServer | None:  # type: ignore[override]
        cfg = data.pop("config_dict", None)
        if isinstance(cfg, dict):
            data["config"] = await CryptoService.encrypt_config(cfg, self._session)
        if "tool_filter_list" in data:
            tool_filter = data.pop("tool_filter_list")
            data["tool_filter"] = json.dumps(tool_filter) if tool_filter is not None else None
        return await super().update(pk, data)

    async def get_by_name(self, name: str) -> ExternalMCPServer | None:
        query = select(ExternalMCPServer).where(ExternalMCPServer.name == name)
        query = self._apply_tenant_filter(query)
        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    async def list_enabled(self) -> list[ExternalMCPServer]:
        query = select(ExternalMCPServer).where(ExternalMCPServer.enabled.is_(True))
        query = self._apply_tenant_filter(query)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def decrypt_config(self, record: ExternalMCPServer) -> dict[str, Any]:
        return await CryptoService.decrypt_config(record.config, self._session)

    @staticmethod
    def parse_tool_filter(record: ExternalMCPServer) -> list[str] | None:
        if not record.tool_filter:
            return None
        try:
            parsed = json.loads(record.tool_filter)
            return parsed if isinstance(parsed, list) else None
        except (json.JSONDecodeError, TypeError):
            return None

    async def get_for_tenant(self, pk: UUID) -> ExternalMCPServer | None:
        return await self.get(pk)
