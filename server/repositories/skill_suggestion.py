from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.skill_suggestion import SkillSuggestion


class SkillSuggestionRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self,
        tenant_id: UUID,
        suggestion_type: str,
        title: str,
        rationale: str,
        confidence: str,
        skill_id: UUID | None = None,
        evidence: dict | None = None,
        patch: dict | None = None,
        proposed_instructions: str | None = None,
        source: dict | None = None,
        slack_channel_id: str | None = None,
        slack_message_ts: str | None = None,
    ) -> SkillSuggestion:
        suggestion = SkillSuggestion(
            tenant_id=tenant_id,
            skill_id=skill_id,
            suggestion_type=suggestion_type,
            title=title,
            rationale=rationale,
            confidence=confidence,
            status="pending",
            evidence=evidence,
            patch=patch,
            proposed_instructions=proposed_instructions,
            source=source,
            slack_channel_id=slack_channel_id,
            slack_message_ts=slack_message_ts,
        )
        self._session.add(suggestion)
        await self._session.commit()
        await self._session.refresh(suggestion)
        return suggestion

    async def get(self, suggestion_id: UUID, tenant_id: UUID) -> SkillSuggestion | None:
        query = (
            select(SkillSuggestion)
            .where(SkillSuggestion.id == suggestion_id)
            .where(SkillSuggestion.tenant_id == tenant_id)
        )
        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    async def list_by_tenant(self, tenant_id: UUID, status: str | None = None) -> list[SkillSuggestion]:
        query = select(SkillSuggestion).where(SkillSuggestion.tenant_id == tenant_id)
        if status:
            query = query.where(SkillSuggestion.status == status)
        query = query.order_by(SkillSuggestion.created_at.desc())
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def list_pending_clarifications(self, tenant_id: UUID) -> list[SkillSuggestion]:
        query = (
            select(SkillSuggestion)
            .where(SkillSuggestion.tenant_id == tenant_id)
            .where(SkillSuggestion.suggestion_type == "clarification")
            .where(SkillSuggestion.status == "pending")
            .order_by(SkillSuggestion.created_at.asc())
        )
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def resolve_pending_clarification(self, suggestion_id: UUID, tenant_id: UUID, note: str) -> bool:
        """Atomically supersede a pending clarification.

        Conditional on status='pending' so it loses cleanly to a concurrent human
        review claim ('reviewing') or another resolver. Caller commits.
        """
        result = await self._session.execute(
            update(SkillSuggestion)
            .where(SkillSuggestion.id == suggestion_id)
            .where(SkillSuggestion.tenant_id == tenant_id)
            .where(SkillSuggestion.status == "pending")
            .values(
                status="superseded",
                reviewed_via="skill_loop",
                reviewed_at=datetime.now(),
                review_note=note,
            )
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    async def count_pending(self, tenant_id: UUID) -> int:
        query = (
            select(func.count())
            .select_from(SkillSuggestion)
            .where(SkillSuggestion.tenant_id == tenant_id)
            .where(SkillSuggestion.status == "pending")
        )
        result = await self._session.execute(query)
        return int(result.scalar_one() or 0)

    async def list_open_for_skill(self, skill_id: UUID, section: str | None = None) -> list[SkillSuggestion]:
        query = (
            select(SkillSuggestion)
            .where(SkillSuggestion.skill_id == skill_id)
            .where(SkillSuggestion.status == "pending")
            .order_by(SkillSuggestion.created_at.asc())
        )
        result = await self._session.execute(query)
        suggestions = list(result.scalars().all())
        if section is None:
            return suggestions
        return [s for s in suggestions if (s.patch or {}).get("section") == section]

    async def supersede_open_for_skill(
        self,
        skill_id: UUID,
        section: str | None,
        exclude_id: UUID | None = None,
    ) -> int:
        """Mark older pending suggestions targeting the same skill + patch section as superseded."""
        superseded = 0
        for suggestion in await self.list_open_for_skill(skill_id, section):
            if exclude_id is not None and suggestion.id == exclude_id:
                continue
            suggestion.status = "superseded"
            superseded += 1
        if superseded:
            await self._session.flush()
        return superseded

    async def claim_for_review(self, suggestion_id: UUID, tenant_id: UUID) -> bool:
        """Atomically move a pending suggestion to the transient 'reviewing' state.

        Returns True only for the caller that won the claim, preventing two concurrent
        approvals/rejections (e.g. Slack + app) from both proceeding.
        """
        result = await self._session.execute(
            update(SkillSuggestion)
            .where(SkillSuggestion.id == suggestion_id)
            .where(SkillSuggestion.tenant_id == tenant_id)
            .where(SkillSuggestion.status == "pending")
            .values(status="reviewing")
            .execution_options(synchronize_session=False)
        )
        await self._session.commit()
        return result.rowcount == 1

    async def restore_to_pending(self, suggestion_id: UUID, tenant_id: UUID) -> None:
        """Release a claim back to 'pending' after an apply fails mid-flight."""
        await self._session.rollback()
        await self._session.execute(
            update(SkillSuggestion)
            .where(SkillSuggestion.id == suggestion_id)
            .where(SkillSuggestion.tenant_id == tenant_id)
            .where(SkillSuggestion.status == "reviewing")
            .values(status="pending")
            .execution_options(synchronize_session=False)
        )
        await self._session.commit()

    async def save(self, suggestion: SkillSuggestion) -> SkillSuggestion:
        self._session.add(suggestion)
        await self._session.commit()
        await self._session.refresh(suggestion)
        return suggestion
