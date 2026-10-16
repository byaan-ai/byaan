from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.source_connections import SourceConnection
from server.models.source_resources import SourceResource
from server.schemas.source_connections import SourceConnectionCreate
from server.services.connector_catalog import (
    connector_catalog_summary,
    get_connector_definition,
    list_connector_definitions,
)
from server.services.crypto_service import CryptoService


class SourceConnectionService:
    def list_connector_definitions(self) -> dict[str, Any]:
        items = list_connector_definitions()
        return {"items": items, "total": len(items), "summary": connector_catalog_summary()}

    async def create_connection(
        self,
        *,
        session: AsyncSession,
        tenant_id: UUID,
        user_id: UUID | None,
        payload: SourceConnectionCreate,
    ) -> SourceConnection:
        definition = get_connector_definition(payload.provider)
        if definition is None or definition.availability != "beta":
            raise ValueError(f"Connector {payload.provider} is not enabled for this official beta")
        if payload.auth_mode != definition.auth_mode:
            raise ValueError(f"Connector {payload.provider} requires auth_mode={definition.auth_mode}")

        credentials = dict(payload.credentials or {})
        encrypted = await CryptoService.encrypt_config(credentials, session)
        capabilities = {
            "catalog_status": definition.availability,
            "runtime_status": "not_certified",
            "commercial_status": "PARTIAL",
            **dict(payload.capabilities or {}),
        }
        connection = SourceConnection(
            tenant_id=tenant_id,
            provider=payload.provider,
            auth_mode=payload.auth_mode,
            encrypted_credentials=encrypted,
            external_account_id=payload.external_account_id,
            display_name=payload.display_name,
            status="beta",
            capabilities_json=capabilities,
            token_expires_at=self._parse_expires_at(credentials.get("expires_at")),
            created_by=user_id,
        )
        session.add(connection)
        await session.commit()
        await session.refresh(connection)
        return connection

    async def list_connections(
        self,
        *,
        session: AsyncSession,
        tenant_id: UUID,
        provider: str | None = None,
        user_id: UUID | None = None,
        include_all: bool = True,
    ) -> list[SourceConnection]:
        stmt = select(SourceConnection).where(SourceConnection.tenant_id == tenant_id)
        if provider:
            stmt = stmt.where(SourceConnection.provider == provider)
        if not include_all:
            stmt = stmt.where(SourceConnection.created_by == user_id)
        result = await session.execute(stmt.order_by(SourceConnection.updated_at.desc()))
        return list(result.scalars().all())

    async def get_connection(
        self,
        *,
        session: AsyncSession,
        tenant_id: UUID,
        connection_id: str | UUID,
        user_id: UUID | None = None,
        include_all: bool = True,
    ) -> SourceConnection | None:
        stmt = select(SourceConnection).where(
            SourceConnection.tenant_id == tenant_id,
            SourceConnection.id == connection_id,
        )
        if not include_all:
            stmt = stmt.where(SourceConnection.created_by == user_id)
        return await session.scalar(stmt)

    async def delete_connection(
        self,
        *,
        session: AsyncSession,
        tenant_id: UUID,
        connection_id: str | UUID,
        user_id: UUID | None = None,
        include_all: bool = True,
    ) -> tuple[bool, int]:
        connection = await self.get_connection(
            session=session,
            tenant_id=tenant_id,
            connection_id=connection_id,
            user_id=user_id,
            include_all=include_all,
        )
        if connection is None:
            return False, 0
        resource_count = int(
            await session.scalar(
                select(func.count(SourceResource.id)).where(
                    SourceResource.tenant_id == tenant_id,
                    SourceResource.source_connection_id == connection.id,
                )
            )
            or 0
        )
        connection.status = "disconnected"
        connection.encrypted_credentials = await CryptoService.encrypt_config({"disconnected": True}, session)
        await session.commit()
        return True, resource_count

    def connection_payload(self, connection: SourceConnection | None) -> dict[str, Any] | None:
        if connection is None:
            return None
        return {
            "id": connection.id,
            "provider": connection.provider,
            "auth_mode": connection.auth_mode,
            "external_account_id": connection.external_account_id,
            "display_name": connection.display_name,
            "status": connection.status,
            "capabilities": connection.capabilities_json,
            "token_expires_at": connection.token_expires_at,
            "created_by": connection.created_by,
            "created_at": connection.created_at,
            "updated_at": connection.updated_at,
        }

    def _parse_expires_at(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str) and value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                return None
        return None
