import asyncio

import httpx

from server.db.session import AsyncSessionFactory
from server.repositories.llm_connections import LLMConnectionRepository
from server.services.settings import SettingsService
from server.services.waitlist_service import waitlist_service
from server.utils.config_loader import is_desktop_mode
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

SYNC_INTERVAL_SECONDS = 5 * 60  # 5 minutes


class CreditSyncService:
    """
    Service for background credit synchronization with Worker D1.

    Only runs in DMG (desktop) mode.
    """

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running: bool = False

    async def start(self) -> None:
        """Start the background sync task (desktop mode only)"""
        if not is_desktop_mode():
            logger.info("Credit sync service disabled (not in desktop mode)")
            return

        if self._running:
            logger.warning("Credit sync service already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._sync_loop())
        logger.info("Credit sync service started (polling every 5 minutes)")

    async def stop(self) -> None:
        """Stop the background sync task gracefully"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Credit sync service stopped")

    async def _sync_loop(self) -> None:
        """Main sync loop - runs every 5 minutes"""
        # Initial delay to let server fully start
        await asyncio.sleep(10)

        while self._running:
            try:
                await self._sync_credits()
            except Exception as e:
                logger.error(
                    f"Error in credit sync: {str(e)}",
                    exc_info=True,
                    posthog_context={"function": "_sync_loop"},
                )

            # Wait for next sync interval
            await asyncio.sleep(SYNC_INTERVAL_SECONDS)

    async def _sync_credits(self) -> None:
        """Perform a single credit sync check"""
        async with AsyncSessionFactory() as session:
            # Check if user already has credits locally - skip sync if so
            has_credits_setting = await SettingsService.get_setting_by_key(session, "has_credits")
            if has_credits_setting and has_credits_setting.setting_value:
                if has_credits_setting.setting_value.lower() == "true":
                    logger.info("[CreditSync] User already has credits locally, skipping poll")
                    return

            # Get stored API key
            api_key_setting = await SettingsService.get_setting_by_key(session, "api_key")
            if not api_key_setting or not api_key_setting.setting_value:
                logger.info("[CreditSync] No API key found, skipping poll")
                return

            api_key = api_key_setting.setting_value

            # Fetch current credit status from Worker
            logger.info("[CreditSync] Polling worker for credit status...")
            sync_data = await self._fetch_sync_from_worker(api_key)

            if sync_data is None:
                logger.warning("[CreditSync] Failed to fetch sync data from Worker")
                return

            worker_has_credits = sync_data.get("hasCredits", False)
            worker_openrouter_key = sync_data.get("openrouterKey")

            if not worker_has_credits:
                logger.info("[CreditSync] No credits from worker yet, will check again in 5 minutes")
                return

            # User has credits - update local state
            if worker_openrouter_key:
                logger.info("[CreditSync] Credits + OpenRouter key detected! Updating local state...")
                await self._handle_credit_grant(session, worker_openrouter_key)
            else:
                # User has credits but no openrouter key returned - still mark has_credits=true
                # but check if we already have a local connection
                llm_repo = LLMConnectionRepository(session)
                existing_connections = await llm_repo.list(filters={"type": "openrouter"})

                if existing_connections:
                    # We have local connection, just update has_credits
                    await SettingsService.upsert_setting(
                        session=session,
                        setting_key="has_credits",
                        setting_value="true",
                        description="Whether user has OpenRouter credits",
                        is_encrypted=False,
                    )
                    logger.info("[CreditSync] Credits detected, local connection exists, has_credits set to true")
                else:
                    # No key from worker and no local connection - this is a problem
                    logger.warning(
                        "[CreditSync] Worker says hasCredits=true but no openrouterKey returned and no local connection exists"
                    )

    async def _fetch_sync_from_worker(self, api_key: str, max_retries: int = 2) -> dict | None:
        """Fetch sync data from Worker /api/sync endpoint with retry logic"""
        if not waitlist_service.base_url:
            return None
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{waitlist_service.base_url}/api/sync",
                        headers={"Authorization": f"Bearer {api_key}"},
                        timeout=20.0,
                    )
                    response.raise_for_status()
                    data = response.json()
                    logger.info(f"[CreditSync] Worker response: hasCredits={data.get('hasCredits', False)}")
                    return data
            except httpx.HTTPError as e:
                error_type = type(e).__name__
                error_message = str(e)

                # Retry if not the last attempt
                if attempt < max_retries:
                    logger.warning(
                        f"[CreditSync] HTTP error (attempt {attempt + 1}/{max_retries + 1}): {error_type} - {error_message}, retrying in 5 seconds..."
                    )
                    await asyncio.sleep(5)
                    continue
                else:
                    # Final attempt failed - check if it's internet or worker issue
                    try:
                        async with httpx.AsyncClient() as client:
                            await client.get("https://dns.google", timeout=3.0)
                        reason = "Worker is slow or unreachable"
                    except Exception:
                        reason = "No internet connection"

                    logger.error(
                        f"[CreditSync] HTTP error fetching from Worker after {max_retries + 1} attempts: {error_type} - {error_message}. Reason: {reason}. Will retry in 5 minutes."
                    )
                    return None
            except Exception as e:
                logger.error(
                    f"[CreditSync] Unexpected error fetching from Worker: {type(e).__name__} - {str(e)}", exc_info=True
                )
                return None

        return None

    async def _handle_credit_grant(self, session, openrouter_key: str) -> None:
        """Handle when user is granted credits"""
        # Check if OpenRouter connection already exists to avoid duplicates
        llm_repo = LLMConnectionRepository(session)
        existing_connections = await llm_repo.list(filters={"type": "openrouter"})

        if not existing_connections:
            # Create new OpenRouter LLM connection
            await waitlist_service._create_llm_connection(session, openrouter_key)
            logger.info("[CreditSync] Created new OpenRouter LLM connection")
        else:
            logger.info("[CreditSync] OpenRouter connection already exists, skipping creation")

        # Update local has_credits setting
        await SettingsService.upsert_setting(
            session=session,
            setting_key="has_credits",
            setting_value="true",
            description="Whether user has OpenRouter credits",
            is_encrypted=False,
        )

        logger.info("[CreditSync] SUCCESS - has_credits set to true")


# Singleton instance
credit_sync_service = CreditSyncService()
