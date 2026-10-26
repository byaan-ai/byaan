from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from server.auth.tenant_context import set_tenant_id
from server.db.session import AsyncSessionFactory
from server.repositories.schedules import ScheduleRepository, ScheduleRunRepository
from server.services.schedule_service import ScheduleService
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

MAX_CONCURRENT = 3
EXECUTION_TIMEOUT = 300
CHECK_INTERVAL = 60
LEASE_STALE_SECONDS = EXECUTION_TIMEOUT * 2
DRAFT_TTL_DAYS = 7


class ScheduleRunnerService:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running: bool = False
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def start(self):
        if self._running:
            logger.warning("Schedule runner already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Schedule runner started (max {MAX_CONCURRENT} concurrent, {EXECUTION_TIMEOUT}s timeout)")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Schedule runner stopped")

    async def _run_loop(self):
        await asyncio.sleep(30)

        while self._running:
            try:
                await self._check_due_schedules()
            except Exception as e:
                logger.error(f"Schedule check error: {e}", exc_info=True)

            await asyncio.sleep(CHECK_INTERVAL)

    async def _check_due_schedules(self):
        async with AsyncSessionFactory() as session:
            repo = ScheduleRepository(session)
            now = datetime.now(UTC).replace(tzinfo=None)

            stale_cutoff = now - timedelta(seconds=LEASE_STALE_SECONDS)
            reclaimed = await repo.reclaim_stale_leases(stale_cutoff)
            if reclaimed:
                logger.warning(f"Reclaimed {reclaimed} stale schedule leases older than {LEASE_STALE_SECONDS}s")

            draft_cutoff = now - timedelta(days=DRAFT_TTL_DAYS)
            removed = await repo.cleanup_old_drafts(draft_cutoff)
            if removed:
                logger.info(f"Cleaned up {removed} stale draft monitors older than {DRAFT_TTL_DAYS} days")

            resumed = await repo.auto_resume_expired_pauses(now)
            if resumed:
                logger.info(f"Auto-resumed {resumed} monitors whose paused_until has passed")

            from server.tools.monitors import MANUAL_RUN_PENDING

            due = await repo.get_due_schedules(now, include_ids=set(MANUAL_RUN_PENDING))
            for schedule in due:
                asyncio.create_task(self._execute_with_controls(str(schedule.id)))

    async def _execute_with_controls(self, schedule_id: str):
        async with self._semaphore:
            async with AsyncSessionFactory() as session:
                repo = ScheduleRepository(session)
                now = datetime.now(UTC).replace(tzinfo=None)

                from uuid import UUID as _UUID

                if not await repo.claim_with_lease(_UUID(schedule_id), now):
                    logger.debug(f"Schedule {schedule_id} already running, skipping")
                    return

                schedule = await repo.get_with_notebook_and_dashboards(schedule_id)
                if not schedule:
                    return

                set_tenant_id(schedule.tenant_id)

                try:
                    result = await asyncio.wait_for(
                        ScheduleService.execute_schedule(session, schedule),
                        timeout=EXECUTION_TIMEOUT,
                    )
                    logger.info(f"Schedule {schedule.name} completed: {result['status']}")

                except TimeoutError:
                    logger.error(f"Schedule {schedule.name} timed out after {EXECUTION_TIMEOUT}s")
                    await self._record_failure(session, schedule, "timeout", f"Timed out after {EXECUTION_TIMEOUT}s")
                    await ScheduleService._handle_run_failure(session, schedule, "timeout")

                except Exception as e:
                    logger.error(f"Schedule {schedule.name} failed: {e}", exc_info=True)
                    await self._record_failure(session, schedule, "failed", str(e))
                    await ScheduleService._handle_run_failure(session, schedule, str(e))

                finally:
                    try:
                        next_run = ScheduleService.calculate_next_run(schedule.cron_expression, schedule.timezone)
                    except Exception:
                        next_run = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
                    await repo.release_schedule(str(schedule.id), next_run)

    async def _record_failure(self, session, schedule, status: str, error: str):
        run_repo = ScheduleRunRepository(session)
        await run_repo.create_run(
            {
                "schedule_id": schedule.id,
                "status": status,
                "error_message": error,
            }
        )


schedule_runner_service = ScheduleRunnerService()
