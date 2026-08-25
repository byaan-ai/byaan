"""Repository for SlackWorkspace operations."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from server.models.slack_workspace import SlackWorkspace


class SlackWorkspaceRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_team_id(self, team_id: str) -> SlackWorkspace | None:
        """Get workspace by Slack team ID."""
        query = (
            select(SlackWorkspace)
            .options(selectinload(SlackWorkspace.default_llm_connection))
            .where(SlackWorkspace.slack_team_id == team_id)
        )
        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_tenant(self, tenant_id: UUID) -> SlackWorkspace | None:
        """Get workspace by tenant ID."""
        query = (
            select(SlackWorkspace)
            .options(selectinload(SlackWorkspace.default_llm_connection))
            .where(SlackWorkspace.tenant_id == tenant_id)
        )
        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    async def create(
        self,
        tenant_id: UUID,
        slack_team_id: str,
        slack_team_name: str | None,
        bot_token_encrypted: str,
        bot_user_id: str | None,
        signing_secret_encrypted: str,
        default_llm_connection_id: UUID | None = None,
        default_model: str | None = None,
        installed_by: UUID | None = None,
    ) -> SlackWorkspace:
        """Create a new Slack workspace."""
        workspace = SlackWorkspace(
            tenant_id=tenant_id,
            slack_team_id=slack_team_id,
            slack_team_name=slack_team_name,
            bot_token_encrypted=bot_token_encrypted,
            bot_user_id=bot_user_id,
            signing_secret_encrypted=signing_secret_encrypted,
            default_llm_connection_id=default_llm_connection_id,
            default_model=default_model,
            installed_by=installed_by,
        )
        self._session.add(workspace)
        await self._session.commit()
        await self._session.refresh(workspace)
        return workspace

    async def update(self, workspace_id: UUID, **updates) -> SlackWorkspace | None:
        """Update a Slack workspace."""
        query = select(SlackWorkspace).where(SlackWorkspace.id == workspace_id)
        result = await self._session.execute(query)
        workspace = result.scalar_one_or_none()

        if not workspace:
            return None

        for key, value in updates.items():
            if hasattr(workspace, key) and key not in ("id", "tenant_id", "created_at"):
                setattr(workspace, key, value)

        await self._session.commit()
        await self._session.refresh(workspace)
        return workspace

    async def delete(self, workspace_id: UUID) -> bool:
        """Delete a Slack workspace."""
        query = select(SlackWorkspace).where(SlackWorkspace.id == workspace_id)
        result = await self._session.execute(query)
        workspace = result.scalar_one_or_none()

        if not workspace:
            return False

        await self._session.delete(workspace)
        await self._session.commit()
        return True
