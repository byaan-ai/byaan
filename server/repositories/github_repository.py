from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from server.models.github_repository import GitHubRepository


class GitHubRepoRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self,
        tenant_id: UUID,
        user_id: UUID,
        repo_full_name: str,
        default_branch: str,
        source: str = "github",
        local_path: str | None = None,
    ) -> GitHubRepository:
        repo = GitHubRepository(
            tenant_id=tenant_id,
            user_id=user_id,
            repo_full_name=repo_full_name,
            default_branch=default_branch,
            source=source,
            local_path=local_path,
        )
        self._session.add(repo)
        await self._session.commit()
        stmt = (
            select(GitHubRepository)
            .where(GitHubRepository.id == repo.id)
            .options(selectinload(GitHubRepository.skills))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def get(self, repo_id: UUID, tenant_id: UUID) -> GitHubRepository | None:
        stmt = (
            select(GitHubRepository)
            .where(GitHubRepository.id == repo_id, GitHubRepository.tenant_id == tenant_id)
            .options(selectinload(GitHubRepository.skills))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_user(self, tenant_id: UUID, user_id: UUID) -> list[GitHubRepository]:
        stmt = (
            select(GitHubRepository)
            .where(
                GitHubRepository.tenant_id == tenant_id,
                GitHubRepository.user_id == user_id,
                GitHubRepository.is_active.is_(True),
            )
            .options(selectinload(GitHubRepository.skills))
            .order_by(GitHubRepository.updated_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_analysis_status(
        self,
        repo_id: UUID,
        status: str,
        error: str | None = None,
        sha: str | None = None,
        language_breakdown: dict | None = None,
    ) -> None:
        stmt = select(GitHubRepository).where(GitHubRepository.id == repo_id)
        result = await self._session.execute(stmt)
        repo = result.scalar_one_or_none()
        if not repo:
            return
        repo.analysis_status = status
        repo.analysis_error = error
        if sha:
            repo.last_analyzed_sha = sha
        if language_breakdown is not None:
            repo.language_breakdown = json.dumps(language_breakdown)
        await self._session.commit()

    async def delete(self, repo_id: UUID, tenant_id: UUID) -> bool:
        repo = await self.get(repo_id, tenant_id)
        if not repo:
            return False
        await self._session.delete(repo)
        await self._session.commit()
        return True

    async def get_by_repo_name(self, tenant_id: UUID, repo_full_name: str) -> GitHubRepository | None:
        stmt = select(GitHubRepository).where(
            GitHubRepository.tenant_id == tenant_id,
            GitHubRepository.repo_full_name == repo_full_name,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_user_and_source(self, tenant_id: UUID, user_id: UUID, source: str) -> list[GitHubRepository]:
        stmt = (
            select(GitHubRepository)
            .where(
                GitHubRepository.tenant_id == tenant_id,
                GitHubRepository.source == source,
                GitHubRepository.is_active.is_(True),
                or_(
                    GitHubRepository.user_id == user_id,
                    GitHubRepository.scope == "org",
                ),
            )
            .options(selectinload(GitHubRepository.skills))
            .order_by(GitHubRepository.updated_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_org_accessible(self, tenant_id: UUID, source: str) -> list[GitHubRepository]:
        """Get all active org-scoped repositories for a tenant (no user required)."""
        stmt = (
            select(GitHubRepository)
            .where(
                GitHubRepository.tenant_id == tenant_id,
                GitHubRepository.source == source,
                GitHubRepository.scope == "org",
                GitHubRepository.is_active.is_(True),
            )
            .options(selectinload(GitHubRepository.skills))
            .order_by(GitHubRepository.updated_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def set_scope(self, repo_id: UUID, tenant_id: UUID, user_id: UUID, scope: str) -> GitHubRepository | None:
        """Update scope ('user' or 'org'). Only the connecting user may change it."""
        if scope not in ("user", "org"):
            return None
        repo = await self.get(repo_id, tenant_id)
        if not repo:
            return None
        if repo.user_id != user_id:
            return None
        repo.scope = scope
        await self._session.commit()
        await self._session.refresh(repo, ["skills"])
        return repo

    async def get_by_local_path(self, tenant_id: UUID, local_path: str) -> GitHubRepository | None:
        stmt = select(GitHubRepository).where(
            GitHubRepository.tenant_id == tenant_id,
            GitHubRepository.source == "local",
            GitHubRepository.local_path == local_path,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
