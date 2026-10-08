from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from server.models.connections import Connection
from server.models.source_connections import SourceConnection
from server.models.source_resources import SourceResource
from server.models.source_snapshots import SourceSnapshot
from server.schemas.source_resources import SourceResourceCreate, SourceResourceSyncRequest


class SourceResourceService:
    async def create_resource(
        self,
        *,
        session: AsyncSession,
        tenant_id: UUID,
        user_id: UUID | None,
        payload: SourceResourceCreate,
        include_all_connections: bool = True,
    ) -> dict[str, Any]:
        await self._validate_source_connection(
            session=session,
            tenant_id=tenant_id,
            user_id=user_id,
            source_connection_id=payload.source_connection_id,
            include_all=include_all_connections,
        )
        await self._validate_connection(
            session=session,
            tenant_id=tenant_id,
            user_id=user_id,
            connection_id=payload.connection_id,
            include_all=include_all_connections,
        )
        resource = SourceResource(
            tenant_id=tenant_id,
            connection_id=payload.connection_id,
            source_connection_id=payload.source_connection_id,
            resource_type=payload.resource_type,
            name=payload.name,
            external_id=payload.external_id,
            source_url=payload.source_url,
            parent_external_id=payload.parent_external_id,
            selection_config_json=payload.selection_config,
            owner_id=user_id,
            visibility=payload.visibility,
            sync_mode=payload.sync_mode,
            sync_config_json={
                "commercial_status": "PARTIAL",
                "runtime_status": "not_certified",
                **payload.sync_config,
            },
            status="beta",
        )
        session.add(resource)
        await session.flush()

        if payload.content and payload.content.strip():
            snapshot = self._build_snapshot(
                resource=resource,
                content=payload.content,
                external_revision=payload.external_revision,
                raw_storage_uri=payload.raw_storage_uri,
                parser_version=payload.parser_version,
                metadata=payload.metadata,
            )
            session.add(snapshot)
            await session.flush()
            resource.latest_snapshot_id = snapshot.id
            resource.status = "ready"

        await session.commit()
        await session.refresh(resource)
        return await self.resource_payload(session=session, resource=resource)

    async def list_resources(self, *, session: AsyncSession, tenant_id: UUID) -> list[dict[str, Any]]:
        result = await session.execute(
            select(SourceResource)
            .options(selectinload(SourceResource.source_connection), selectinload(SourceResource.snapshots))
            .where(SourceResource.tenant_id == tenant_id)
            .order_by(SourceResource.updated_at.desc())
        )
        return [await self.resource_payload(session=session, resource=resource) for resource in result.scalars().all()]

    async def get_resource(
        self,
        *,
        session: AsyncSession,
        tenant_id: UUID,
        resource_id: str | UUID,
    ) -> SourceResource | None:
        return await session.scalar(
            select(SourceResource)
            .options(selectinload(SourceResource.source_connection), selectinload(SourceResource.snapshots))
            .where(SourceResource.tenant_id == tenant_id, SourceResource.id == resource_id)
        )

    async def sync_resource(
        self,
        *,
        session: AsyncSession,
        tenant_id: UUID,
        resource_id: str | UUID,
        payload: SourceResourceSyncRequest,
        user_id: UUID | None = None,
        include_all: bool = True,
    ) -> dict[str, Any]:
        resource = await self.get_resource(session=session, tenant_id=tenant_id, resource_id=resource_id)
        if resource is None:
            raise ValueError("Source resource not found")
        if not include_all and resource.owner_id != user_id:
            raise PermissionError("You can only update source resources you created")
        if not payload.content or not payload.content.strip():
            resource.status = "needs_confirmation"
            await session.commit()
            await session.refresh(resource)
            return await self.resource_payload(session=session, resource=resource)

        snapshot = self._build_snapshot(
            resource=resource,
            content=payload.content,
            external_revision=payload.external_revision,
            raw_storage_uri=payload.raw_storage_uri,
            parser_version=payload.parser_version,
            metadata=payload.metadata,
        )
        session.add(snapshot)
        await session.flush()
        resource.latest_snapshot_id = snapshot.id
        resource.status = "ready"
        await session.commit()
        await session.refresh(resource)
        return await self.resource_payload(session=session, resource=resource)

    async def list_snapshots(
        self,
        *,
        session: AsyncSession,
        tenant_id: UUID,
        resource_id: str | UUID,
    ) -> dict[str, Any]:
        resource = await self.get_resource(session=session, tenant_id=tenant_id, resource_id=resource_id)
        if resource is None:
            raise ValueError("Source resource not found")
        result = await session.execute(
            select(SourceSnapshot)
            .where(SourceSnapshot.tenant_id == tenant_id, SourceSnapshot.resource_id == resource.id)
            .order_by(SourceSnapshot.captured_at.desc())
        )
        items = [self._snapshot_payload(snapshot) for snapshot in result.scalars().all()]
        return {"items": items, "total": len(items)}

    async def delete_resource(
        self,
        *,
        session: AsyncSession,
        tenant_id: UUID,
        resource_id: str | UUID,
    ) -> bool:
        resource = await self.get_resource(session=session, tenant_id=tenant_id, resource_id=resource_id)
        if resource is None:
            return False
        await session.delete(resource)
        await session.commit()
        return True

    async def resource_payload(self, *, session: AsyncSession, resource: SourceResource) -> dict[str, Any]:
        latest_snapshot = await self._latest_snapshot(session=session, resource=resource)
        source_connection = await self._source_connection(session=session, resource=resource)
        return {
            "id": resource.id,
            "connection_id": resource.connection_id,
            "source_connection_id": resource.source_connection_id,
            "source_connection": self._source_connection_payload(source_connection),
            "resource_type": resource.resource_type,
            "name": resource.name,
            "external_id": resource.external_id,
            "source_url": resource.source_url,
            "parent_external_id": resource.parent_external_id,
            "selection_config_json": resource.selection_config_json,
            "visibility": resource.visibility,
            "sync_mode": resource.sync_mode,
            "sync_config_json": resource.sync_config_json,
            "status": resource.status,
            "latest_snapshot_id": resource.latest_snapshot_id,
            "created_at": resource.created_at,
            "updated_at": resource.updated_at,
            "latest_snapshot": self._snapshot_payload(latest_snapshot),
        }

    async def _validate_source_connection(
        self,
        *,
        session: AsyncSession,
        tenant_id: UUID,
        user_id: UUID | None,
        source_connection_id: UUID | None,
        include_all: bool,
    ) -> None:
        if source_connection_id is None:
            return
        stmt = select(SourceConnection).where(
            SourceConnection.tenant_id == tenant_id,
            SourceConnection.id == source_connection_id,
            SourceConnection.status != "disconnected",
        )
        if not include_all:
            stmt = stmt.where(SourceConnection.created_by == user_id)
        connection = await session.scalar(stmt)
        if connection is None:
            raise ValueError("Source connection not found")

    async def _validate_connection(
        self,
        *,
        session: AsyncSession,
        tenant_id: UUID,
        user_id: UUID | None,
        connection_id: UUID | None,
        include_all: bool,
    ) -> None:
        if connection_id is None:
            return
        stmt = select(Connection).where(Connection.tenant_id == tenant_id, Connection.id == connection_id)
        if not include_all:
            stmt = stmt.where(Connection.created_by == user_id)
        connection = await session.scalar(stmt)
        if connection is None:
            raise ValueError("Connection not found")

    async def _latest_snapshot(self, *, session: AsyncSession, resource: SourceResource) -> SourceSnapshot | None:
        if resource.latest_snapshot_id:
            snapshot = await session.get(SourceSnapshot, resource.latest_snapshot_id)
            if snapshot is not None and snapshot.tenant_id == resource.tenant_id:
                return snapshot
        return await session.scalar(
            select(SourceSnapshot)
            .where(SourceSnapshot.tenant_id == resource.tenant_id, SourceSnapshot.resource_id == resource.id)
            .order_by(SourceSnapshot.captured_at.desc())
            .limit(1)
        )

    async def _source_connection(
        self,
        *,
        session: AsyncSession,
        resource: SourceResource,
    ) -> SourceConnection | None:
        if resource.source_connection_id is None:
            return None
        return await session.scalar(
            select(SourceConnection).where(
                SourceConnection.tenant_id == resource.tenant_id,
                SourceConnection.id == resource.source_connection_id,
            )
        )

    def _build_snapshot(
        self,
        *,
        resource: SourceResource,
        content: str,
        external_revision: str | None,
        raw_storage_uri: str | None,
        parser_version: str | None,
        metadata: dict[str, Any],
    ) -> SourceSnapshot:
        content_hash = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
        return SourceSnapshot(
            tenant_id=resource.tenant_id,
            resource_id=resource.id,
            external_revision=external_revision,
            content_hash=content_hash,
            raw_storage_uri=raw_storage_uri
            or f"control-plane://source-resources/{resource.id}/snapshots/{content_hash}",
            parser_version=parser_version or "metadata-only-v1",
            metadata_json={
                "content_length": len(content),
                "commercial_status": "PARTIAL",
                "runtime_status": "caller_supplied_content",
                **metadata,
            },
            status="captured",
        )

    def _snapshot_payload(self, snapshot: SourceSnapshot | None) -> dict[str, Any] | None:
        if snapshot is None:
            return None
        return {
            "id": snapshot.id,
            "resource_id": snapshot.resource_id,
            "external_revision": snapshot.external_revision,
            "content_hash": snapshot.content_hash,
            "raw_storage_uri": snapshot.raw_storage_uri,
            "captured_at": snapshot.captured_at,
            "parser_version": snapshot.parser_version,
            "metadata_json": snapshot.metadata_json,
            "status": snapshot.status,
            "error_json": snapshot.error_json,
        }

    def _source_connection_payload(self, connection: SourceConnection | None) -> dict[str, Any] | None:
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
