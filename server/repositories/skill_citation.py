from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.skill_citation import SkillCitation

_CITATION_FIELDS = (
    "repo_id",
    "path",
    "start_line",
    "end_line",
    "blob_sha",
    "commit_sha",
    "snippet_hash",
    "snippet",
    "claim_key",
    "status",
)


class SkillCitationRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def bulk_replace_for_skill(self, skill_id: UUID, citations: list[dict]) -> list[SkillCitation]:
        """Atomically replace all citations for a skill with the provided set."""
        await self._session.execute(delete(SkillCitation).where(SkillCitation.skill_id == skill_id))

        rows: list[SkillCitation] = []
        for citation in citations:
            row = SkillCitation(skill_id=skill_id, **{k: citation[k] for k in _CITATION_FIELDS if k in citation})
            self._session.add(row)
            rows.append(row)

        await self._session.commit()
        for row in rows:
            await self._session.refresh(row)
        return rows

    async def list_for_repo_paths(self, repo_id: UUID, paths: list[str]) -> list[SkillCitation]:
        """Citations for a repo whose path is in the changed set."""
        if not paths:
            return []
        query = select(SkillCitation).where(
            SkillCitation.repo_id == repo_id,
            SkillCitation.path.in_(paths),
        )
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def list_for_skill(self, skill_id: UUID) -> list[SkillCitation]:
        query = select(SkillCitation).where(SkillCitation.skill_id == skill_id).order_by(SkillCitation.path)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def update_status(self, citation_id: UUID, status: str) -> SkillCitation | None:
        citation = await self._session.get(SkillCitation, citation_id)
        if not citation:
            return None
        citation.status = status
        await self._session.commit()
        await self._session.refresh(citation)
        return citation

    async def save(self, citation: SkillCitation) -> SkillCitation:
        self._session.add(citation)
        await self._session.commit()
        await self._session.refresh(citation)
        return citation

    async def stats_for_repo(self, repo_id: UUID) -> dict[str, int]:
        """Health metrics for a repo: total citations and unresolved count."""
        total_query = select(func.count()).select_from(SkillCitation).where(SkillCitation.repo_id == repo_id)
        unresolved_query = (
            select(func.count())
            .select_from(SkillCitation)
            .where(SkillCitation.repo_id == repo_id, SkillCitation.status == "unresolved")
        )
        total = (await self._session.execute(total_query)).scalar_one()
        unresolved = (await self._session.execute(unresolved_query)).scalar_one()
        return {"total": total, "unresolved": unresolved}
