from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.custom_skill import CustomSkill
from server.models.skill_version import SkillVersion


class SkillVersionRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def _next_version(self, skill_id: UUID) -> int:
        query = select(func.max(SkillVersion.version)).where(SkillVersion.skill_id == skill_id)
        result = await self._session.execute(query)
        current_max = result.scalar_one_or_none()
        return (current_max or 0) + 1

    async def snapshot_skill(
        self,
        skill: CustomSkill,
        changed_by: str,
        suggestion_id: UUID | None = None,
    ) -> SkillVersion:
        """Persist the current state of ``skill`` as a new version row.

        Must be called BEFORE mutating the live skill. Lazy v1: the first snapshot
        of a skill captures its pre-change state as version 1.
        """
        version = await self._next_version(skill.id)
        snapshot = SkillVersion(
            skill_id=skill.id,
            version=version,
            name=skill.name,
            description=skill.description,
            instructions=skill.instructions,
            changed_by=changed_by,
            suggestion_id=suggestion_id,
        )
        self._session.add(snapshot)
        await self._session.flush()
        return snapshot

    async def list_for_skill(self, skill_id: UUID) -> list[SkillVersion]:
        query = select(SkillVersion).where(SkillVersion.skill_id == skill_id).order_by(SkillVersion.version.desc())
        result = await self._session.execute(query)
        return list(result.scalars().all())
