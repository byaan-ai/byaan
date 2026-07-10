from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.skill_loop_settings import SkillLoopSettings
from server.utils.config_loader import get_skill_loop_config


class SkillLoopSettingsRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, tenant_id: UUID) -> SkillLoopSettings | None:
        result = await self._session.execute(select(SkillLoopSettings).where(SkillLoopSettings.tenant_id == tenant_id))
        return result.scalar_one_or_none()

    async def get_or_defaults(self, tenant_id: UUID) -> SkillLoopSettings:
        """Return the tenant's settings row, or an unsaved defaults instance (never persists on read)."""
        existing = await self.get(tenant_id)
        if existing is not None:
            return existing
        return SkillLoopSettings(
            tenant_id=tenant_id,
            enabled=True,
            digest_enabled=True,
            digest_hour=int(get_skill_loop_config()["digest_hour"]),
        )

    async def upsert(self, tenant_id: UUID, **fields) -> SkillLoopSettings:
        settings = await self.get(tenant_id)
        if settings is None:
            settings = SkillLoopSettings(
                tenant_id=tenant_id,
                enabled=True,
                digest_enabled=True,
                digest_hour=int(get_skill_loop_config()["digest_hour"]),
            )
            self._session.add(settings)
        for key, value in fields.items():
            if value is not None and hasattr(settings, key):
                setattr(settings, key, value)
        await self._session.commit()
        await self._session.refresh(settings)
        return settings
