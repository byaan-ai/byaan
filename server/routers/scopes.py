"""
Scopes router - Returns user permission scopes based on their role in a tenant.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, _auth_context_dependency
from server.auth.scopes import get_scopes_for_role
from server.auth.tenant_context import set_tenant_id
from server.db.session import AsyncSessionFactory, get_async_session
from server.models.tenant import Tenant
from server.models.tenant_member import TenantMember, TenantRole
from server.schemas.standard_response import success_response
from server.services.settings import SettingsService
from server.utils.config_loader import get_waitlist_config, is_self_hosted
from server.utils.custom_logger import get_logger
from server.utils.deployment import get_feature_flags
from server.utils.seed_notebook import seed_demo_notebooks_for_user

logger = get_logger(__name__)
router = APIRouter()


class ScopesResponse(BaseModel):
    """Response model for the scopes endpoint."""

    scopes: list[str]
    role: str
    tenant_id: str
    tenant_name: str
    features: dict[str, bool] | None = None


class CreateTenantRequest(BaseModel):
    """Request model for creating a new tenant/workspace."""

    name: str = Field(..., min_length=1, max_length=100, description="Workspace name")


@router.get("/scopes")
async def get_user_scopes(
    tenant_id: UUID,
    auth: AuthContext = Depends(_auth_context_dependency),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get the permission scopes for the current user based on their role in the specified tenant.

    Args:
        tenant_id: UUID of the tenant to get scopes for

    Returns:
        ScopesResponse with list of scopes, role, tenant_id, and tenant_name

    Raises:
        403: User is not a member of this tenant
        404: Tenant not found
    """
    try:
        user = auth.user

        # Get the tenant
        tenant_result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = tenant_result.scalar_one_or_none()

        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found",
            )

            if is_self_hosted():
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Self-hosted instance requires team features.",
                )
            role = TenantRole.OWNER
            scopes = get_scopes_for_role(role)
            response = ScopesResponse(
                scopes=scopes,
                role=role.value,
                tenant_id=str(tenant_id),
                tenant_name=tenant.name,
                features=get_feature_flags(),
            )
            return success_response(
                data=response.model_dump(),
                message="Scopes retrieved successfully",
            )

        # Check if user is owner of the tenant first
        if tenant.owner_id == user.id:
            role = TenantRole.OWNER
        else:
            # Get the user's membership in this tenant
            membership_result = await session.execute(
                select(TenantMember).where(
                    TenantMember.tenant_id == tenant_id,
                    TenantMember.user_id == user.id,
                )
            )
            membership = membership_result.scalar_one_or_none()

            if not membership:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User is not a member of this tenant",
                )

            role = TenantRole(membership.role)

        scopes = get_scopes_for_role(role)

        response = ScopesResponse(
            scopes=scopes,
            role=role.value,
            tenant_id=str(tenant_id),
            tenant_name=tenant.name,
            features=get_feature_flags(),
        )

        return success_response(
            data=response.model_dump(),
            message="Scopes retrieved successfully",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_user_scopes: {str(e)}",
            posthog_context={"function": "get_user_scopes", "tenant_id": str(tenant_id), "user_id": str(user.id)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve scopes",
        )


@router.get("/scopes/all")
async def get_all_user_scopes(
    auth: AuthContext = Depends(_auth_context_dependency),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Get the permission scopes for the current user across all tenants they belong to.

    Returns:
        List of ScopesResponse for each tenant the user is a member/owner of
    """
    try:
        user = auth.user

        if not is_self_hosted():
            tenant_result = await session.execute(select(Tenant).where(Tenant.id == auth.tenant_id))
            tenant = tenant_result.scalar_one_or_none()

            if tenant:
                scopes = get_scopes_for_role(TenantRole.OWNER)
                tenant_scopes = [
                    ScopesResponse(
                        scopes=scopes,
                        role=TenantRole.OWNER.value,
                        tenant_id=str(tenant.id),
                        tenant_name=tenant.name,
                        features=get_feature_flags(),
                    ).model_dump()
                ]
                return success_response(
                    data={"tenants": tenant_scopes},
                    message="All scopes retrieved successfully",
                )

        tenant_scopes = []
        seen_tenant_ids = set()

        # First, get tenants where the user is the owner
        owned_tenants_result = await session.execute(select(Tenant).where(Tenant.owner_id == user.id))
        owned_tenants = owned_tenants_result.scalars().all()

        for tenant in owned_tenants:
            seen_tenant_ids.add(tenant.id)
            scopes = get_scopes_for_role(TenantRole.OWNER)

            tenant_scopes.append(
                ScopesResponse(
                    scopes=scopes,
                    role=TenantRole.OWNER.value,
                    tenant_id=str(tenant.id),
                    tenant_name=tenant.name,
                    features=get_feature_flags(),
                ).model_dump()
            )

        # Then get all tenant memberships for this user (excluding already seen tenants)
        memberships_result = await session.execute(
            select(TenantMember, Tenant)
            .join(Tenant, TenantMember.tenant_id == Tenant.id)
            .where(TenantMember.user_id == user.id)
        )
        memberships = memberships_result.all()

        for membership, tenant in memberships:
            # Skip if already added as owner
            if tenant.id in seen_tenant_ids:
                continue

            role = TenantRole(membership.role)
            scopes = get_scopes_for_role(role)

            tenant_scopes.append(
                ScopesResponse(
                    scopes=scopes,
                    role=role.value,
                    tenant_id=str(tenant.id),
                    tenant_name=tenant.name,
                    features=get_feature_flags(),
                ).model_dump()
            )

        return success_response(
            data={"tenants": tenant_scopes},
            message="All scopes retrieved successfully",
        )

    except Exception as e:
        logger.error(
            f"Unexpected error in get_all_user_scopes: {str(e)}",
            posthog_context={"function": "get_all_user_scopes", "user_id": str(user.id)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve scopes",
        )


def _generate_slug(name: str, email: str) -> str:
    """Generate a URL-safe slug from workspace name and email."""
    # Try to use workspace name first
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        # Fallback to email username
        username = email.split("@")[0]
        slug = re.sub(r"[^a-z0-9]+", "-", username.lower()).strip("-")
    return slug


@router.post("/tenants")
async def create_tenant(
    payload: CreateTenantRequest,
    auth: AuthContext = Depends(_auth_context_dependency),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Create a new personal tenant/workspace for the current user.

    This endpoint is called during onboarding when a user provides their workspace name.
    Note: In local mode, this is not typically called as the default tenant is used.
    """
    try:
        user = auth.user

        # In self-hosted mode, only superusers (master admin) can create tenants
        if is_self_hosted() and not user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Workspace creation is disabled. Contact your administrator.",
            )

        # Check if user already has a personal tenant
        existing_personal = await session.execute(
            select(Tenant).where(Tenant.owner_id == user.id, Tenant.is_personal.is_(True))
        )
        if existing_personal.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User already has a personal workspace",
            )

        # Generate unique slug
        base_slug = _generate_slug(payload.name, user.email)
        slug = base_slug
        counter = 1
        while True:
            existing = await session.execute(select(Tenant).where(Tenant.slug == slug))
            if not existing.scalar_one_or_none():
                break
            slug = f"{base_slug}-{counter}"
            counter += 1

        # Create tenant
        tenant = Tenant(
            name=payload.name,
            slug=slug,
            owner_id=user.id,
            is_personal=True,
        )
        session.add(tenant)
        await session.flush()

        # Create membership with OWNER role
        member = TenantMember(
            user_id=user.id,
            tenant_id=tenant.id,
            role=TenantRole.OWNER.value,
            joined_at=datetime.now(UTC),
        )
        session.add(member)
        await session.flush()  # Flush to get tenant.id before calling worker

        worker_url = get_waitlist_config().get("worker_url")
        if worker_url:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{worker_url}/api/keys/register",
                        json={"email": user.email},
                        timeout=10.0,
                    )
                    response.raise_for_status()
                    data = response.json()
                    api_key = data.get("api_key")

                if not api_key:
                    raise ValueError("Worker did not return API key")

                set_tenant_id(tenant.id)

                await SettingsService.upsert_setting(
                    session=session,
                    setting_key="api_key",
                    setting_value=api_key,
                    description="API key for Cloudflare worker authentication",
                    is_encrypted=False,
                )

                logger.info(f"Created personal tenant '{tenant.slug}' for user {user.email} with API key")

            except Exception as e:
                logger.error(f"Failed to register API key with worker: {str(e)}")
                await session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to register API key: {str(e)}",
                )
        else:
            logger.info(
                f"Created personal tenant '{tenant.slug}' for user {user.email} without worker API key "
                "(WORKER_URL not configured)"
            )

        await session.commit()

        # Seed demo notebooks in background (after commit)
        tenant_id_copy = tenant.id
        user_id_copy = user.id

        async def seed_in_background():
            try:
                async with AsyncSessionFactory() as seed_session:
                    await seed_demo_notebooks_for_user(seed_session, user_id_copy, tenant_id_copy)
            except Exception as e:
                logger.error(f"Failed to seed demo notebooks for user {user_id_copy}: {e}")

        asyncio.create_task(seed_in_background())

        # Return tenant info with scopes
        scopes = get_scopes_for_role(TenantRole.OWNER)
        response = ScopesResponse(
            scopes=scopes,
            role=TenantRole.OWNER.value,
            tenant_id=str(tenant.id),
            tenant_name=tenant.name,
            features=get_feature_flags(),
        )

        return success_response(
            data=response.model_dump(),
            message="Workspace created successfully",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in create_tenant: {str(e)}",
            posthog_context={"function": "create_tenant", "user_id": str(user.id)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create workspace",
        )
