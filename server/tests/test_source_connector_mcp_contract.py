from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastmcp import FastMCP
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.mcp import tool_wrappers
from server.mcp.tool_wrappers import (
    create_source_connection_wrapper,
    create_source_resource_wrapper,
    describe_source_resource_wrapper,
    disconnect_source_connection_wrapper,
    list_connector_definitions_wrapper,
    list_source_connections_wrapper,
    list_source_resources_wrapper,
    sync_source_resource_wrapper,
)
from server.mcp.tools import register_all_tools
from server.models.source_connections import SourceConnection
from server.models.tenant import Tenant
from server.models.tenant_member import TenantMember, TenantRole
from server.models.user import User
from server.services.crypto_service import CryptoService

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _patch_mcp_session_factory(test_engine, monkeypatch: pytest.MonkeyPatch):
    test_session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    monkeypatch.setattr(tool_wrappers, "AsyncSessionFactory", test_session_factory)


async def _seed_owner(test_session: AsyncSession) -> tuple[Tenant, User]:
    owner = User(
        id=uuid4(),
        email=f"source-mcp-owner-{uuid4()}@example.test",
        hashed_password="fakehash",
        is_active=True,
        is_verified=True,
    )
    test_session.add(owner)
    await test_session.flush()
    tenant = Tenant(
        id=uuid4(),
        name="Source MCP Tenant",
        slug=f"source-mcp-{uuid4().hex[:8]}",
        owner_id=owner.id,
        is_personal=True,
    )
    test_session.add(tenant)
    await test_session.flush()
    test_session.add(TenantMember(user_id=owner.id, tenant_id=tenant.id, role=TenantRole.OWNER.value))
    await test_session.commit()
    return tenant, owner


async def test_source_connector_mcp_tools_are_registered() -> None:
    async def get_session():
        return {"session_id": "test", "tenant_id": uuid4(), "user_id": uuid4(), "notebook_id": None}

    mcp = FastMCP("source-registration-test")
    register_all_tools(mcp, get_session)

    names = {tool.name for tool in await mcp.list_tools()}

    assert {
        "list_connector_definitions",
        "create_source_connection",
        "list_source_connections",
        "disconnect_source_connection",
        "create_source_resource",
        "list_source_resources",
        "describe_source_resource",
        "sync_source_resource",
    }.issubset(names)


async def test_source_connector_mcp_control_plane_lifecycle_and_redaction(
    test_session: AsyncSession,
) -> None:
    tenant, owner = await _seed_owner(test_session)

    catalog_payload = json.loads(
        await list_connector_definitions_wrapper(
            tenant.id,
            owner.id,
            provider="volcengine_tos",
            include_planned=False,
        )
    )
    assert catalog_payload["success"] is True
    assert catalog_payload["summary"]["overall_status"] == "PARTIAL"
    assert catalog_payload["items"][0]["availability"] == "beta"

    connection_payload = json.loads(
        await create_source_connection_wrapper(
            json.dumps(
                {
                    "provider": "volcengine_tos",
                    "auth_mode": "access_key",
                    "display_name": "TOS MCP beta account",
                    "external_account_id": "tos-mcp-account",
                    "credentials": {
                        "endpoint": "https://tos.example.test",
                        "access_key_id": "AKIA_MCP_TEST_ONLY",
                        "secret_access_key": "super-secret-mcp",
                    },
                }
            ),
            tenant.id,
            owner.id,
        )
    )
    assert connection_payload["success"] is True
    assert connection_payload["connection"]["status"] == "beta"
    serialized_connection_payload = json.dumps(connection_payload)
    assert "credentials" not in serialized_connection_payload
    assert "super-secret-mcp" not in serialized_connection_payload

    connection = (
        await test_session.execute(
            select(SourceConnection).where(SourceConnection.id == connection_payload["connection"]["id"])
        )
    ).scalar_one()
    assert "super-secret-mcp" not in connection.encrypted_credentials
    decrypted = await CryptoService.decrypt_config(connection.encrypted_credentials, test_session)
    assert decrypted["secret_access_key"] == "super-secret-mcp"

    list_payload = json.loads(await list_source_connections_wrapper(tenant.id, owner.id, "volcengine_tos"))
    assert list_payload["success"] is True
    assert list_payload["total"] == 1
    assert "super-secret-mcp" not in json.dumps(list_payload)

    resource_payload = json.loads(
        await create_source_resource_wrapper(
            json.dumps(
                {
                    "resource_type": "tos_object",
                    "name": "Revenue object",
                    "external_id": "bucket/revenue.csv",
                    "source_connection_id": connection_payload["connection"]["id"],
                    "content": "region,revenue\nAMER,42\n",
                    "external_revision": "etag-1",
                    "metadata": {"source": "mcp-test", "token": "raw-token"},
                }
            ),
            tenant.id,
            owner.id,
        )
    )
    assert resource_payload["success"] is True
    assert resource_payload["resource"]["status"] == "ready"
    assert resource_payload["resource"]["latest_snapshot"]["content_hash"].startswith("sha256:")
    assert "raw-token" not in json.dumps(resource_payload)

    resources = json.loads(await list_source_resources_wrapper(tenant.id, owner.id, query="revenue"))
    assert resources["success"] is True
    assert resources["items"][0]["name"] == "Revenue object"

    described = json.loads(
        await describe_source_resource_wrapper(resource_payload["resource"]["id"], tenant.id, owner.id)
    )
    assert described["success"] is True
    assert described["snapshots"]["total"] == 1

    synced = json.loads(
        await sync_source_resource_wrapper(
            resource_payload["resource"]["id"],
            json.dumps({"content": "region,revenue\nEMEA,43\n", "external_revision": "etag-2"}),
            tenant.id,
            owner.id,
        )
    )
    assert synced["success"] is True
    assert synced["resource"]["latest_snapshot"]["external_revision"] == "etag-2"

    disconnected = json.loads(
        await disconnect_source_connection_wrapper(connection_payload["connection"]["id"], tenant.id, owner.id)
    )
    assert disconnected["success"] is True
    assert disconnected["affected_resource_count"] == 1
