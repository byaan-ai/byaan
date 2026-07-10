"""
Waitlist Service - HTTP client for Cloudflare Worker
Handles all waitlist operations including multi-user support for local DMG mode
"""

import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.tenant_context import set_tenant_id
from server.models.connections import Connection
from server.models.datasets import Dataset
from server.models.llm_connections import LLMConnection
from server.models.notebooks import Notebook
from server.models.queries import Query
from server.models.tenant import Tenant
from server.models.tenant_member import TenantMember, TenantRole
from server.models.user import User
from server.services.posthog_service import PostHogService
from server.services.settings import SettingsService
from server.utils.config_loader import get_waitlist_config
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

DEFAULT_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


class WaitlistService:
    """Service for managing waitlist via Cloudflare Worker.

    `base_url` is empty when WORKER_URL is not configured; callers must guard
    every network call so worker-backed features stay quietly disabled.
    """

    def __init__(self):
        config = get_waitlist_config()
        self.base_url = config.get("worker_url") or ""
        self.worker_url = self.base_url
        if not self.base_url:
            logger.info("WORKER_URL not configured; waitlist/credit features disabled.")

    async def join_waitlist(self, email: str, session: AsyncSession, name: str | None = None) -> dict:
        """
        One-shot registration: Worker saves email and returns full credentials,
        then we create the local User + Tenant + settings + LLM connection.
        Same path for new and returning users — Worker handles idempotency.
        """
        if not self.base_url:
            raise RuntimeError("WORKER_URL is not configured; waitlist signup is unavailable in this deployment.")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/api/waitlist/join",
                json={"email": email, "name": name},
                timeout=10.0,
            )
            response.raise_for_status()
            result = response.json()

        user_id_from_worker = result.get("userId")
        user_name = result.get("name") or name
        api_key = result.get("apiKey")
        openrouter_key = result.get("openrouterKey")
        has_openrouter_key = result.get("hasOpenRouterKey", False)
        has_credits = result.get("hasCredits", False)
        is_existing_user = result.get("isExistingUser", False)

        user = await self._get_or_create_local_user(
            email=email,
            user_id_from_worker=user_id_from_worker,
            name=user_name,
            session=session,
        )
        tenant = await self._create_personal_tenant_for_user(user, session)
        set_tenant_id(tenant.id)

        await SettingsService.upsert_setting(
            session=session,
            setting_key="user_id",
            setting_value=str(user.id),
            description="User's database ID",
            is_encrypted=False,
        )
        await SettingsService.upsert_setting(
            session=session,
            setting_key="user_name",
            setting_value=user_name if user_name else "",
            description="User's name",
            is_encrypted=False,
        )
        await SettingsService.upsert_setting(
            session=session,
            setting_key="user_email",
            setting_value=email,
            description="User's email",
            is_encrypted=False,
        )
        if api_key:
            await SettingsService.upsert_setting(
                session=session,
                setting_key="api_key",
                setting_value=api_key,
                description="User's API key",
                is_encrypted=True,
            )

        # Existing-user path: D1 has the OpenRouter key; fetch and use it
        if is_existing_user and not openrouter_key and has_openrouter_key and api_key:
            openrouter_key = await self.get_openrouter_key_from_d1(api_key)
            if openrouter_key:
                logger.info(f"OpenRouter key fetched from D1 for existing user {email}")
            else:
                logger.warning(f"OpenRouter key exists in D1 but fetch failed for {email}")

        if openrouter_key:
            await self._create_llm_connection(session, openrouter_key)
            has_credits = True
            logger.info(f"Created LLM connection for {email}")

        await SettingsService.upsert_setting(
            session=session,
            setting_key="has_credits",
            setting_value="true" if has_credits else "false",
            description="Whether OpenRouter API key exists",
            is_encrypted=False,
        )

        PostHogService.identify(
            distinct_id=str(user.id),
            properties={"email": email, "name": user_name},
        )
        PostHogService.capture_event(
            distinct_id=str(user.id),
            event="existing_user_login" if is_existing_user else "onboarding_completed",
            properties={"email": email},
        )

        await session.commit()

        logger.info(f"Registered {email} with tenant {tenant.slug}")

        # Seed demo notebooks in background (after commit so tenant/user exist)
        import asyncio

        from server.db.session import AsyncSessionFactory
        from server.utils.seed_notebook import seed_demo_notebooks_for_user

        user_id_copy = user.id
        tenant_id_copy = tenant.id

        async def seed_in_background():
            try:
                async with AsyncSessionFactory() as seed_session:
                    await seed_demo_notebooks_for_user(seed_session, user_id_copy, tenant_id_copy)
            except Exception as e:
                logger.error(f"Failed to seed demo notebooks for user {user_id_copy}: {e}", exc_info=True)

        asyncio.create_task(seed_in_background())

        return {
            "apiKey": api_key,
            "userId": str(user.id),
            "userName": user.full_name,
            "email": email,
            "tenantId": str(tenant.id),
            "tenantName": tenant.name,
            "hasCredits": has_credits,
            "openrouterKey": openrouter_key,
            "onboarded": True,
            "hasAccess": True,
        }

    async def _auto_login_existing_user(self, session: AsyncSession) -> dict | None:
        """Auto-login existing users when no tenant_id is provided (localStorage cleared, app update, etc.)."""
        # Try default tenant first (pre-multi-tenant upgrade path)
        credentials = await self._try_default_tenant_login(session)
        if credentials:
            return credentials

        # Fallback: find any personal tenant with a real owner
        result = await session.execute(
            select(Tenant)
            .where(Tenant.is_personal.is_(True), Tenant.owner_id != DEFAULT_USER_ID)
            .order_by(Tenant.created_at.desc())
            .limit(1)
        )
        tenant = result.scalar_one_or_none()
        if not tenant:
            return None

        user_result = await session.execute(select(User).where(User.id == tenant.owner_id))
        user = user_result.scalar_one_or_none()
        if not user:
            return None

        logger.info(f"Auto-login: Found personal tenant {tenant.slug} for user {user.email}")
        return await self._build_credentials_response(user, tenant, session)

    async def _try_default_tenant_login(self, session: AsyncSession) -> dict | None:
        """Try auto-login via the default tenant (pre-multi-tenant upgrade path)."""
        result = await session.execute(select(Tenant).where(Tenant.id == DEFAULT_TENANT_ID))
        tenant = result.scalar_one_or_none()
        if not tenant:
            return None

        notebook_result = await session.execute(
            select(func.count()).select_from(Notebook).where(Notebook.tenant_id == DEFAULT_TENANT_ID)
        )
        notebook_count = notebook_result.scalar()

        if notebook_count == 0:
            return None

        set_tenant_id(DEFAULT_TENANT_ID)
        email_setting = await SettingsService.get_setting_by_key(session, "user_email")

        email = (
            email_setting.setting_value if email_setting and email_setting.setting_value else "local-user@byaan.local"
        )

        if tenant.owner_id != DEFAULT_USER_ID:
            user_result = await session.execute(select(User).where(User.id == tenant.owner_id))
            user = user_result.scalar_one_or_none()
            if user:
                return await self._build_credentials_response(user, tenant, session)
            return None

        name_setting = await SettingsService.get_setting_by_key(session, "user_name")
        user_name = name_setting.setting_value if name_setting else None

        user = await self._get_or_create_local_user(
            email=email,
            user_id_from_worker=None,
            name=user_name,
            session=session,
        )

        tenant.owner_id = user.id
        tenant.is_personal = True
        tenant.name = self._generate_workspace_name(email)

        member = TenantMember(
            user_id=user.id,
            tenant_id=tenant.id,
            role=TenantRole.OWNER.value,
            joined_at=datetime.now(UTC),
        )
        session.add(member)

        await session.commit()

        await self._fix_created_by_for_existing_data(session, DEFAULT_TENANT_ID, user.id)

        logger.info(f"Auto-login: Created user {email} and assigned Default Workspace")

        return await self._build_credentials_response(user, tenant, session)

    async def _build_credentials_response(self, user: User, tenant: Tenant, session: AsyncSession) -> dict:
        """Build the credentials response dict."""
        set_tenant_id(tenant.id)

        api_key_setting = await SettingsService.get_setting_by_key(session, "api_key")
        has_credits_setting = await SettingsService.get_setting_by_key(session, "has_credits")

        has_credits = False
        if has_credits_setting and has_credits_setting.setting_value:
            has_credits = has_credits_setting.setting_value.lower() == "true"

        PostHogService.set_current_user_id(str(user.id))

        return {
            "userId": str(user.id),
            "userName": user.full_name,
            "email": user.email,
            "apiKey": api_key_setting.setting_value if api_key_setting else None,
            "hasCredits": has_credits,
            "tenantId": str(tenant.id),
            "tenantName": tenant.name,
        }

    async def _fix_created_by_for_existing_data(self, session: AsyncSession, tenant_id: UUID, user_id: UUID) -> None:
        """Fix created_by for all resources migrated from pre-multi-tenant version."""
        # Update notebooks
        await session.execute(
            update(Notebook)
            .where(Notebook.tenant_id == tenant_id, Notebook.created_by.is_(None))
            .values(created_by=user_id)
        )
        # Update connections
        await session.execute(
            update(Connection)
            .where(Connection.tenant_id == tenant_id, Connection.created_by.is_(None))
            .values(created_by=user_id)
        )
        # Update datasets
        await session.execute(
            update(Dataset)
            .where(Dataset.tenant_id == tenant_id, Dataset.created_by.is_(None))
            .values(created_by=user_id)
        )
        # Update queries
        await session.execute(
            update(Query).where(Query.tenant_id == tenant_id, Query.created_by.is_(None)).values(created_by=user_id)
        )
        # Update LLM connections
        await session.execute(
            update(LLMConnection)
            .where(LLMConnection.tenant_id == tenant_id, LLMConnection.created_by.is_(None))
            .values(created_by=user_id)
        )
        await session.commit()

    async def get_stored_credentials(self, tenant_id: UUID | None, session: AsyncSession) -> dict | None:
        """Retrieve stored credentials for a tenant, with auto-login for upgrade scenarios."""
        if not tenant_id:
            return await self._auto_login_existing_user(session)

        result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = result.scalar_one_or_none()
        if not tenant:
            return await self._auto_login_existing_user(session)

        if tenant.owner_id == DEFAULT_USER_ID:
            return await self._auto_login_existing_user(session)

        user_result = await session.execute(select(User).where(User.id == tenant.owner_id))
        user = user_result.scalar_one_or_none()
        if not user:
            return await self._auto_login_existing_user(session)

        # Fix any resources with null created_by (from pre-multi-tenant migration)
        await self._fix_created_by_for_existing_data(session, tenant_id, user.id)

        return await self._build_credentials_response(user, tenant, session)

    async def _create_llm_connection(self, session: AsyncSession, openrouter_api_key: str) -> None:
        """
        Create an LLM connection record for OpenRouter
        """
        try:
            from server.repositories.llm_connections import LLMConnectionRepository

            llm_repo = LLMConnectionRepository(session)

            openrouter_config = {
                "api_key": openrouter_api_key,
                "api_base": "https://openrouter.ai/api/v1",
                "site_url": "byaan-app",
                "app_name": "Byaan",
            }

            await llm_repo.create(
                {
                    "type": "openrouter",
                    "name": "Beta User OpenRouter",
                    "config_dict": openrouter_config,
                }
            )

            logger.info("Successfully created OpenRouter LLM connection")

        except Exception as e:
            logger.error(
                f"Failed to create LLM connection: {str(e)}",
                exc_info=True,
                posthog_context={"function": "_create_llm_connection"},
            )
            # Don't raise - we want onboarding to continue even if this fails

    async def get_openrouter_key_from_d1(self, api_key: str) -> str | None:
        """
        Fetch OpenRouter API key from Worker D1 database.

        Args:
            api_key: User's waitlist API key for authentication

        Returns: OpenRouter API key string or None if not found
        """
        if not self.base_url:
            return None
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/api/keys/openrouter/get",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=10.0,
                )
                response.raise_for_status()
                result = response.json()
                openrouter_key = result.get("openrouterKey")

                if openrouter_key:
                    logger.info("Successfully fetched OpenRouter key from D1")
                    return openrouter_key
                else:
                    logger.info("No OpenRouter key found in D1")
                    return None

        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch OpenRouter key from D1: {str(e)}", exc_info=True)
            return None
        except Exception as e:
            logger.error(
                f"Unexpected error fetching OpenRouter key from D1: {str(e)}",
                exc_info=True,
            )
            return None

    def _get_current_iso_timestamp(self) -> str:
        """Get current timestamp in ISO 8601 format"""
        return datetime.now(UTC).isoformat()

    # ==================== Multi-User Support Methods ====================

    async def _get_or_create_local_user(
        self,
        email: str,
        user_id_from_worker: str | None,
        name: str | None,
        session: AsyncSession,
    ) -> User:
        """
        Get existing user by email or create new one.
        For multi-user local DMG mode.
        """
        # Try to find by email first
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if user:
            logger.info(f"Found existing local user for {email}")
            return user

        # Create new user
        if user_id_from_worker:
            try:
                user_id = UUID(str(user_id_from_worker))
            except (ValueError, AttributeError):
                logger.warning(f"Invalid UUID from worker: {user_id_from_worker}, generating new UUID")
                user_id = uuid4()
        else:
            user_id = uuid4()
        new_user = User(
            id=user_id,
            email=email,
            full_name=name,
            hashed_password="local-mode-no-password",
            is_active=True,
            is_verified=True,
            is_superuser=False,
        )
        try:
            session.add(new_user)
            await session.flush()
            logger.info(f"Created new local user for {email} with id {user_id}")
            return new_user
        except IntegrityError:
            await session.rollback()
            result = await session.execute(select(User).where(User.email == email))
            existing_user = result.scalar_one_or_none()
            if existing_user:
                logger.info(f"Found user created by concurrent request: {email}")
                return existing_user
            raise

    async def _create_personal_tenant_for_user(
        self,
        user: User,
        session: AsyncSession,
    ) -> Tenant:
        """
        Create personal tenant for a specific user if not exists.
        For multi-user local DMG mode.
        """
        # Check if user already has a personal tenant
        result = await session.execute(select(Tenant).where(Tenant.owner_id == user.id, Tenant.is_personal.is_(True)))
        tenant = result.scalar_one_or_none()

        if tenant:
            member_result = await session.execute(
                select(TenantMember).where(
                    TenantMember.tenant_id == tenant.id,
                    TenantMember.user_id == user.id,
                )
            )
            membership = member_result.scalar_one_or_none()
            if not membership:
                member = TenantMember(
                    user_id=user.id,
                    tenant_id=tenant.id,
                    role=TenantRole.OWNER.value,
                    joined_at=datetime.now(UTC),
                )
                session.add(member)
                await session.flush()
                logger.info(f"Created missing OWNER membership for user {user.email} in tenant {tenant.slug}")
            elif membership.role != TenantRole.OWNER.value:
                membership.role = TenantRole.OWNER.value
                await session.flush()
                logger.info(f"Fixed role to OWNER for user {user.email} in tenant {tenant.slug}")

            logger.info(f"User {user.email} already has personal tenant: {tenant.slug}")
            return tenant

        # Generate workspace name from email
        workspace_name = self._generate_workspace_name(user.email)

        # Generate unique slug
        base_slug = self._generate_slug(workspace_name, user.email)
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
            name=workspace_name,
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
        await session.flush()

        logger.info(f"Created personal tenant '{slug}' for user {user.email}")
        return tenant

    def _generate_workspace_name(self, email: str) -> str:
        """Generate workspace name from email (e.g., 'john's workspace' from john@example.com)"""
        username = email.split("@")[0]
        # Capitalize first letter
        display_name = username.capitalize()
        return f"{display_name}'s workspace"

    def _generate_slug(self, name: str, email: str) -> str:
        """Generate a URL-safe slug from workspace name and email."""
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if not slug:
            username = email.split("@")[0]
            slug = re.sub(r"[^a-z0-9]+", "-", username.lower()).strip("-")
        return slug


# Singleton instance
waitlist_service = WaitlistService()
