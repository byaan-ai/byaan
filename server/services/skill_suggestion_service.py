from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.skill_suggestion import SkillSuggestion
from server.models.tenant import Tenant
from server.repositories.custom_skill import CustomSkillRepository
from server.repositories.skill_suggestion import SkillSuggestionRepository
from server.repositories.skill_version import SkillVersionRepository
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

MAX_PENDING_PER_SKILL = 5


class SkillSuggestionService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = SkillSuggestionRepository(session)
        self._skill_repo = CustomSkillRepository(session)
        self._version_repo = SkillVersionRepository(session)

    def _patch_section(self, patch: dict | None) -> str | None:
        if not patch:
            return None
        return patch.get("section")

    async def create_suggestion(
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
        section = self._patch_section(patch)
        if skill_id is not None and section is not None:
            await self._repo.supersede_open_for_skill(skill_id, section)

        suggestion = await self._repo.create(
            tenant_id=tenant_id,
            suggestion_type=suggestion_type,
            title=title,
            rationale=rationale,
            confidence=confidence,
            skill_id=skill_id,
            evidence=evidence,
            patch=patch,
            proposed_instructions=proposed_instructions,
            source=source,
            slack_channel_id=slack_channel_id,
            slack_message_ts=slack_message_ts,
        )

        if skill_id is not None:
            pending = await self._repo.list_open_for_skill(skill_id)
            overflow = len(pending) - MAX_PENDING_PER_SKILL
            for stale in pending[:overflow]:
                if stale.id == suggestion.id:
                    continue
                stale.status = "superseded"
            if overflow > 0:
                await self._session.commit()
                await self._session.refresh(suggestion)

        return suggestion

    async def approve(
        self,
        suggestion_id: UUID,
        tenant_id: UUID,
        *,
        reviewed_by: UUID | None = None,
        reviewed_via: str,
        reviewer_slack_user_id: str | None = None,
        reviewer_display_name: str | None = None,
        final_instructions: str | None = None,
    ) -> tuple[SkillSuggestion, int | None]:
        suggestion = await self._repo.get(suggestion_id, tenant_id)
        if not suggestion:
            raise ValueError("Suggestion not found")
        if not await self._repo.claim_for_review(suggestion_id, tenant_id):
            raise ValueError(f"Suggestion is not pending (current status: {suggestion.status})")

        try:
            new_version: int | None = None

            if suggestion.suggestion_type == "edit":
                if not suggestion.skill_id:
                    raise ValueError("Edit suggestion has no target skill")
                skill = await self._skill_repo.get(suggestion.skill_id, tenant_id)
                if not skill:
                    raise ValueError("Target skill not found")
                instructions = final_instructions or suggestion.proposed_instructions
                if not instructions:
                    raise ValueError("No instructions to apply for edit suggestion")
                snapshot = await self._version_repo.snapshot_skill(
                    skill, changed_by="loop", suggestion_id=suggestion.id
                )
                new_version = snapshot.version
                skill.instructions = instructions
            elif suggestion.suggestion_type == "new_skill":
                if not suggestion.proposed_instructions:
                    raise ValueError("New skill suggestion has no proposed instructions")
                owner_id = await self._tenant_owner_id(tenant_id)
                skill = await self._skill_repo.create(
                    tenant_id=tenant_id,
                    created_by=owner_id,
                    name=suggestion.title[:100],
                    description=(suggestion.rationale or suggestion.title)[:500],
                    instructions=suggestion.proposed_instructions,
                    scope="org",
                    skill_type="general",
                )
                snapshot = await self._version_repo.snapshot_skill(
                    skill, changed_by="loop", suggestion_id=suggestion.id
                )
                new_version = snapshot.version
                suggestion.skill_id = skill.id

            self._stamp_review(
                suggestion,
                status="applied",
                reviewed_by=reviewed_by,
                reviewed_via=reviewed_via,
                reviewer_slack_user_id=reviewer_slack_user_id,
                reviewer_display_name=reviewer_display_name,
            )

            await self._session.commit()
        except Exception:
            await self._repo.restore_to_pending(suggestion_id, tenant_id)
            raise

        await self._session.refresh(suggestion)
        return suggestion, new_version

    async def reject(
        self,
        suggestion_id: UUID,
        tenant_id: UUID,
        reason: str,
        *,
        reviewed_by: UUID | None = None,
        reviewed_via: str,
        reviewer_slack_user_id: str | None = None,
        reviewer_display_name: str | None = None,
    ) -> SkillSuggestion:
        suggestion = await self._repo.get(suggestion_id, tenant_id)
        if not suggestion:
            raise ValueError("Suggestion not found")
        if not await self._repo.claim_for_review(suggestion_id, tenant_id):
            raise ValueError(f"Suggestion is not pending (current status: {suggestion.status})")

        try:
            self._stamp_review(
                suggestion,
                status="rejected",
                reviewed_by=reviewed_by,
                reviewed_via=reviewed_via,
                reviewer_slack_user_id=reviewer_slack_user_id,
                reviewer_display_name=reviewer_display_name,
                review_note=reason,
            )

            await self._session.commit()
        except Exception:
            await self._repo.restore_to_pending(suggestion_id, tenant_id)
            raise

        await self._session.refresh(suggestion)
        return suggestion

    def _stamp_review(
        self,
        suggestion: SkillSuggestion,
        *,
        status: str,
        reviewed_by: UUID | None,
        reviewed_via: str,
        reviewer_slack_user_id: str | None,
        reviewer_display_name: str | None,
        review_note: str | None = None,
    ) -> None:
        suggestion.status = status
        suggestion.reviewed_by = reviewed_by
        suggestion.reviewed_via = reviewed_via
        suggestion.reviewer_slack_user_id = reviewer_slack_user_id
        suggestion.reviewer_display_name = reviewer_display_name
        suggestion.reviewed_at = datetime.now()
        if review_note is not None:
            suggestion.review_note = review_note

    async def _tenant_owner_id(self, tenant_id: UUID) -> UUID:
        result = await self._session.execute(select(Tenant.owner_id).where(Tenant.id == tenant_id))
        owner_id = result.scalar_one_or_none()
        if not owner_id:
            raise ValueError("Tenant owner not found")
        return owner_id
