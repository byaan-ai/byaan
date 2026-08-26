from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.dashboard import Dashboard
from server.models.folder import Folder
from server.models.notebooks import Notebook
from server.models.sharing import SharingCompatibilityLink, SharingGrant
from server.models.tenant import Tenant
from server.models.user import User
from server.services.folder_service import FolderService

pytestmark = pytest.mark.asyncio


async def _seed_workspace(test_session: AsyncSession) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    owner = User(
        id=uuid4(),
        email=f"folder-sharing-owner-{uuid4().hex[:8]}@example.test",
        hashed_password="fakehash",
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    test_session.add(owner)
    await test_session.flush()
    tenant = Tenant(
        id=uuid4(),
        name="Folder Sharing Tenant",
        slug=f"folder-sharing-{uuid4().hex[:8]}",
        owner_id=owner.id,
        is_personal=True,
    )
    test_session.add(tenant)
    await test_session.flush()
    folder = Folder(
        id=uuid4(),
        tenant_id=tenant.id,
        created_by=owner.id,
        name="Shared folder",
        description="Canonical sharing integration",
    )
    notebook = Notebook(
        id=uuid4(),
        tenant_id=tenant.id,
        created_by=owner.id,
        notebook_name="Shared notebook",
        description="Notebook share fixture",
    )
    dashboard = Dashboard(
        id=uuid4(),
        tenant_id=tenant.id,
        notebook_id=notebook.id,
        version_num=1,
        html_content="<main>dashboard</main>",
    )
    test_session.add_all([folder, notebook])
    await test_session.flush()
    test_session.add(dashboard)
    await test_session.commit()
    return tenant.id, owner.id, folder.id, notebook.id, dashboard.id


async def test_folder_notebook_share_creates_and_revokes_canonical_grant(test_session: AsyncSession) -> None:
    tenant_id, owner_id, folder_id, notebook_id, _dashboard_id = await _seed_workspace(test_session)

    folder_notebook = await FolderService.share_notebook_to_folder(
        folder_id=folder_id,
        notebook_id=notebook_id,
        shared_by=owner_id,
        is_snapshot=False,
        session=test_session,
    )

    grant = (
        await test_session.execute(
            select(SharingGrant)
            .join(SharingCompatibilityLink, SharingCompatibilityLink.grant_id == SharingGrant.id)
            .where(
                SharingGrant.tenant_id == tenant_id,
                SharingCompatibilityLink.legacy_surface == "folder_notebook",
                SharingCompatibilityLink.legacy_id == str(folder_notebook.id),
            )
        )
    ).scalar_one()
    assert grant.object_type == "notebook"
    assert grant.object_id == notebook_id
    assert grant.channel == "folder"
    assert grant.status == "active"

    removed = await FolderService.unshare_notebook_from_folder(
        folder_id=folder_id,
        notebook_id=notebook_id,
        user_id=owner_id,
        session=test_session,
    )
    assert removed is True
    revoked = await test_session.get(SharingGrant, grant.id)
    assert revoked is not None
    assert revoked.status == "revoked"
    assert revoked.revoked_at is not None


async def test_folder_dashboard_share_creates_and_revokes_canonical_grant(test_session: AsyncSession) -> None:
    tenant_id, owner_id, folder_id, _notebook_id, dashboard_id = await _seed_workspace(test_session)

    folder_dashboard = await FolderService.share_dashboard_to_folder(
        folder_id=folder_id,
        dashboard_id=dashboard_id,
        shared_by=owner_id,
        is_snapshot=False,
        session=test_session,
    )

    grant = (
        await test_session.execute(
            select(SharingGrant)
            .join(SharingCompatibilityLink, SharingCompatibilityLink.grant_id == SharingGrant.id)
            .where(
                SharingGrant.tenant_id == tenant_id,
                SharingCompatibilityLink.legacy_surface == "folder_dashboard",
                SharingCompatibilityLink.legacy_id == str(folder_dashboard.id),
            )
        )
    ).scalar_one()
    assert grant.object_type == "dashboard"
    assert grant.object_id == dashboard_id
    assert grant.object_version_id == dashboard_id
    assert grant.channel == "folder"
    assert grant.status == "active"

    removed = await FolderService.unshare_dashboard_from_folder(
        folder_id=folder_id,
        dashboard_id=dashboard_id,
        user_id=owner_id,
        session=test_session,
    )
    assert removed is True
    revoked = await test_session.get(SharingGrant, grant.id)
    assert revoked is not None
    assert revoked.status == "revoked"
    assert revoked.revoked_at is not None
