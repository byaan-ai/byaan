from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from server.models.custom_skill import CustomSkill
from server.models.user import User
from server.repositories.skill_version import SkillVersionRepository

_CONTENT_FIELDS = ("name", "description", "instructions")


class CustomSkillRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self,
        tenant_id: UUID,
        created_by: UUID,
        name: str,
        description: str,
        instructions: str,
        scope: str = "user",
        skill_type: str = "general",
        api_base_url: str | None = None,
        api_type: str | None = None,
        api_auth_type: str | None = None,
        api_domain: str | None = None,
        api_credentials_encrypted: str | None = None,
        github_repo_id: UUID | None = None,
        github_analysis_type: str | None = None,
    ) -> CustomSkill:
        skill = CustomSkill(
            tenant_id=tenant_id,
            created_by=created_by,
            name=name,
            description=description,
            instructions=instructions,
            scope=scope,
            skill_type=skill_type,
            is_active=True,
            api_base_url=api_base_url,
            api_type=api_type,
            api_auth_type=api_auth_type,
            api_domain=api_domain,
            api_credentials_encrypted=api_credentials_encrypted,
            github_repo_id=github_repo_id,
            github_analysis_type=github_analysis_type,
        )
        self._session.add(skill)
        await self._session.commit()
        await self._session.refresh(skill, ["creator"])
        return skill

    async def get(self, skill_id: UUID, tenant_id: UUID) -> CustomSkill | None:
        query = (
            select(CustomSkill)
            .options(selectinload(CustomSkill.creator), selectinload(CustomSkill.github_repository))
            .where(CustomSkill.id == skill_id)
            .where(CustomSkill.tenant_id == tenant_id)
        )
        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    async def list_accessible(self, tenant_id: UUID, user_id: UUID) -> list[CustomSkill]:
        query = (
            select(CustomSkill)
            .options(selectinload(CustomSkill.creator), selectinload(CustomSkill.github_repository))
            .where(CustomSkill.tenant_id == tenant_id)
            .where(CustomSkill.is_active == True)  # noqa: E712
            .where(
                or_(
                    CustomSkill.scope == "org",
                    CustomSkill.created_by == user_id,
                )
            )
            .order_by(CustomSkill.created_at.desc())
        )
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def list_org_accessible(self, tenant_id: UUID) -> list[CustomSkill]:
        """Get all active org-scoped custom skills for a tenant (no user required)."""
        query = (
            select(CustomSkill)
            .options(selectinload(CustomSkill.creator))
            .where(CustomSkill.tenant_id == tenant_id)
            .where(CustomSkill.is_active == True)  # noqa: E712
            .where(CustomSkill.scope == "org")
            .order_by(CustomSkill.created_at.desc())
        )
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def update(
        self,
        skill_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
        **updates,
    ) -> CustomSkill | None:
        skill = await self.get(skill_id, tenant_id)
        if not skill:
            return None
        if skill.created_by != user_id:
            return None

        if any(field in updates and getattr(skill, field) != updates[field] for field in _CONTENT_FIELDS):
            await SkillVersionRepository(self._session).snapshot_skill(skill, changed_by="user")

        for key, value in updates.items():
            if hasattr(skill, key) and key not in ("id", "tenant_id", "created_by", "created_at"):
                setattr(skill, key, value)

        await self._session.commit()
        await self._session.refresh(skill, ["creator"])
        return skill

    async def delete(self, skill_id: UUID, tenant_id: UUID, user_id: UUID) -> bool:
        skill = await self.get(skill_id, tenant_id)
        if not skill:
            return False
        if skill.created_by != user_id:
            return False

        await self._session.delete(skill)
        await self._session.commit()
        return True

    async def share_with_org(self, skill_id: UUID, tenant_id: UUID, user_id: UUID) -> CustomSkill | None:
        return await self.update(skill_id, tenant_id, user_id, scope="org")

    async def unshare_from_org(self, skill_id: UUID, tenant_id: UUID, user_id: UUID) -> CustomSkill | None:
        return await self.update(skill_id, tenant_id, user_id, scope="user")

    async def search(self, tenant_id: UUID, user_id: UUID, query: str) -> list[CustomSkill]:
        all_skills = await self.list_accessible(tenant_id, user_id)
        if not query:
            return all_skills

        query_lower = query.lower()
        return [
            skill
            for skill in all_skills
            if query_lower in skill.name.lower()
            or query_lower in skill.description.lower()
            or query_lower in skill.instructions.lower()
        ]

    async def get_creator_names(self, skill_ids: list[UUID]) -> dict[UUID, str]:
        if not skill_ids:
            return {}
        query = (
            select(CustomSkill.id, User.full_name, User.email)
            .join(User, CustomSkill.created_by == User.id)
            .where(CustomSkill.id.in_(skill_ids))
        )
        result = await self._session.execute(query)
        return {row.id: row.full_name or row.email.split("@")[0] for row in result.all()}

    async def get_by_type(self, tenant_id: UUID, skill_type: str) -> list[CustomSkill]:
        """Get all active org-scoped custom skills of a specific type for a tenant."""
        query = (
            select(CustomSkill)
            .options(selectinload(CustomSkill.creator))
            .where(CustomSkill.tenant_id == tenant_id)
            .where(CustomSkill.skill_type == skill_type)
            .where(CustomSkill.is_active == True)  # noqa: E712
            .where(CustomSkill.scope == "org")
            .order_by(CustomSkill.created_at.desc())
        )
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def upsert_github_skill(
        self,
        tenant_id: UUID,
        created_by: UUID,
        github_repo_id: UUID,
        github_analysis_type: str,
        name: str,
        description: str,
        instructions: str,
    ) -> CustomSkill:
        query = select(CustomSkill).where(
            CustomSkill.github_repo_id == github_repo_id,
            CustomSkill.github_analysis_type == github_analysis_type,
        )
        result = await self._session.execute(query)
        existing = result.scalar_one_or_none()

        if existing:
            updates = {"name": name, "description": description, "instructions": instructions}
            if any(getattr(existing, field) != updates[field] for field in _CONTENT_FIELDS):
                await SkillVersionRepository(self._session).snapshot_skill(existing, changed_by="loop")
                for field, value in updates.items():
                    setattr(existing, field, value)
                await self._session.commit()
            await self._session.refresh(existing, ["creator"])
            return existing

        return await self.create(
            tenant_id=tenant_id,
            created_by=created_by,
            name=name,
            description=description,
            instructions=instructions,
            scope="user",
            skill_type="github_analysis",
            github_repo_id=github_repo_id,
            github_analysis_type=github_analysis_type,
        )

    async def list_by_github_repo(self, github_repo_id: UUID) -> list[CustomSkill]:
        query = (
            select(CustomSkill)
            .where(CustomSkill.github_repo_id == github_repo_id)
            .order_by(CustomSkill.github_analysis_type, CustomSkill.name)
        )
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def delete_by_github_repo(self, github_repo_id: UUID) -> int:
        skills = await self.list_by_github_repo(github_repo_id)
        count = len(skills)
        for skill in skills:
            await self._session.delete(skill)
        if count:
            await self._session.commit()
        return count
