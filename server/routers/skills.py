from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, require_scope
from server.auth.scopes import Scope
from server.db.session import get_async_session
from server.models.user import User
from server.repositories.skill_credentials import SkillCredentialRepository
from server.schemas.skills import (
    CredentialFieldSchema,
    DomainToggleRequest,
    SkillCredentialCreate,
    SkillCredentialResponse,
    SkillScope,
    SkillStatusResponse,
)
from server.schemas.standard_response import success_response
from server.services.crypto_service import CryptoService
from server.services.skill_discovery import SkillDiscovery
from server.utils.custom_logger import get_logger
from server.utils.deployment import is_feature_enabled

logger = get_logger(__name__)

router = APIRouter()


def _get_effective_owner(credential) -> UUID | None:
    """Get the effective owner of a credential, handling legacy NULL created_by."""
    if credential.created_by:
        return credential.created_by
    if credential.scope == "user":
        return credential.user_id
    return None


def _validate_skill_name(skill_name: str) -> None:
    """Validate that skill_name is a discovered skill."""
    if not SkillDiscovery.is_valid_skill(skill_name):
        available = SkillDiscovery.get_skill_names()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown skill: {skill_name}. Available: {available}",
        )


async def _get_user_names(session: AsyncSession, user_ids: set[UUID]) -> dict[UUID, str]:
    """Fetch user names for given user IDs."""
    if not user_ids:
        return {}
    result = await session.execute(select(User.id, User.full_name, User.email).where(User.id.in_(user_ids)))
    return {row.id: row.full_name or row.email.split("@")[0] for row in result.all()}


@router.get("/skills")
async def list_skills(
    auth: AuthContext = Depends(require_scope(Scope.USER_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    """List all available skills with their configuration status."""
    repo = SkillCredentialRepository(session)
    user_credentials = await repo.get_all_for_tenant_user(auth.tenant_id, auth.user_id)

    configured_scopes: dict[str, list[SkillScope]] = {}
    for cred in user_credentials:
        configured_scopes.setdefault(cred.skill_name, []).append(SkillScope(cred.scope))

    created_by_map = await repo.get_created_by_map(auth.tenant_id, auth.user_id)

    org_creator_ids = {v["org"] for v in created_by_map.values() if v.get("org")}
    user_names = await _get_user_names(session, org_creator_ids)

    domain_state: dict[str, bool] = {}
    for cred in user_credentials:
        decrypted = await repo.get_decrypted_credentials(cred)
        if not decrypted:
            continue
        domain_active = decrypted.get("domain_active", decrypted.get("subdomain_active", True))
        if cred.skill_name not in domain_state or cred.scope == "user":
            domain_state[cred.skill_name] = domain_active

    all_skills = SkillDiscovery.discover_all()

    skills = [
        SkillStatusResponse(
            skill_name=config.name,
            display_name=config.display_name,
            description=config.description,
            is_configured=config.name in configured_scopes,
            required_credentials=config.required_credentials,
            credential_fields=[
                CredentialFieldSchema(
                    key=c.key,
                    label=c.label,
                    placeholder=c.placeholder,
                    help=c.help,
                    optional=c.optional,
                    type=c.type,
                    options=c.options,
                    default=c.default,
                    depends_on=c.depends_on,
                )
                for c in config.credentials
            ],
            emoji=config.emoji,
            homepage=config.homepage,
            domain=config.api.domain,
            scopes_configured=configured_scopes.get(config.name, []),
            user_scope_created_by=created_by_map.get(config.name, {}).get("user"),
            org_scope_created_by=created_by_map.get(config.name, {}).get("org"),
            org_scope_created_by_name=user_names.get(created_by_map.get(config.name, {}).get("org")),
            domain_active=domain_state.get(config.name, True),
        ).model_dump()
        for config in all_skills.values()
    ]

    return success_response(data=skills, message="Skills retrieved")


@router.post("/skills/{skill_name}/credentials")
async def save_skill_credentials(
    skill_name: str,
    payload: SkillCredentialCreate,
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Save or update credentials for a skill."""
    if payload.scope == SkillScope.ORG and not is_feature_enabled("team_sharing_enabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Team sharing is not available in this deployment mode",
        )
    _validate_skill_name(skill_name)
    config = SkillDiscovery.get_skill_config(skill_name)

    # For org scope, user_id should be None
    user_id = auth.user_id if payload.scope == SkillScope.USER else None

    repo = SkillCredentialRepository(session)

    existing = await repo.get_by_skill(skill_name, auth.tenant_id, user_id, payload.scope.value)
    if existing:
        effective_owner = _get_effective_owner(existing)
        if effective_owner and effective_owner != auth.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the user who added these credentials can update them",
            )
        decrypted = await repo.get_decrypted_credentials(existing)
        if decrypted:
            payload.credentials = {**decrypted, **payload.credentials}

    credential = await repo.upsert(
        skill_name=skill_name,
        credentials=payload.credentials,
        tenant_id=auth.tenant_id,
        user_id=user_id,
        scope=payload.scope.value,
        created_by=auth.user_id,
    )

    scope_label = "organization" if payload.scope == SkillScope.ORG else "personal"

    return success_response(
        data=SkillCredentialResponse(
            id=credential.id,
            skill_name=credential.skill_name,
            scope=credential.scope,
            is_configured=True,
            created_at=credential.created_at,
            updated_at=credential.updated_at,
        ).model_dump(),
        message=f"{config.display_name} credentials saved ({scope_label})",
    )


@router.delete("/skills/{skill_name}/credentials")
async def delete_skill_credentials(
    skill_name: str,
    scope: SkillScope = Query(default=SkillScope.USER, description="Scope to delete: 'user' or 'org'"),
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete credentials for a skill."""
    _validate_skill_name(skill_name)
    config = SkillDiscovery.get_skill_config(skill_name)

    # For org scope, user_id should be None
    user_id = auth.user_id if scope == SkillScope.USER else None

    repo = SkillCredentialRepository(session)
    existing = await repo.get_by_skill(skill_name, auth.tenant_id, user_id, scope.value)

    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credentials not found")

    effective_owner = _get_effective_owner(existing)
    if effective_owner and effective_owner != auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the user who added these credentials can delete them",
        )

    await repo.delete_by_skill(skill_name, auth.tenant_id, user_id, scope.value)

    scope_label = "organization" if scope == SkillScope.ORG else "personal"
    return success_response(message=f"{config.display_name} credentials deleted ({scope_label})")


@router.post("/skills/{skill_name}/share")
async def share_skill_with_team(
    skill_name: str,
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Share user's skill credentials with the team (copy to org scope)."""
    if not is_feature_enabled("team_sharing_enabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Team sharing is not available in this deployment mode",
        )
    _validate_skill_name(skill_name)
    config = SkillDiscovery.get_skill_config(skill_name)

    repo = SkillCredentialRepository(session)

    user_cred = await repo.get_by_skill(skill_name, auth.tenant_id, auth.user_id, "user")
    if not user_cred:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No personal credentials found for {config.display_name} to share",
        )

    effective_owner = _get_effective_owner(user_cred)
    if effective_owner and effective_owner != auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the user who added these credentials can share them",
        )

    credential = await repo.share_to_org(skill_name, auth.tenant_id, auth.user_id)

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to share {config.display_name} credentials",
        )

    return success_response(
        data=SkillCredentialResponse(
            id=credential.id,
            skill_name=credential.skill_name,
            scope=credential.scope,
            is_configured=True,
            created_at=credential.created_at,
            updated_at=credential.updated_at,
        ).model_dump(),
        message=f"{config.display_name} shared with team",
    )


@router.patch("/skills/{skill_name}/domain-toggle")
async def toggle_skill_domain(
    skill_name: str,
    payload: DomainToggleRequest,
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Toggle domain whitelisting for a skill's credentials."""
    _validate_skill_name(skill_name)
    config = SkillDiscovery.get_skill_config(skill_name)

    user_id = auth.user_id if payload.scope == SkillScope.USER else None
    repo = SkillCredentialRepository(session)
    existing = await repo.get_by_skill(skill_name, auth.tenant_id, user_id, payload.scope.value)

    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credentials not found")

    effective_owner = _get_effective_owner(existing)
    if effective_owner and effective_owner != auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the credential owner can toggle domain whitelisting",
        )

    decrypted = await repo.get_decrypted_credentials(existing)
    if not decrypted:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to decrypt credentials")

    decrypted["domain_active"] = payload.active
    decrypted.pop("subdomain_active", None)
    existing.credentials_encrypted = await CryptoService.encrypt_config(decrypted, session)
    await session.commit()

    state_label = "enabled" if payload.active else "disabled"
    return success_response(message=f"{config.display_name} domain whitelisting {state_label}")
