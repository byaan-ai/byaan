from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from server.models.schedules import Schedule, ScheduleRun
from server.repositories.base import AsyncCRUDRepository


class ScheduleRepository(AsyncCRUDRepository[Schedule]):
    def __init__(self, session):
        super().__init__(session, Schedule)

    async def get_all_for_tenant(self, tenant_id: UUID) -> list[Schedule]:
        query = (
            select(Schedule)
            .where(Schedule.tenant_id == tenant_id)
            .options(selectinload(Schedule.notebook))
            .order_by(Schedule.created_at.desc())
        )
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def get_for_notebook(self, notebook_id: str) -> list[Schedule]:
        query = (
            select(Schedule)
            .where(Schedule.notebook_id == notebook_id)
            .options(selectinload(Schedule.notebook))
            .order_by(Schedule.created_at.desc())
        )
        query = self._apply_tenant_filter(query)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def get_with_notebook(self, schedule_id: str) -> Schedule | None:
        query = select(Schedule).where(Schedule.id == schedule_id).options(selectinload(Schedule.notebook))
        query = self._apply_tenant_filter(query)
        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    async def get_with_notebook_and_dashboards(self, schedule_id: str) -> Schedule | None:
        from server.models.notebooks import Notebook

        query = (
            select(Schedule)
            .where(Schedule.id == schedule_id)
            .options(selectinload(Schedule.notebook).selectinload(Notebook.dashboards))
        )
        query = self._apply_tenant_filter(query)
        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    async def get_due_schedules(self, now: datetime, include_ids: set[str] | None = None) -> list[Schedule]:
        """Active + due schedules, plus any row flagged ``manual_trigger_pending``
        (which covers draft / paused monitors that a user asked to run once).

        ``include_ids`` is kept for backward compatibility but no longer required:
        manual-run coordination now lives on the schedule row so it survives
        multi-worker deployments.
        """
        from sqlalchemy import or_

        base_predicate = or_(
            Schedule.status == "active",
            Schedule.manual_trigger_pending.is_(True),
            Schedule.id.in_(list(include_ids)) if include_ids else False,
        )
        query = (
            select(Schedule)
            .where(
                base_predicate,
                Schedule.is_running.is_(False),
                Schedule.next_run_at <= now,
                or_(Schedule.paused_until.is_(None), Schedule.paused_until <= now),
            )
            .options(selectinload(Schedule.notebook))
        )
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def claim_schedule(self, schedule_id: str) -> bool:
        """Legacy claim without lease — retained for compatibility; prefer claim_with_lease."""
        result = await self._session.execute(
            update(Schedule).where(Schedule.id == schedule_id, Schedule.is_running.is_(False)).values(is_running=True)
        )
        await self._session.commit()
        return result.rowcount > 0

    async def claim_with_lease(self, schedule_id: UUID, now: datetime) -> bool:
        """Atomic claim: sets running_since + is_running only if neither already set."""
        from sqlalchemy import or_

        result = await self._session.execute(
            update(Schedule)
            .where(
                Schedule.id == schedule_id,
                Schedule.is_running.is_(False),
                or_(Schedule.running_since.is_(None), Schedule.running_since < now),
            )
            .values(is_running=True, running_since=now)
        )
        await self._session.commit()
        return (result.rowcount or 0) > 0

    async def auto_resume_expired_pauses(self, now: datetime) -> int:
        """Flip paused monitors whose paused_until has passed back to active."""
        result = await self._session.execute(
            update(Schedule)
            .where(
                Schedule.status == "paused",
                Schedule.paused_until.is_not(None),
                Schedule.paused_until <= now,
            )
            .values(status="active", is_enabled=True, paused_until=None)
        )
        await self._session.commit()
        return result.rowcount or 0

    async def reclaim_stale_leases(self, cutoff: datetime) -> int:
        """Release leases whose running_since is older than cutoff (crash recovery)."""
        result = await self._session.execute(
            update(Schedule)
            .where(Schedule.is_running.is_(True), Schedule.running_since < cutoff)
            .values(is_running=False, running_since=None)
        )
        await self._session.commit()
        return result.rowcount or 0

    async def release_schedule(self, schedule_id: str, next_run_at: datetime) -> None:
        await self._session.execute(
            update(Schedule)
            .where(Schedule.id == schedule_id)
            .values(is_running=False, running_since=None, next_run_at=next_run_at)
        )
        await self._session.commit()

    async def list_by_status(self, tenant_id: UUID, status: str) -> list[Schedule]:
        query = (
            select(Schedule)
            .where(Schedule.tenant_id == tenant_id, Schedule.status == status)
            .options(selectinload(Schedule.notebook))
            .order_by(Schedule.created_at.desc())
        )
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def cleanup_old_drafts(self, cutoff: datetime) -> int:
        from sqlalchemy import delete

        result = await self._session.execute(
            delete(Schedule).where(Schedule.status == "draft", Schedule.updated_at < cutoff)
        )
        await self._session.commit()
        return result.rowcount or 0

    async def count_active_for_workspace(self, slack_workspace_id: UUID) -> int:
        from sqlalchemy import func

        query = (
            select(func.count(Schedule.id))
            .where(Schedule.slack_workspace_id == slack_workspace_id)
            .where(Schedule.status == "active")
        )
        result = await self._session.execute(query)
        return int(result.scalar() or 0)

    async def increment_failure(self, schedule_id: UUID) -> int:
        """Increment consecutive_failures and return the new count."""
        from sqlalchemy import func

        await self._session.execute(
            update(Schedule)
            .where(Schedule.id == schedule_id)
            .values(consecutive_failures=func.coalesce(Schedule.consecutive_failures, 0) + 1)
        )
        await self._session.commit()
        q = select(Schedule.consecutive_failures).where(Schedule.id == schedule_id)
        res = await self._session.execute(q)
        return int(res.scalar() or 0)

    async def reset_failure(self, schedule_id: UUID) -> None:
        await self._session.execute(update(Schedule).where(Schedule.id == schedule_id).values(consecutive_failures=0))
        await self._session.commit()

    async def set_status(self, schedule_id: UUID, status: str, paused_until: datetime | None = None) -> Schedule | None:
        patch: dict[str, Any] = {"status": status}
        if status == "active":
            patch["is_enabled"] = True
            patch["paused_until"] = None
        elif status in {"paused", "auto_paused"}:
            patch["is_enabled"] = False
            if paused_until is not None:
                patch["paused_until"] = paused_until
        elif status == "draft":
            patch["is_enabled"] = False
        await self._session.execute(update(Schedule).where(Schedule.id == schedule_id).values(**patch))
        await self._session.commit()
        return await self.get_with_notebook(str(schedule_id))

    async def get_for_slack_workspace(self, slack_workspace_id: UUID) -> list[Schedule]:
        query = (
            select(Schedule)
            .where(Schedule.slack_workspace_id == slack_workspace_id)
            .options(selectinload(Schedule.notebook))
            .order_by(Schedule.created_at.desc())
        )
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def count_for_tenant(self, tenant_id: UUID) -> int:
        from sqlalchemy import func

        query = select(func.count(Schedule.id)).where(Schedule.tenant_id == tenant_id)
        result = await self._session.execute(query)
        return int(result.scalar() or 0)

    async def clear_lost_thread(self, schedule_id: UUID) -> None:
        """Clear slack_thread_ts after a thread_not_found so future posts hit the channel root."""
        await self._session.execute(update(Schedule).where(Schedule.id == schedule_id).values(slack_thread_ts=None))
        await self._session.commit()

    async def disable_for_workspace(self, slack_workspace_id: UUID) -> int:
        result = await self._session.execute(
            update(Schedule)
            .where(Schedule.slack_workspace_id == slack_workspace_id)
            .values(is_enabled=False, slack_workspace_id=None)
        )
        await self._session.commit()
        return result.rowcount or 0


class ScheduleRunRepository(AsyncCRUDRepository[ScheduleRun]):
    def __init__(self, session):
        super().__init__(session, ScheduleRun)

    async def get_recent_runs(self, schedule_id: str, limit: int = 20) -> list[ScheduleRun]:
        query = (
            select(ScheduleRun)
            .where(ScheduleRun.schedule_id == schedule_id)
            .order_by(ScheduleRun.started_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def create_run(self, data: dict[str, Any]) -> ScheduleRun:
        run = ScheduleRun(**data)
        self._session.add(run)
        await self._session.commit()
        await self._session.refresh(run)
        return run
