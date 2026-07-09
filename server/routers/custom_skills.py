from __future__ import annotations

from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, require_scope
from server.auth.scopes import Scope
from server.db.session import get_async_session
from server.repositories.custom_skill import CustomSkillRepository
from server.repositories.skill_version import SkillVersionRepository
from server.schemas.custom_skills import (
    CustomSkillCreate,
    CustomSkillDomainToggleRequest,
    CustomSkillListItem,
    CustomSkillResponse,
    CustomSkillUpdate,
)
from server.schemas.skill_suggestions import SkillVersionResponse
from server.schemas.standard_response import success_response
from server.services.crypto_service import CryptoService
from server.utils.custom_logger import get_logger
from server.utils.deployment import is_feature_enabled

logger = get_logger(__name__)

router = APIRouter()


def _to_response(skill, include_instructions: bool = True) -> dict:
    creator_name = ""
    if skill.creator:
        creator_name = skill.creator.full_name or skill.creator.email.split("@")[0]

    github_repo_name = None
    if skill.github_repo_id and skill.github_repository:
        github_repo_name = skill.github_repository.repo_full_name

    common = {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "scope": skill.scope,
        "skill_type": skill.skill_type,
        "is_active": skill.is_active,
        "created_by": skill.created_by,
        "created_by_name": creator_name,
        "created_at": skill.created_at,
        "updated_at": skill.updated_at,
        "can_execute_api": skill.can_execute_api,
        "api_base_url": skill.api_base_url,
        "api_type": skill.api_type,
        "api_auth_type": skill.api_auth_type,
        "api_domain": skill.api_domain,
        "domain_active": skill.domain_active,
        "has_credentials": bool(skill.api_credentials_encrypted),
        "github_repo_id": skill.github_repo_id,
        "github_analysis_type": skill.github_analysis_type,
        "github_repo_name": github_repo_name,
    }

    if include_instructions:
        return CustomSkillResponse(instructions=skill.instructions, **common).model_dump()
    else:
        return CustomSkillListItem(**common).model_dump()


async def _process_api_config(payload_api_config, existing_skill, session) -> dict:
    """Process API config from payload and return fields to set on the skill."""
    api_fields = {}
    api_fields["api_base_url"] = payload_api_config.api_base_url
    api_fields["api_type"] = payload_api_config.api_type
    api_fields["api_auth_type"] = payload_api_config.api_auth_type

    domain = payload_api_config.api_domain
    if not domain:
        parsed = urlparse(payload_api_config.api_base_url)
        domain = parsed.netloc or ""
    api_fields["api_domain"] = domain

    api_key = payload_api_config.api_key
    if api_key:
        encrypted = await CryptoService.encrypt_config({"api_key": api_key}, session)
        api_fields["api_credentials_encrypted"] = encrypted
    elif existing_skill and existing_skill.api_credentials_encrypted:
        api_fields["api_credentials_encrypted"] = existing_skill.api_credentials_encrypted

    return api_fields


@router.get("/custom-skills")
async def list_custom_skills(
    auth: AuthContext = Depends(require_scope(Scope.USER_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    """List all accessible custom skills (personal + team shared)."""
    repo = CustomSkillRepository(session)
    skills = await repo.list_accessible(auth.tenant_id, auth.user_id)
    return success_response(
        data=[_to_response(skill, include_instructions=True) for skill in skills],
        message="Custom skills retrieved",
    )


@router.get("/custom-skills/{skill_id}")
async def get_custom_skill(
    skill_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.USER_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get a single custom skill with full content."""
    repo = CustomSkillRepository(session)
    skill = await repo.get(skill_id, auth.tenant_id)

    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom skill not found")

    if skill.scope == "user" and skill.created_by != auth.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    return success_response(data=_to_response(skill), message="Custom skill retrieved")


@router.get("/custom-skills/{skill_id}/versions")
async def list_custom_skill_versions(
    skill_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.USER_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    """List the version history (snapshots) for a custom skill."""
    repo = CustomSkillRepository(session)
    skill = await repo.get(skill_id, auth.tenant_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom skill not found")

    if skill.scope == "user" and skill.created_by != auth.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    versions = await SkillVersionRepository(session).list_for_skill(skill_id)
    return success_response(
        data=[SkillVersionResponse.model_validate(v).model_dump() for v in versions],
        message="Skill versions retrieved",
    )


@router.post("/custom-skills")
async def create_custom_skill(
    payload: CustomSkillCreate,
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Create a new custom skill."""
    if payload.scope == "org" and not is_feature_enabled("team_sharing_enabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Team sharing is not available in this deployment mode",
        )
    repo = CustomSkillRepository(session)

    api_kwargs = {}
    if payload.api_config:
        api_kwargs = await _process_api_config(payload.api_config, None, session)

    skill = await repo.create(
        tenant_id=auth.tenant_id,
        created_by=auth.user_id,
        name=payload.name,
        description=payload.description,
        instructions=payload.instructions,
        scope=payload.scope,
        skill_type=payload.skill_type,
        **api_kwargs,
    )

    return success_response(data=_to_response(skill), message="Custom skill created")


@router.put("/custom-skills/{skill_id}")
async def update_custom_skill(
    skill_id: UUID,
    payload: CustomSkillUpdate,
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Update a custom skill (only the creator can update)."""
    repo = CustomSkillRepository(session)

    existing = await repo.get(skill_id, auth.tenant_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom skill not found")

    if existing.created_by != auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the creator can update this skill",
        )

    updates = payload.model_dump(exclude_unset=True, exclude={"api_config", "remove_api_config"})

    if payload.remove_api_config:
        updates["api_base_url"] = None
        updates["api_type"] = None
        updates["api_auth_type"] = None
        updates["api_domain"] = None
        updates["api_credentials_encrypted"] = None
        updates["domain_active"] = True
    elif payload.api_config:
        api_fields = await _process_api_config(payload.api_config, existing, session)
        updates.update(api_fields)

    if not updates:
        return success_response(data=_to_response(existing), message="No changes made")

    skill = await repo.update(skill_id, auth.tenant_id, auth.user_id, **updates)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom skill not found")

    return success_response(data=_to_response(skill), message="Custom skill updated")


@router.delete("/custom-skills/{skill_id}")
async def delete_custom_skill(
    skill_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete a custom skill (only the creator can delete)."""
    repo = CustomSkillRepository(session)

    existing = await repo.get(skill_id, auth.tenant_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom skill not found")

    if existing.created_by != auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the creator can delete this skill",
        )

    deleted = await repo.delete(skill_id, auth.tenant_id, auth.user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom skill not found")

    return success_response(message="Custom skill deleted")


@router.patch("/custom-skills/{skill_id}/domain-toggle")
async def toggle_custom_skill_domain(
    skill_id: UUID,
    payload: CustomSkillDomainToggleRequest,
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Toggle the domain whitelist for an API-enabled custom skill."""
    repo = CustomSkillRepository(session)

    existing = await repo.get(skill_id, auth.tenant_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom skill not found")

    if existing.created_by != auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the creator can toggle this skill's domain",
        )

    if not existing.can_execute_api:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This skill does not have API configuration",
        )

    skill = await repo.update(skill_id, auth.tenant_id, auth.user_id, domain_active=payload.active)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom skill not found")

    return success_response(data=_to_response(skill), message="Domain toggle updated")


@router.post("/custom-skills/{skill_id}/share")
async def share_custom_skill(
    skill_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Share a personal custom skill with the team."""
    if not is_feature_enabled("team_sharing_enabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Team sharing is not available in this deployment mode",
        )
    repo = CustomSkillRepository(session)

    existing = await repo.get(skill_id, auth.tenant_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom skill not found")

    if existing.created_by != auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the creator can share this skill",
        )

    if existing.scope == "org":
        return success_response(data=_to_response(existing), message="Skill is already shared with team")

    skill = await repo.share_with_org(skill_id, auth.tenant_id, auth.user_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to share skill")

    return success_response(data=_to_response(skill), message="Custom skill shared with team")


@router.post("/custom-skills/{skill_id}/unshare")
async def unshare_custom_skill(
    skill_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Remove team sharing from a custom skill (make it personal again)."""
    if not is_feature_enabled("team_sharing_enabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Team sharing is not available in this deployment mode",
        )
    repo = CustomSkillRepository(session)

    existing = await repo.get(skill_id, auth.tenant_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom skill not found")

    if existing.created_by != auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the creator can unshare this skill",
        )

    if existing.scope == "user":
        return success_response(data=_to_response(existing), message="Skill is already personal")

    skill = await repo.unshare_from_org(skill_id, auth.tenant_id, auth.user_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to unshare skill")

    return success_response(data=_to_response(skill), message="Custom skill is now personal")
