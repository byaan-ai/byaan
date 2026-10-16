from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.mcp import tool_wrappers
from server.mcp.tool_wrappers import describe_sharing_grant_wrapper, list_sharing_grants_wrapper
from server.models.notebooks import Notebook
from server.models.sharing import SharingAuditEvent, SharingCompatibilityLink, SharingGrant, SharingSecret
from server.models.tenant import Tenant
from server.models.user import User

pytestmark = pytest.mark.asyncio


async def _seed_notebook_grant(test_session: AsyncSession) -> tuple[Tenant, User, SharingGrant]:
    tenant = (await test_session.execute(select(Tenant))).scalars().first()
    if tenant is None:
        owner = User(
            id=uuid4(),
            email=f"sharing-read-owner-{uuid4().hex[:8]}@example.test",
            hashed_password="fakehash",
            is_active=True,
            is_verified=True,
        )
        test_session.add(owner)
        await test_session.flush()
        tenant = Tenant(
            id=uuid4(),
            name="Sharing read tenant",
            slug=f"sharing-read-{uuid4().hex[:8]}",
            owner_id=owner.id,
            is_personal=True,
        )
        test_session.add(tenant)
        await test_session.flush()
    owner = await test_session.get(User, tenant.owner_id)
    assert owner is not None
    notebook = Notebook(
        id=uuid4(),
        tenant_id=tenant.id,
        created_by=owner.id,
        notebook_name="Sharing read surface notebook",
        description="Notebook grant fixture",
    )
    grant = SharingGrant(
        tenant_id=tenant.id,
        object_type="notebook",
        object_id=notebook.id,
        object_version_id=None,
        object_version_digest="",
        mode="live_notebook",
        channel="folder",
        audience="folder_member",
        status="active",
        created_by=owner.id,
        metadata_json={
            "legacy_surface": "folder_notebook",
            "legacy_id": "legacy-folder-notebook-1",
            "password": "plain-password",
            "token": "raw-token",
        },
    )
    test_session.add(notebook)
    test_session.add(grant)
    await test_session.flush()
    test_session.add(
        SharingCompatibilityLink(
            tenant_id=tenant.id,
            grant_id=grant.id,
            legacy_surface="folder_notebook",
            legacy_id="legacy-folder-notebook-1",
            metadata_json={"notebook_id": str(notebook.id), "verifier": "raw-verifier"},
        )
    )
    test_session.add(
        SharingSecret(
            tenant_id=tenant.id,
            grant_id=grant.id,
            secret_type="password",
            algorithm="pbkdf2_sha256:210000",
            salt="raw-salt",
            verifier_hash="sha256:raw-verifier-hash",
            status="active",
            created_by=owner.id,
        )
    )
    test_session.add(
        SharingAuditEvent(
            tenant_id=tenant.id,
            grant_id=grant.id,
            object_type="notebook",
            object_id=notebook.id,
            object_version_id=None,
            actor_type="human",
            actor_id=str(owner.id),
            action="sharing.test",
            outcome="active",
            details_json={"sql": "select * from restricted_table", "password": "plain-password"},
        )
    )
    await test_session.commit()
    return tenant, owner, grant


async def test_sharing_rest_read_surface_is_tenant_scoped_and_redacted(test_client, test_session: AsyncSession) -> None:
    tenant, _owner, grant = await _seed_notebook_grant(test_session)

    list_response = await test_client.get("/api/sharing/grants?legacy_surface=folder_notebook")
    assert list_response.status_code == 200
    list_payload = list_response.json()["data"]
    assert list_payload["total"] == 1
    assert list_payload["items"][0]["id"] == str(grant.id)
    assert list_payload["items"][0]["tenant_id"] == str(tenant.id)

    describe_response = await test_client.get(f"/api/sharing/grants/{grant.id}")
    assert describe_response.status_code == 200
    evidence = describe_response.json()["data"]
    assert evidence["grant"]["id"] == str(grant.id)
    assert evidence["compatibility_links"][0]["legacy_surface"] == "folder_notebook"
    assert evidence["has_secret"] is True
    assert evidence["secret_counts"] == [{"secret_type": "password", "status": "active", "count": 1}]
    assert evidence["audit_events"][0]["action"] == "sharing.test"

    serialized = json.dumps(evidence)
    assert "plain-password" not in serialized
    assert "raw-token" not in serialized
    assert "raw-verifier" not in serialized
    assert "raw-salt" not in serialized
    assert "raw-verifier-hash" not in serialized
    assert "restricted_table" not in serialized

    other_user = User(
        id=uuid4(),
        email=f"sharing-read-other-{uuid4().hex[:8]}@example.test",
        hashed_password="fakehash",
        is_active=True,
        is_verified=True,
    )
    other_tenant = Tenant(
        id=uuid4(),
        name="Other sharing tenant",
        slug=f"other-sharing-{uuid4().hex[:8]}",
        owner_id=other_user.id,
        is_personal=True,
    )
    test_session.add(other_user)
    await test_session.flush()
    test_session.add(other_tenant)
    await test_session.commit()
    not_found = await test_client.get(
        f"/api/sharing/grants/{grant.id}",
        headers={"x-tenant-id": str(other_tenant.id), "X-Local-User-ID": str(other_user.id)},
    )
    assert not_found.status_code == 404


@pytest.fixture(autouse=True)
def _patch_mcp_session_factory(test_engine, monkeypatch: pytest.MonkeyPatch):
    test_session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    monkeypatch.setattr(tool_wrappers, "AsyncSessionFactory", test_session_factory)


async def test_sharing_mcp_read_surface_matches_rest_redaction(test_session: AsyncSession) -> None:
    tenant, owner, grant = await _seed_notebook_grant(test_session)

    list_payload = json.loads(
        await list_sharing_grants_wrapper(
            tenant.id,
            owner.id,
            legacy_surface="folder_notebook",
        )
    )
    assert list_payload["success"] is True
    assert list_payload["total"] == 1
    assert list_payload["items"][0]["id"] == str(grant.id)

    evidence = json.loads(await describe_sharing_grant_wrapper(str(grant.id), tenant.id, owner.id))
    assert evidence["success"] is True
    assert evidence["grant"]["id"] == str(grant.id)
    assert evidence["compatibility_links"][0]["legacy_surface"] == "folder_notebook"
    assert evidence["has_secret"] is True

    serialized = json.dumps(evidence)
    assert "plain-password" not in serialized
    assert "raw-token" not in serialized
    assert "raw-verifier" not in serialized
    assert "raw-salt" not in serialized
    assert "raw-verifier-hash" not in serialized
    assert "restricted_table" not in serialized
