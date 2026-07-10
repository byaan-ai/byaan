from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, require_scope
from server.auth.scopes import Scope
from server.db.session import get_async_session
from server.models.custom_skill import CustomSkill
from server.models.skill_suggestion import SkillSuggestion
from server.repositories.skill_suggestion import SkillSuggestionRepository
from server.schemas.skill_suggestions import (
    SkillSuggestionApproveRequest,
    SkillSuggestionRejectRequest,
    SkillSuggestionResponse,
)
from server.schemas.standard_response import success_response
from server.services.skill_suggestion_service import SkillSuggestionService
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

router = APIRouter()


async def _skill_names(session: AsyncSession, suggestions: list[SkillSuggestion]) -> dict[UUID, str]:
    skill_ids = {s.skill_id for s in suggestions if s.skill_id is not None}
    if not skill_ids:
        return {}
    result = await session.execute(select(CustomSkill.id, CustomSkill.name).where(CustomSkill.id.in_(skill_ids)))
    return {row.id: row.name for row in result.all()}


def _to_response(suggestion: SkillSuggestion, skill_names: dict[UUID, str]) -> dict:
    data = SkillSuggestionResponse.model_validate(suggestion)
    data.skill_name = skill_names.get(suggestion.skill_id) if suggestion.skill_id else None
    if data.status == "reviewing":
        data.status = "pending"
    return data.model_dump()


@router.get("/skill-suggestions")
async def list_skill_suggestions(
    status: str | None = None,
    auth: AuthContext = Depends(require_scope(Scope.USER_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    repo = SkillSuggestionRepository(session)
    suggestions = await repo.list_by_tenant(auth.tenant_id, status=status)
    skill_names = await _skill_names(session, suggestions)
    return success_response(
        data=[_to_response(s, skill_names) for s in suggestions],
        message="Skill suggestions retrieved",
    )


@router.get("/skill-suggestions/pending-count")
async def get_pending_count(
    auth: AuthContext = Depends(require_scope(Scope.USER_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    repo = SkillSuggestionRepository(session)
    count = await repo.count_pending(auth.tenant_id)
    return success_response(data={"count": count}, message="Pending suggestion count retrieved")


@router.get("/skill-suggestions/{suggestion_id}")
async def get_skill_suggestion(
    suggestion_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.USER_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    repo = SkillSuggestionRepository(session)
    suggestion = await repo.get(suggestion_id, auth.tenant_id)
    if not suggestion:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill suggestion not found")
    skill_names = await _skill_names(session, [suggestion])
    return success_response(data=_to_response(suggestion, skill_names), message="Skill suggestion retrieved")


@router.post("/skill-suggestions/{suggestion_id}/approve")
async def approve_skill_suggestion(
    suggestion_id: UUID,
    payload: SkillSuggestionApproveRequest,
    auth: AuthContext = Depends(require_scope(Scope.TENANT_MANAGE_ROLES)),
    session: AsyncSession = Depends(get_async_session),
):
    service = SkillSuggestionService(session)
    try:
        suggestion, new_version = await service.approve(
            suggestion_id,
            auth.tenant_id,
            reviewed_by=auth.user_id,
            reviewed_via="app",
            final_instructions=payload.final_instructions,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    skill_names = await _skill_names(session, [suggestion])
    data = _to_response(suggestion, skill_names)
    data["new_version"] = new_version
    return success_response(data=data, message="Skill suggestion approved")


@router.post("/skill-suggestions/{suggestion_id}/reject")
async def reject_skill_suggestion(
    suggestion_id: UUID,
    payload: SkillSuggestionRejectRequest,
    auth: AuthContext = Depends(require_scope(Scope.TENANT_MANAGE_ROLES)),
    session: AsyncSession = Depends(get_async_session),
):
    service = SkillSuggestionService(session)
    try:
        suggestion = await service.reject(
            suggestion_id,
            auth.tenant_id,
            payload.reason,
            reviewed_by=auth.user_id,
            reviewed_via="app",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    skill_names = await _skill_names(session, [suggestion])
    return success_response(data=_to_response(suggestion, skill_names), message="Skill suggestion rejected")
