from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from server.models.source_connections import SourceConnection
from server.models.source_resources import SourceResource
from server.models.tenant import Tenant
from server.models.tenant_member import TenantMember, TenantRole
from server.models.user import User
from server.services.crypto_service import CryptoService

pytestmark = pytest.mark.asyncio


async def test_connector_definitions_endpoint_reports_partial_summary(test_client) -> None:
    response = await test_client.get("/api/connector-definitions")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["summary"]["overall_status"] == "PARTIAL"
    assert data["summary"]["ready"] == 0
    assert data["summary"]["beta"] >= 6
    assert data["summary"]["planned"] >= 1
    assert all(item["availability"] != "available" for item in data["items"])


async def test_source_connection_lifecycle_encrypts_and_redacts_credentials(test_client, test_session) -> None:
    payload = {
        "provider": "volcengine_tos",
        "auth_mode": "access_key",
        "display_name": "TOS beta account",
        "external_account_id": "tos-account-1",
        "credentials": {
            "endpoint": "https://tos.example.test",
            "region": "cn-north-1",
            "access_key_id": "AKIA_TEST_ONLY",
            "secret_access_key": "super-secret",
        },
    }

    create_response = await test_client.post("/api/source-connections", json=payload)

    assert create_response.status_code == 201
    connection_payload = create_response.json()["data"]
    assert connection_payload["provider"] == "volcengine_tos"
    assert connection_payload["status"] == "beta"
    assert "credentials" not in connection_payload
    assert "secret_access_key" not in str(connection_payload)

    connection = (
        await test_session.execute(select(SourceConnection).where(SourceConnection.id == connection_payload["id"]))
    ).scalar_one()
    assert "super-secret" not in connection.encrypted_credentials
    decrypted = await CryptoService.decrypt_config(connection.encrypted_credentials, test_session)
    assert decrypted["secret_access_key"] == "super-secret"

    list_response = await test_client.get("/api/source-connections?provider=volcengine_tos")
    assert list_response.status_code == 200
    assert list_response.json()["data"]["total"] == 1
    assert "super-secret" not in str(list_response.json())

    delete_response = await test_client.delete(f"/api/source-connections/{connection_payload['id']}")
    assert delete_response.status_code == 200
    await test_session.refresh(connection)
    assert connection.status == "disconnected"


async def test_source_resource_snapshot_and_tenant_isolation(test_client, test_session) -> None:
    connection_response = await test_client.post(
        "/api/source-connections",
        json={
            "provider": "web",
            "auth_mode": "none",
            "display_name": "Web metadata",
            "credentials": {},
        },
    )
    assert connection_response.status_code == 201
    source_connection_id = connection_response.json()["data"]["id"]

    create_response = await test_client.post(
        "/api/source-resources",
        json={
            "resource_type": "web",
            "name": "Quarterly plan",
            "source_url": "https://example.test/plan",
            "source_connection_id": source_connection_id,
            "content": "Revenue plan source text",
            "external_revision": "rev-1",
            "metadata": {"source": "unit-test"},
        },
    )

    assert create_response.status_code == 201
    resource_payload = create_response.json()["data"]
    assert resource_payload["status"] == "ready"
    assert resource_payload["latest_snapshot"]["external_revision"] == "rev-1"
    assert resource_payload["latest_snapshot"]["content_hash"].startswith("sha256:")

    snapshot_response = await test_client.get(f"/api/source-resources/{resource_payload['id']}/snapshots")
    assert snapshot_response.status_code == 200
    assert snapshot_response.json()["data"]["total"] == 1

    sync_response = await test_client.post(
        f"/api/source-resources/{resource_payload['id']}/sync",
        json={"content": "Revenue plan source text updated", "external_revision": "rev-2"},
    )
    assert sync_response.status_code == 200
    assert sync_response.json()["data"]["latest_snapshot"]["external_revision"] == "rev-2"

    resource = (
        await test_session.execute(select(SourceResource).where(SourceResource.id == resource_payload["id"]))
    ).scalar_one()
    assert resource.tenant_id is not None

    other_tenant_id = uuid4()
    other_user_id = uuid4()
    other_user = User(
        id=other_user_id,
        email="other@test.com",
        hashed_password="fakehash",
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    test_session.add(other_user)
    await test_session.flush()
    other_tenant = Tenant(
        id=other_tenant_id,
        name="Other Tenant",
        slug="other-tenant",
        owner_id=other_user_id,
        is_personal=True,
    )
    test_session.add(other_tenant)
    await test_session.flush()
    test_session.add(TenantMember(user_id=other_user_id, tenant_id=other_tenant_id, role=TenantRole.OWNER.value))
    await test_session.commit()

    isolated_response = await test_client.get(
        f"/api/source-resources/{resource_payload['id']}",
        headers={"x-tenant-id": str(other_tenant_id)},
    )
    assert isolated_response.status_code == 404
