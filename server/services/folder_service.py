from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from server.models.connections import Connection
from server.models.dashboard import Dashboard
from server.models.folder import Folder
from server.models.folder_member import FolderMember
from server.models.folder_notebook import FolderNotebook
from server.models.messages import Message
from server.models.notebooks import Notebook, NotebookDataset
from server.models.queries import Query
from server.models.tenant_member import TenantMember, TenantRole
from server.models.threads import Thread
from server.repositories.folder import FolderRepository
from server.repositories.folder_dashboard import FolderDashboardRepository
from server.repositories.folder_member import FolderMemberRepository
from server.repositories.folder_notebook import FolderNotebookRepository
from server.repositories.notebooks import NotebookRepository
from server.schemas.notebook_export import NotebookExport
from server.services.agent_session_factory import create_agent_session
from server.services.dataset import DatasetService
from server.services.notebook_export_service import NotebookExportService
from server.services.sharing import SharingService
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)


async def _warm_dashboard_cache_background(dashboard_id: str, notebook_id: str) -> None:
    """Warm cache for dashboard queries in the background."""
    from server.db.session import get_async_session_context
    from server.services.dashboard_cache_service import DashboardCacheService

    try:
        async with get_async_session_context() as bg_session:
            await DashboardCacheService.refresh_dashboard_cache(
                session=bg_session,
                dashboard_id=dashboard_id,
                notebook_id=notebook_id,
                triggered_by="dashboard_share",
            )
        logger.info(f"Background cache warming completed for dashboard {dashboard_id}")
    except Exception as e:
        logger.warning(f"Background cache warming failed for dashboard {dashboard_id}: {e}")


class FolderService:
    """Service for managing folders and sharing."""

    # ==================== Folder CRUD ====================

    @staticmethod
    async def create_folder(
        tenant_id: UUID,
        created_by: UUID,
        name: str,
        description: str | None,
        is_public: bool,
        session: AsyncSession,
    ) -> Folder:
        """Create a new folder and add creator as first member."""
        folder = Folder(
            tenant_id=tenant_id,
            created_by=created_by,
            name=name,
            description=description,
            is_public=is_public,
        )
        session.add(folder)
        await session.flush()  # Get folder ID

        # Add creator as folder member automatically
        member = FolderMember(
            folder_id=folder.id,
            user_id=created_by,
            added_by=created_by,
        )
        session.add(member)

        await session.commit()
        await session.refresh(folder)

        logger.info(f"Folder '{name}' created by user {created_by} in tenant {tenant_id}")
        return folder

    @staticmethod
    async def get_folder(folder_id: UUID, session: AsyncSession) -> Folder | None:
        """Get a folder by ID."""
        folder_repo = FolderRepository(session)
        return await folder_repo.get(folder_id)

    @staticmethod
    async def get_folder_with_creator(folder_id: UUID, session: AsyncSession) -> Folder | None:
        """Get a folder by ID with creator loaded."""
        folder_repo = FolderRepository(session)
        return await folder_repo.get_with_creator(folder_id)

    @staticmethod
    async def list_folders_for_user(
        user_id: UUID,
        tenant_id: UUID,
        session: AsyncSession,
    ) -> list[Folder]:
        """List folders visible to user. All users see only folders they're members of + public folders."""
        folder_repo = FolderRepository(session)
        return await folder_repo.list_by_user_membership_or_public(user_id, tenant_id)

    @staticmethod
    async def update_folder(
        folder_id: UUID,
        name: str | None,
        description: str | None,
        is_public: bool | None,
        session: AsyncSession,
    ) -> Folder | None:
        """Update folder details."""
        folder_repo = FolderRepository(session)
        update_data = {}
        if name is not None:
            update_data["name"] = name
        if description is not None:
            update_data["description"] = description
        if is_public is not None:
            update_data["is_public"] = is_public

        if not update_data:
            return await folder_repo.get(folder_id)

        return await folder_repo.update(folder_id, update_data)

    @staticmethod
    async def delete_folder(folder_id: UUID, session: AsyncSession) -> bool:
        """Delete a folder (cascades to members and shared notebooks)."""
        folder_repo = FolderRepository(session)
        result = await folder_repo.delete(folder_id)
        if result:
            logger.info(f"Folder {folder_id} deleted")
        return result

    # ==================== Folder Member Management ====================

    @staticmethod
    async def can_manage_folder_members(
        folder: Folder,
        user_id: UUID,
        user_role: TenantRole,
    ) -> bool:
        """Check if user can manage folder members (creator or tenant admin)."""
        if user_role in (TenantRole.OWNER, TenantRole.ADMIN):
            return True
        return folder.created_by == user_id

    @staticmethod
    async def add_folder_member(
        folder_id: UUID,
        user_id: UUID,
        added_by: UUID,
        tenant_id: UUID,
        session: AsyncSession,
    ) -> FolderMember:
        """Add a user to a folder. User must be a tenant member."""
        # Verify user is a tenant member
        result = await session.execute(
            select(TenantMember).where(TenantMember.tenant_id == tenant_id, TenantMember.user_id == user_id)
        )
        if not result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="User is not a member of this workspace"
            )

        # Check if already a member
        member_repo = FolderMemberRepository(session)
        existing = await member_repo.get_membership(folder_id, user_id)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="User is already a member of this folder"
            )

        member = FolderMember(
            folder_id=folder_id,
            user_id=user_id,
            added_by=added_by,
        )
        session.add(member)
        await session.commit()

        # Re-fetch with relationships loaded to support Pydantic model_validate
        result = await session.execute(
            select(FolderMember)
            .where(FolderMember.id == member.id)
            .options(selectinload(FolderMember.user), selectinload(FolderMember.added_by_user))
        )
        member = result.scalar_one()

        logger.info(f"User {user_id} added to folder {folder_id} by {added_by}")
        return member

    @staticmethod
    async def remove_folder_member(
        folder_id: UUID,
        member_id: UUID,
        session: AsyncSession,
    ) -> bool:
        """Remove a member from a folder."""
        member_repo = FolderMemberRepository(session)
        result = await member_repo.delete(member_id)
        if result:
            logger.info(f"Member {member_id} removed from folder {folder_id}")
        return result

    @staticmethod
    async def list_folder_members(
        folder_id: UUID,
        session: AsyncSession,
    ) -> list[FolderMember]:
        """List all members of a folder."""
        member_repo = FolderMemberRepository(session)
        return await member_repo.list_by_folder(folder_id)

    @staticmethod
    async def is_folder_member(
        folder_id: UUID,
        user_id: UUID,
        session: AsyncSession,
    ) -> bool:
        """Check if user is a folder member."""
        member_repo = FolderMemberRepository(session)
        return await member_repo.is_member(folder_id, user_id)

    # ==================== Notebook Sharing ====================

    @staticmethod
    async def share_notebook_to_folder(
        folder_id: UUID,
        notebook_id: UUID,
        shared_by: UUID,
        is_snapshot: bool,
        session: AsyncSession,
    ) -> FolderNotebook:
        """Share a notebook to a folder. User must own the notebook.

        Args:
            is_snapshot: If True, creates a frozen snapshot. If False, creates live share.
        """
        # Verify notebook exists and user owns it
        notebook_repo = NotebookRepository(session)
        notebook = await notebook_repo.get(notebook_id)

        if not notebook:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")

        if notebook.created_by != shared_by:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="You can only share notebooks you created"
            )

        # Check if already shared
        folder_notebook_repo = FolderNotebookRepository(session)
        existing = await folder_notebook_repo.get_by_folder_and_notebook(folder_id, notebook_id)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Notebook is already shared to this folder"
            )

        # Prepare snapshot data if needed
        snapshot_data = None
        snapshot_updated_at = None

        if is_snapshot:
            export = await NotebookExportService.export_notebook(session, notebook_id)
            snapshot_data = export.model_dump_json()
            snapshot_updated_at = datetime.utcnow()

        folder_notebook = FolderNotebook(
            folder_id=folder_id,
            notebook_id=notebook_id,
            shared_by=shared_by,
            is_snapshot=is_snapshot,
            snapshot_data=snapshot_data,
            snapshot_updated_at=snapshot_updated_at,
        )
        session.add(folder_notebook)
        await session.commit()
        await session.refresh(folder_notebook)

        await SharingService(session).ensure_folder_notebook_grant(
            tenant_id=notebook.tenant_id,
            actor_id=shared_by,
            folder_notebook_id=folder_notebook.id,
            notebook_id=notebook_id,
            is_snapshot=is_snapshot,
            snapshot_updated_at=snapshot_updated_at,
        )

        share_type = "snapshot" if is_snapshot else "live"
        logger.info(f"Notebook {notebook_id} shared ({share_type}) to folder {folder_id} by user {shared_by}")
        return folder_notebook

    @staticmethod
    async def unshare_notebook_from_folder(
        folder_id: UUID,
        notebook_id: UUID,
        user_id: UUID,
        session: AsyncSession,
    ) -> bool:
        """Remove a notebook from a folder. Only original creator can do this."""
        notebook_repo = NotebookRepository(session)
        notebook = await notebook_repo.get(notebook_id)

        if not notebook:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")

        if notebook.created_by != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Only the notebook creator can unshare it"
            )

        folder_notebook_repo = FolderNotebookRepository(session)
        folder_notebook = await folder_notebook_repo.get_by_folder_and_notebook(folder_id, notebook_id)

        if not folder_notebook:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook is not shared to this folder")

        result = await folder_notebook_repo.delete(folder_notebook.id)
        if result:
            await SharingService(session).revoke_legacy_grant(
                tenant_id=notebook.tenant_id,
                legacy_surface="folder_notebook",
                legacy_id=str(folder_notebook.id),
                actor_id=user_id,
                reason="folder notebook share removed",
            )
            logger.info(f"Notebook {notebook_id} unshared from folder {folder_id}")
        return result

    @staticmethod
    async def list_folder_notebooks(
        folder_id: UUID,
        session: AsyncSession,
    ) -> list[FolderNotebook]:
        """List all notebooks shared to a folder."""
        folder_notebook_repo = FolderNotebookRepository(session)
        return await folder_notebook_repo.list_by_folder(folder_id)

    @staticmethod
    async def list_folders_for_notebook(
        notebook_id: UUID,
        session: AsyncSession,
    ) -> list[FolderNotebook]:
        """List all folders a notebook is shared to."""
        folder_notebook_repo = FolderNotebookRepository(session)
        return await folder_notebook_repo.list_by_notebook(notebook_id)

    @staticmethod
    async def update_snapshot(
        folder_id: UUID,
        notebook_id: UUID,
        user_id: UUID,
        session: AsyncSession,
    ) -> FolderNotebook:
        """Update a snapshot share to the current notebook state. Only owner can do this."""
        folder_notebook_repo = FolderNotebookRepository(session)
        folder_notebook = await folder_notebook_repo.get_by_folder_and_notebook(folder_id, notebook_id)

        if not folder_notebook:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook is not shared to this folder")

        # Only the person who shared can update the snapshot
        if folder_notebook.shared_by != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner who shared can update the snapshot"
            )

        if not folder_notebook.is_snapshot:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This is a live share, not a snapshot")

        # Re-export the notebook to update snapshot
        export = await NotebookExportService.export_notebook(session, notebook_id)
        folder_notebook.snapshot_data = export.model_dump_json()
        folder_notebook.snapshot_updated_at = datetime.utcnow()

        await session.commit()
        await session.refresh(folder_notebook)

        logger.info(f"Snapshot updated for notebook {notebook_id} in folder {folder_id} by user {user_id}")
        return folder_notebook

    # ==================== Dashboard Sharing ====================

    @staticmethod
    async def share_dashboard_to_folder(
        folder_id: UUID,
        dashboard_id: UUID,
        shared_by: UUID,
        is_snapshot: bool,
        session: AsyncSession,
    ) -> FolderDashboard:
        """Share a dashboard to a folder. User must own the notebook that contains the dashboard."""
        from server.models.folder_dashboard import FolderDashboard
        from server.repositories.dashboard import DashboardRepository

        # Verify dashboard exists
        dashboard_repo = DashboardRepository(session)
        dashboard = await dashboard_repo.get(dashboard_id)

        if not dashboard:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard not found")

        # Verify user owns the notebook that contains the dashboard
        notebook_repo = NotebookRepository(session)
        notebook = await notebook_repo.get(dashboard.notebook_id)

        if not notebook:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Associated notebook not found")

        if notebook.created_by != shared_by:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="You can only share dashboards from notebooks you created"
            )

        folder_dashboard_repo = FolderDashboardRepository(session)

        # Check if any dashboard from this notebook is already shared to this folder
        existing_from_notebook = await folder_dashboard_repo.get_by_folder_and_notebook(folder_id, notebook.id)
        if existing_from_notebook:
            # Update the existing share to point to the new dashboard version
            old_dashboard_id = existing_from_notebook.dashboard_id
            existing_from_notebook.dashboard_id = dashboard_id
            existing_from_notebook.is_snapshot = is_snapshot
            await session.commit()
            await session.refresh(existing_from_notebook)
            await SharingService(session).ensure_folder_dashboard_grant(
                tenant_id=dashboard.tenant_id,
                actor_id=shared_by,
                folder_dashboard_id=existing_from_notebook.id,
                dashboard_id=dashboard_id,
            )
            logger.info(
                f"Dashboard share updated from {old_dashboard_id} to {dashboard_id} "
                f"in folder {folder_id} by user {shared_by}"
            )
            asyncio.create_task(_warm_dashboard_cache_background(str(dashboard_id), str(dashboard.notebook_id)))
            return existing_from_notebook

        # For now, we don't support snapshot for dashboards (can be added later)
        folder_dashboard = FolderDashboard(
            folder_id=folder_id,
            dashboard_id=dashboard_id,
            shared_by=shared_by,
            is_snapshot=is_snapshot,
            snapshot_data=None,
            snapshot_updated_at=None,
        )
        session.add(folder_dashboard)
        await session.commit()
        await session.refresh(folder_dashboard)

        await SharingService(session).ensure_folder_dashboard_grant(
            tenant_id=dashboard.tenant_id,
            actor_id=shared_by,
            folder_dashboard_id=folder_dashboard.id,
            dashboard_id=dashboard_id,
        )

        share_type = "snapshot" if is_snapshot else "live"
        logger.info(f"Dashboard {dashboard_id} shared ({share_type}) to folder {folder_id} by user {shared_by}")
        asyncio.create_task(_warm_dashboard_cache_background(str(dashboard_id), str(dashboard.notebook_id)))
        return folder_dashboard

    @staticmethod
    async def unshare_dashboard_from_folder(
        folder_id: UUID,
        dashboard_id: UUID,
        user_id: UUID,
        session: AsyncSession,
    ) -> bool:
        """Remove a dashboard from a folder. Only the notebook creator can do this."""
        from server.repositories.dashboard import DashboardRepository

        # Verify dashboard exists and user owns the notebook
        dashboard_repo = DashboardRepository(session)
        dashboard = await dashboard_repo.get(dashboard_id)

        if not dashboard:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard not found")

        notebook_repo = NotebookRepository(session)
        notebook = await notebook_repo.get(dashboard.notebook_id)

        if not notebook or notebook.created_by != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Only the notebook creator can unshare the dashboard"
            )

        folder_dashboard_repo = FolderDashboardRepository(session)
        folder_dashboard = await folder_dashboard_repo.get_by_folder_and_dashboard(folder_id, dashboard_id)

        if not folder_dashboard:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard is not shared to this folder")

        result = await folder_dashboard_repo.delete(folder_dashboard.id)
        if result:
            await SharingService(session).revoke_legacy_grant(
                tenant_id=dashboard.tenant_id,
                legacy_surface="folder_dashboard",
                legacy_id=str(folder_dashboard.id),
                actor_id=user_id,
                reason="folder dashboard share removed",
            )
            logger.info(f"Dashboard {dashboard_id} unshared from folder {folder_id}")
        return result

    @staticmethod
    async def list_folder_dashboards(
        folder_id: UUID,
        session: AsyncSession,
    ) -> list[FolderDashboard]:
        """List all dashboards shared to a folder."""

        folder_dashboard_repo = FolderDashboardRepository(session)
        return await folder_dashboard_repo.list_by_folder(folder_id)

    @staticmethod
    async def list_folders_for_dashboard(
        dashboard_id: UUID,
        session: AsyncSession,
    ) -> list[FolderDashboard]:
        """List all folders a dashboard is shared to."""

        folder_dashboard_repo = FolderDashboardRepository(session)
        return await folder_dashboard_repo.list_by_dashboard(dashboard_id)

    @staticmethod
    async def list_folders_for_notebook_dashboards(
        notebook_id: UUID,
        session: AsyncSession,
    ) -> list[dict]:
        """List folders where any dashboard version of a notebook is shared.

        Returns unique folders with version info for each share.
        """
        folder_dashboard_repo = FolderDashboardRepository(session)
        folder_dashboards = await folder_dashboard_repo.list_by_notebook_id(notebook_id)

        seen_folders: set[UUID] = set()
        result = []
        for fd in folder_dashboards:
            if fd.folder_id in seen_folders:
                continue
            seen_folders.add(fd.folder_id)
            result.append(
                {
                    "id": str(fd.id),
                    "folder_id": str(fd.folder_id),
                    "folder_name": fd.folder.name if fd.folder else None,
                    "folder_description": fd.folder.description if fd.folder else None,
                    "shared_by": str(fd.shared_by) if fd.shared_by else None,
                    "shared_by_user": {
                        "id": str(fd.shared_by_user.id),
                        "email": fd.shared_by_user.email,
                        "full_name": fd.shared_by_user.full_name,
                    }
                    if fd.shared_by_user
                    else None,
                    "created_at": fd.created_at.isoformat() if fd.created_at else None,
                    "is_snapshot": fd.is_snapshot,
                    "snapshot_updated_at": fd.snapshot_updated_at.isoformat() if fd.snapshot_updated_at else None,
                    "dashboard_id": str(fd.dashboard_id),
                    "shared_version": fd.dashboard.version_num if fd.dashboard else None,
                }
            )
        return result

    @staticmethod
    async def update_folder_dashboard_version(
        folder_id: UUID,
        old_dashboard_id: UUID,
        new_dashboard_id: UUID,
        user_id: UUID,
        session: AsyncSession,
    ) -> FolderDashboard:
        """Update a shared dashboard to a different version. User must own the notebook."""
        from server.repositories.dashboard import DashboardRepository

        dashboard_repo = DashboardRepository(session)
        folder_dashboard_repo = FolderDashboardRepository(session)

        # Verify old dashboard exists
        old_dashboard = await dashboard_repo.get(old_dashboard_id)
        if not old_dashboard:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Original dashboard not found")

        # Verify new dashboard exists
        new_dashboard = await dashboard_repo.get(new_dashboard_id)
        if not new_dashboard:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="New dashboard not found")

        # Verify both dashboards belong to the same notebook
        if old_dashboard.notebook_id != new_dashboard.notebook_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New dashboard must be from the same notebook",
            )

        # Verify user owns the notebook
        notebook_repo = NotebookRepository(session)
        notebook = await notebook_repo.get(old_dashboard.notebook_id)
        if not notebook or notebook.created_by != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the notebook creator can update the shared dashboard version",
            )

        # Get the existing folder_dashboard record
        folder_dashboard = await folder_dashboard_repo.get_by_folder_and_dashboard(folder_id, old_dashboard_id)
        if not folder_dashboard:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Dashboard is not shared to this folder",
            )

        # Update the dashboard_id to the new version
        folder_dashboard.dashboard_id = new_dashboard_id
        await session.commit()
        await session.refresh(folder_dashboard)

        await SharingService(session).ensure_folder_dashboard_grant(
            tenant_id=new_dashboard.tenant_id,
            actor_id=user_id,
            folder_dashboard_id=folder_dashboard.id,
            dashboard_id=new_dashboard_id,
        )

        logger.info(
            f"Dashboard version updated in folder {folder_id}: {old_dashboard_id} -> {new_dashboard_id} by user {user_id}"
        )
        return folder_dashboard

    # ==================== Notebook Cloning ====================

    @staticmethod
    async def clone_notebook(
        folder_id: UUID,
        notebook_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
        new_name: str | None,
        session: AsyncSession,
    ) -> dict:
        """Clone a shared notebook with full data: chat history, queries, dashboards, datasets."""
        # Verify notebook is shared to this folder
        folder_notebook_repo = FolderNotebookRepository(session)
        folder_notebook = await folder_notebook_repo.get_by_folder_and_notebook(folder_id, notebook_id)

        if not folder_notebook:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook is not shared to this folder")

        # Get export data - from snapshot or live notebook
        if folder_notebook.is_snapshot and folder_notebook.snapshot_data:
            # Parse from stored snapshot JSON
            export = NotebookExport.model_validate_json(folder_notebook.snapshot_data)
        else:
            # Export live notebook
            export = await NotebookExportService.export_notebook(session, notebook_id)

        # Create cloned notebook
        clone_name = new_name or f"Copy of {export.title}"
        cloned_notebook = Notebook(
            tenant_id=tenant_id,
            created_by=user_id,
            notebook_name=clone_name,
            description=export.description,
        )
        session.add(cloned_notebook)
        await session.flush()

        # Clone chat history (create thread + messages)
        # IMPORTANT: Thread ID must equal notebook ID for messages API to work
        messages_cloned = 0
        if export.chat_history:
            cloned_thread = Thread(
                id=cloned_notebook.id,  # Thread ID must match notebook ID
                notebook_id=cloned_notebook.id,
                thread_title="Cloned conversation",
            )
            session.add(cloned_thread)
            await session.flush()

            for idx, msg in enumerate(export.chat_history):
                if msg.created_at:
                    try:
                        message_timestamp = datetime.fromisoformat(msg.created_at.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        message_timestamp = datetime.now() + timedelta(microseconds=idx)
                else:
                    message_timestamp = datetime.now() + timedelta(microseconds=idx)

                new_message = Message(
                    thread_id=cloned_thread.id,
                    role=msg.role,
                    content=msg.content,
                    created_at=message_timestamp,
                )
                session.add(new_message)
                messages_cloned += 1

        # Clone datasets and queries (build query ID mapping for dashboard HTML)
        datasets_cloned = 0
        queries_cloned = 0
        query_id_map: dict[str, str] = {}  # old_id -> new_id
        connection_warnings = []

        # Get original notebook to access dataset associations
        result = await session.execute(
            select(Notebook)
            .where(Notebook.id == notebook_id)
            .options(selectinload(Notebook.notebook_datasets).selectinload(NotebookDataset.dataset))
        )
        original = result.scalar_one_or_none()

        # Build mapping from export dataset name to original dataset
        original_datasets_by_name: dict[str, tuple] = {}  # name -> (dataset, connection)
        if original:
            for nb_dataset in original.notebook_datasets:
                ds = nb_dataset.dataset
                if ds.type == "connection" and ds.connection_id:
                    # Fetch connection to get its name
                    conn_result = await session.execute(select(Connection).where(Connection.id == ds.connection_id))
                    conn = conn_result.scalar_one_or_none()
                    if conn:
                        original_datasets_by_name[conn.name] = (ds, conn)
                elif ds.name:
                    original_datasets_by_name[ds.name] = (ds, None)

        # Iterate through export datasets and create proper associations
        for exported_dataset in export.datasets:
            dataset_id_to_use = None

            # Find matching original dataset by name
            mapping = original_datasets_by_name.get(exported_dataset.original_name)

            if not mapping:
                connection_warnings.append(f"Dataset '{exported_dataset.original_name}' not found in source notebook")
                continue

            original_dataset, connection = mapping

            if original_dataset.type == "connection" and original_dataset.connection_id:
                # Verify connection still exists
                if not connection:
                    connection_warnings.append(f"Connection for '{exported_dataset.original_name}' not accessible")
                    continue

                # Use DatasetService to create/reuse dataset (handles deduplication)
                try:
                    dataset = await DatasetService.create_dataset(
                        session=session,
                        type="connection",
                        connection_id=str(original_dataset.connection_id),
                        notebook_id=str(cloned_notebook.id),
                        name=exported_dataset.original_name,
                    )
                    dataset_id_to_use = str(dataset.id)
                except ValueError as e:
                    connection_warnings.append(f"Failed to create dataset for '{exported_dataset.original_name}': {e}")
                    continue
            else:
                # File-based dataset - link to existing dataset
                new_nb_dataset = NotebookDataset(
                    notebook_id=cloned_notebook.id,
                    dataset_id=original_dataset.id,
                )
                session.add(new_nb_dataset)
                dataset_id_to_use = str(original_dataset.id)

            datasets_cloned += 1

            # Create queries for THIS specific exported dataset's queries
            for exported_query in exported_dataset.queries:
                old_query_id = str(exported_query.id)

                new_query = Query(
                    tenant_id=tenant_id,
                    created_by=user_id,
                    name=exported_query.name,
                    query=exported_query.query,
                    output_schema=exported_query.output_schema or "",
                    dataset_id=dataset_id_to_use,
                    notebook_id=cloned_notebook.id,
                )
                session.add(new_query)
                await session.flush()

                query_id_map[old_query_id] = str(new_query.id)
                queries_cloned += 1

        # Clone dashboards with query ID replacement in HTML
        dashboards_cloned = 0
        for dashboard in export.dashboards:
            html_content = dashboard.html_content

            # Replace old query IDs with new ones in the HTML
            for old_id, new_id in query_id_map.items():
                html_content = re.sub(rf"\b{re.escape(old_id)}\b", new_id, html_content)

            new_dashboard = Dashboard(
                tenant_id=tenant_id,
                notebook_id=cloned_notebook.id,
                version_num=dashboard.version,
                html_content=html_content,
            )
            session.add(new_dashboard)
            dashboards_cloned += 1

        await session.commit()
        await session.refresh(cloned_notebook)

        session_items_cloned = 0
        try:
            # Get session data from original notebook
            original_session = await create_agent_session(str(notebook_id))
            session_items = await original_session.get_items()

            # If there are session items, clone them to the new notebook's session
            if session_items:
                cloned_session = await create_agent_session(str(cloned_notebook.id))
                await cloned_session.add_items(session_items)
                session_items_cloned = len(session_items)
                logger.info(f"Cloned {session_items_cloned} session items from {notebook_id} to {cloned_notebook.id}")
        except Exception as e:
            logger.warning(f"Failed to clone session data for notebook {notebook_id}: {str(e)}", exc_info=True)

        logger.info(
            f"User {user_id} cloned notebook {notebook_id} to {cloned_notebook.id}: "
            f"{messages_cloned} msgs, {queries_cloned} queries, {dashboards_cloned} dashboards, {datasets_cloned} datasets, {session_items_cloned} session items"
        )

        return {
            "notebook_id": cloned_notebook.id,
            "notebook_name": cloned_notebook.notebook_name,
            "messages_cloned": messages_cloned,
            "queries_cloned": queries_cloned,
            "dashboards_cloned": dashboards_cloned,
            "datasets_cloned": datasets_cloned,
            "session_items_cloned": session_items_cloned,
            "connection_access_warnings": connection_warnings if connection_warnings else None,
        }

    # ==================== Access Check Helpers ====================

    @staticmethod
    async def can_access_notebook_via_folder(
        notebook_id: UUID,
        user_id: UUID,
        session: AsyncSession,
    ) -> bool:
        """Check if user can access a notebook through folder membership."""
        # Get all folders this notebook is shared to
        folder_notebook_repo = FolderNotebookRepository(session)
        folder_notebooks = await folder_notebook_repo.list_by_notebook(notebook_id)

        if not folder_notebooks:
            return False

        # Check if user is a member of any of these folders
        member_repo = FolderMemberRepository(session)
        for fn in folder_notebooks:
            if await member_repo.is_member(fn.folder_id, user_id):
                return True

        return False

    @staticmethod
    async def can_access_dashboard_via_folder(
        dashboard_id: UUID,
        user_id: UUID,
        session: AsyncSession,
    ) -> bool:
        """Check if user can access a dashboard.

        Access is granted if:
        - Dashboard is shared to a public folder
        - User is a member of a folder the dashboard is shared to
        """
        folder_dashboard_repo = FolderDashboardRepository(session)
        folder_dashboards = await folder_dashboard_repo.list_by_dashboard(dashboard_id)

        if not folder_dashboards:
            return False

        folder_repo = FolderRepository(session)
        member_repo = FolderMemberRepository(session)

        for fd in folder_dashboards:
            # Check if folder is public
            folder = await folder_repo.get(fd.folder_id)
            if folder and folder.is_public:
                return True
            # Check if user is a member
            if await member_repo.is_member(fd.folder_id, user_id):
                return True

        return False

    # ==================== Viewer-Specific Methods ====================

    @staticmethod
    async def list_dashboards_for_viewer(
        user_id: UUID,
        tenant_id: UUID,
        session: AsyncSession,
    ) -> list[dict]:
        """Get all dashboards accessible to a viewer through folder membership.

        Returns a flat list of dashboards (no folder hierarchy) with dashboard details.
        Deduplicates if same dashboard is shared to multiple folders.
        """

        member_repo = FolderMemberRepository(session)
        folder_dashboard_repo = FolderDashboardRepository(session)

        # Get all folders the user is a member of
        memberships = await member_repo.list_by_user(user_id)

        accessible_dashboards = []
        seen_dashboard_ids: set[UUID] = set()

        for membership in memberships:
            # Get all dashboards shared to this folder
            folder_dashboards = await folder_dashboard_repo.list_by_folder(membership.folder_id)

            for fd in folder_dashboards:
                # Skip if we've already added this dashboard (dedup)
                if fd.dashboard_id in seen_dashboard_ids:
                    continue

                seen_dashboard_ids.add(fd.dashboard_id)

                # Get folder name
                folder = await FolderRepository(session).get(membership.folder_id)
                folder_name = folder.name if folder else None

                # Build dashboard info
                dashboard_info = {
                    "id": str(fd.dashboard_id),
                    "folder_id": str(fd.folder_id),
                    "folder_name": folder_name,
                    "version": fd.dashboard.version_num if fd.dashboard else None,
                    "notebook_id": str(fd.dashboard.notebook_id) if fd.dashboard else None,
                    "notebook_name": fd.dashboard.notebook.notebook_name
                    if fd.dashboard and fd.dashboard.notebook
                    else None,
                    "shared_by": str(fd.shared_by) if fd.shared_by else None,
                    "shared_at": fd.created_at.isoformat() if fd.created_at else None,
                }
                accessible_dashboards.append(dashboard_info)

        return accessible_dashboards

    @staticmethod
    async def list_all_accessible_dashboards(
        user_id: UUID,
        tenant_id: UUID,
        session: AsyncSession,
    ) -> dict:
        """Get all dashboards accessible to a user, grouped by folder.

        Returns folders user is member of + public folders, with their dashboards.

        Returns:
            {
                "folders": [
                    {
                        "folder_id": "...",
                        "folder_name": "...",
                        "is_public": True/False,
                        "dashboards": [...]
                    }
                ],
                "total_dashboards": N
            }
        """
        folder_repo = FolderRepository(session)
        folder_dashboard_repo = FolderDashboardRepository(session)

        # Get accessible folders - membership or public only
        folders = await folder_repo.list_by_user_membership_or_public(user_id, tenant_id)

        result_folders = []
        total_dashboards = 0

        for folder in folders:
            # Get dashboards shared to this folder
            folder_dashboards = await folder_dashboard_repo.list_by_folder(folder.id)

            dashboards = []
            for fd in folder_dashboards:
                dashboard_info = {
                    "id": str(fd.dashboard_id),
                    "notebook_name": fd.dashboard.notebook.notebook_name
                    if fd.dashboard and fd.dashboard.notebook
                    else None,
                    "version": fd.dashboard.version_num if fd.dashboard else None,
                    "shared_by": str(fd.shared_by) if fd.shared_by else None,
                    "shared_at": fd.created_at.isoformat() if fd.created_at else None,
                    "notebook_id": str(fd.dashboard.notebook_id) if fd.dashboard else None,
                    "notebook_created_by": str(fd.dashboard.notebook.created_by)
                    if fd.dashboard and fd.dashboard.notebook
                    else None,
                    "folder_id": str(folder.id),
                }
                dashboards.append(dashboard_info)

            # Only include folders that have dashboards
            if dashboards:
                result_folders.append(
                    {
                        "folder_id": str(folder.id),
                        "folder_name": folder.name,
                        "is_public": folder.is_public,
                        "dashboards": dashboards,
                    }
                )
                total_dashboards += len(dashboards)

        return {
            "folders": result_folders,
            "total_dashboards": total_dashboards,
        }

    @staticmethod
    async def list_all_accessible_notebooks(
        user_id: UUID,
        tenant_id: UUID,
        session: AsyncSession,
    ) -> dict:
        """Get all notebooks accessible to a user, grouped by folder.

        Returns folders user is member of + public folders, with their shared notebooks.

        Returns:
            {
                "folders": [
                    {
                        "folder_id": "...",
                        "folder_name": "...",
                        "is_public": True/False,
                        "notebooks": [...]
                    }
                ],
                "total_notebooks": N
            }
        """
        folder_repo = FolderRepository(session)
        folder_notebook_repo = FolderNotebookRepository(session)

        # Get accessible folders - membership or public only
        folders = await folder_repo.list_by_user_membership_or_public(user_id, tenant_id)

        result_folders = []
        total_notebooks = 0

        for folder in folders:
            # Get notebooks shared to this folder
            folder_notebooks = await folder_notebook_repo.list_by_folder(folder.id)

            notebooks = []
            for fn in folder_notebooks:
                notebook_info = {
                    "id": str(fn.notebook_id),
                    "notebook_name": fn.notebook.notebook_name if fn.notebook else None,
                    "description": fn.notebook.description if fn.notebook else None,
                    "shared_by": str(fn.shared_by) if fn.shared_by else None,
                    "shared_at": fn.created_at.isoformat() if fn.created_at else None,
                    "is_snapshot": fn.is_snapshot,
                }
                notebooks.append(notebook_info)

            # Only include folders that have notebooks
            if notebooks:
                result_folders.append(
                    {
                        "folder_id": str(folder.id),
                        "folder_name": folder.name,
                        "is_public": folder.is_public,
                        "notebooks": notebooks,
                    }
                )
                total_notebooks += len(notebooks)

        return {
            "folders": result_folders,
            "total_notebooks": total_notebooks,
        }
