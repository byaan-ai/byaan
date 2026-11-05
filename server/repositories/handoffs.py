from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from server.models.handoff import (
    TERMINAL_STATUSES,
    WORKING_STATUSES,
    Handoff,
    HandoffEvent,
    HandoffMessage,
)
from server.models.notebooks import Notebook
from server.repositories.base import AsyncCRUDRepository

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "proposed": {"queued", "dismissed"},
    "queued": {"preparing", "cancelled", "dismissed"},
    "preparing": {"running", "failed", "cancelled", "taken_over"},
    "running": {"waiting_on_human", "pushing", "failed", "cancelled", "taken_over"},
    "waiting_on_human": {"running", "fixing", "failed", "cancelled", "taken_over"},
    "pushing": {"ci_running", "done", "failed", "ci_failed"},
    "ci_running": {"fixing", "done", "ci_failed", "cancelled", "taken_over"},
    "fixing": {"waiting_on_human", "pushing", "failed", "ci_failed", "cancelled", "taken_over"},
    "done": {"queued"},
    "ci_failed": {"queued"},
    "dismissed": set(),
    "cancelled": {"queued"},
    "failed": {"queued"},
    "taken_over": set(),
}


def sources_for(to_status: str) -> tuple[str, ...]:
    return tuple(src for src, targets in ALLOWED_TRANSITIONS.items() if to_status in targets)


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def visible_to_clause(viewer_id: UUID):
    """Slack handoffs are shared with the workspace; app handoffs belong to their requester and notebook owner."""
    owns_notebook = select(Notebook.id).where(Notebook.id == Handoff.notebook_id, Notebook.created_by == viewer_id)
    return or_(Handoff.origin == "slack", Handoff.requested_by == viewer_id, owns_notebook.exists())


class HandoffRepository(AsyncCRUDRepository[Handoff]):
    def __init__(self, session):
        super().__init__(session, Handoff)

    async def update_draft(self, handoff_id: UUID, **fields: Any) -> bool:
        result = await self._session.execute(
            update(Handoff)
            .where(Handoff.id == handoff_id, Handoff.status == "proposed")
            .values(**fields)
            .execution_options(synchronize_session=False)
        )
        await self._session.commit()
        return result.rowcount == 1

    async def is_visible(self, handoff_id: UUID, viewer_id: UUID) -> bool:
        result = await self._session.execute(
            select(Handoff.id).where(Handoff.id == handoff_id, visible_to_clause(viewer_id))
        )
        return result.scalar_one_or_none() is not None

    async def get_unscoped(self, handoff_id: UUID) -> Handoff | None:
        result = await self._session.execute(
            select(Handoff).where(Handoff.id == handoff_id).execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def refresh(self, handoff: Handoff) -> Handoff:
        await self._session.refresh(handoff)
        return handoff

    async def transition(
        self,
        handoff_id: UUID,
        to_status: str,
        from_statuses: Iterable[str] | None = None,
        **fields: Any,
    ) -> bool:
        allowed = set(sources_for(to_status))
        sources = allowed if from_statuses is None else allowed.intersection(from_statuses)
        if not sources:
            raise ValueError(f"No valid transition into {to_status!r} from {from_statuses!r}")

        values: dict[str, Any] = {"status": to_status, **fields}
        if to_status in TERMINAL_STATUSES and "finished_at" not in fields:
            values["finished_at"] = utcnow()
        if to_status not in WORKING_STATUSES:
            values.setdefault("worker_id", None)

        try:
            result = await self._session.execute(
                update(Handoff)
                .where(Handoff.id == handoff_id, Handoff.status.in_(sources))
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            return False
        return (result.rowcount or 0) > 0

    async def update_fields(self, handoff_id: UUID, **fields: Any) -> None:
        await self._session.execute(
            update(Handoff)
            .where(Handoff.id == handoff_id)
            .values(**fields)
            .execution_options(synchronize_session=False)
        )
        await self._session.commit()

    async def heartbeat(self, handoff_id: UUID, worker_id: str) -> bool:
        result = await self._session.execute(
            update(Handoff)
            .where(Handoff.id == handoff_id, Handoff.worker_id == worker_id, Handoff.status.in_(WORKING_STATUSES))
            .values(heartbeat_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        await self._session.commit()
        return (result.rowcount or 0) > 0

    async def status_of(self, handoff_id: UUID) -> str | None:
        result = await self._session.execute(select(Handoff.status).where(Handoff.id == handoff_id))
        return result.scalar_one_or_none()

    async def queued_candidates(self, limit: int = 20) -> list[Handoff]:
        result = await self._session.execute(
            select(Handoff).where(Handoff.status == "queued").order_by(Handoff.created_at.asc()).limit(limit)
        )
        return list(result.scalars().all())

    async def claim_poll(self, statuses: Iterable[str], interval: timedelta, limit: int = 20) -> list[UUID]:
        now = utcnow()
        result = await self._session.execute(
            select(Handoff.id)
            .where(
                Handoff.status.in_(tuple(statuses)),
                Handoff.next_check_at.is_not(None),
                Handoff.next_check_at <= now,
            )
            .order_by(Handoff.next_check_at.asc())
            .limit(limit)
        )
        claimed: list[UUID] = []
        for handoff_id in result.scalars().all():
            outcome = await self._session.execute(
                update(Handoff)
                .where(Handoff.id == handoff_id, Handoff.next_check_at <= now)
                .values(next_check_at=now + interval)
                .execution_options(synchronize_session=False)
            )
            if outcome.rowcount:
                claimed.append(handoff_id)
        await self._session.commit()
        return claimed

    async def stale_working(self, cutoff: datetime) -> list[Handoff]:
        result = await self._session.execute(
            select(Handoff).where(
                Handoff.status.in_(WORKING_STATUSES),
                or_(Handoff.heartbeat_at.is_(None), Handoff.heartbeat_at < cutoff),
            )
        )
        return list(result.scalars().all())

    async def owned_by(self, worker_id: str) -> list[Handoff]:
        result = await self._session.execute(
            select(Handoff).where(Handoff.worker_id == worker_id, Handoff.status.in_(WORKING_STATUSES))
        )
        return list(result.scalars().all())

    async def expired_workspaces(self, cutoff: datetime) -> list[Handoff]:
        result = await self._session.execute(
            select(Handoff).where(
                Handoff.status.in_(TERMINAL_STATUSES),
                Handoff.workspace_path.is_not(None),
                Handoff.finished_at.is_not(None),
                Handoff.finished_at < cutoff,
            )
        )
        return list(result.scalars().all())

    async def referenced_workspace_paths(self) -> set[str]:
        result = await self._session.execute(select(Handoff.workspace_path).where(Handoff.workspace_path.is_not(None)))
        return {path for path in result.scalars().all() if path}

    async def count_working(self) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(Handoff).where(Handoff.status.in_(WORKING_STATUSES))
        )
        return int(result.scalar_one())

    async def active_for_repo(self, repo_id: UUID) -> list[Handoff]:
        result = await self._session.execute(
            select(Handoff).where(
                Handoff.github_repository_id == repo_id,
                Handoff.status.not_in(TERMINAL_STATUSES),
            )
        )
        return list(result.scalars().all())

    async def queue_position(self, handoff: Handoff) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(Handoff)
            .where(
                Handoff.tenant_id == handoff.tenant_id,
                Handoff.status == "queued",
                Handoff.created_at < handoff.created_at,
            )
        )
        return int(result.scalar_one()) + 1

    async def latest_for_origin(
        self,
        tenant_id: UUID,
        *,
        slack_conversation_id: UUID | None = None,
        notebook_id: UUID | None = None,
        statuses: Iterable[str] | None = None,
    ) -> Handoff | None:
        query = select(Handoff).where(Handoff.tenant_id == tenant_id)
        if slack_conversation_id is not None:
            query = query.where(Handoff.slack_conversation_id == slack_conversation_id)
        elif notebook_id is not None:
            query = query.where(Handoff.notebook_id == notebook_id)
        else:
            return None
        if statuses is not None:
            query = query.where(Handoff.status.in_(tuple(statuses)))
        result = await self._session.execute(query.order_by(Handoff.created_at.desc()).limit(1))
        return result.scalar_one_or_none()

    async def task_for_thread(
        self,
        tenant_id: UUID,
        repo_id: UUID,
        *,
        slack_conversation_id: UUID | None,
        notebook_id: UUID | None,
    ) -> Handoff | None:
        """The thread's current handoff for this repo; one task per thread and repo until it is dismissed or handed off."""
        query = select(Handoff).where(
            Handoff.tenant_id == tenant_id,
            Handoff.github_repository_id == repo_id,
            Handoff.schedule_id.is_(None),
            Handoff.status.not_in(("dismissed", "taken_over")),
        )
        if slack_conversation_id is not None:
            query = query.where(Handoff.slack_conversation_id == slack_conversation_id)
        elif notebook_id is not None:
            query = query.where(Handoff.notebook_id == notebook_id, Handoff.slack_conversation_id.is_(None))
        else:
            return None
        result = await self._session.execute(query.order_by(Handoff.created_at.desc()).limit(1))
        return result.scalar_one_or_none()

    async def list_for_tenant(
        self,
        tenant_id: UUID,
        *,
        statuses: Iterable[str] | None = None,
        repo_id: UUID | None = None,
        repo_full_name: str | None = None,
        origin: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        viewer_id: UUID | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[Handoff]:
        query = select(Handoff).where(Handoff.tenant_id == tenant_id)
        if viewer_id is not None:
            query = query.where(visible_to_clause(viewer_id))
        if origin:
            query = query.where(Handoff.origin == origin)
        if repo_full_name:
            query = query.where(func.lower(Handoff.repo_full_name) == repo_full_name.lower())
        if statuses:
            query = query.where(Handoff.status.in_(tuple(statuses)))
        if repo_id:
            query = query.where(Handoff.github_repository_id == repo_id)
        if created_from:
            query = query.where(Handoff.created_at >= created_from)
        if created_to:
            query = query.where(Handoff.created_at <= created_to)
        result = await self._session.execute(query.order_by(Handoff.created_at.desc()).offset(offset).limit(limit))
        return list(result.scalars().all())

    async def find_by_fingerprint(
        self, schedule_id: UUID, fingerprint: str, cooldown_since: datetime
    ) -> Handoff | None:
        result = await self._session.execute(
            select(Handoff)
            .where(
                Handoff.schedule_id == schedule_id,
                Handoff.fingerprint == fingerprint,
                or_(
                    Handoff.status.not_in(TERMINAL_STATUSES),
                    Handoff.status.in_(("dismissed", "done", "ci_failed", "taken_over"))
                    & (Handoff.finished_at >= cooldown_since),
                ),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def open_proposal_for_schedule(self, schedule_id: UUID) -> Handoff | None:
        result = await self._session.execute(
            select(Handoff).where(Handoff.schedule_id == schedule_id, Handoff.status == "proposed").limit(1)
        )
        return result.scalar_one_or_none()

    async def open_prs_for_review(self, limit: int = 50) -> list[Handoff]:
        result = await self._session.execute(
            select(Handoff)
            .where(Handoff.status.in_(("done", "ci_failed")), Handoff.pr_number.is_not(None))
            .limit(limit)
        )
        return list(result.scalars().all())


class HandoffEventRepository:
    def __init__(self, session):
        self._session = session

    async def append(
        self,
        handoff_id: UUID,
        source: str,
        kind: str,
        message: str = "",
        data: dict[str, Any] | None = None,
    ) -> HandoffEvent:
        for attempt in range(3):
            result = await self._session.execute(
                select(func.coalesce(func.max(HandoffEvent.seq), 0)).where(HandoffEvent.handoff_id == handoff_id)
            )
            event = HandoffEvent(
                handoff_id=handoff_id,
                seq=int(result.scalar_one()) + 1,
                source=source,
                kind=kind,
                message=message[:4000],
                data=data,
            )
            self._session.add(event)
            try:
                await self._session.commit()
                return event
            except IntegrityError:
                await self._session.rollback()
                if attempt == 2:
                    raise
        raise RuntimeError("unreachable")

    async def recent(self, handoff_id: UUID, limit: int = 50) -> list[HandoffEvent]:
        result = await self._session.execute(
            select(HandoffEvent)
            .where(HandoffEvent.handoff_id == handoff_id)
            .order_by(HandoffEvent.seq.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    async def after(self, handoff_id: UUID, after_seq: int, limit: int = 200) -> list[HandoffEvent]:
        result = await self._session.execute(
            select(HandoffEvent)
            .where(HandoffEvent.handoff_id == handoff_id, HandoffEvent.seq > after_seq)
            .order_by(HandoffEvent.seq.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def latest_of_kind(self, handoff_id: UUID, kind: str) -> HandoffEvent | None:
        result = await self._session.execute(
            select(HandoffEvent)
            .where(HandoffEvent.handoff_id == handoff_id, HandoffEvent.kind == kind)
            .order_by(HandoffEvent.seq.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


class HandoffMessageRepository:
    def __init__(self, session):
        self._session = session

    async def add(
        self,
        handoff_id: UUID,
        kind: str,
        body: str,
        *,
        author_user_id: UUID | None = None,
        author_label: str | None = None,
        question_seq: int | None = None,
    ) -> HandoffMessage:
        message = HandoffMessage(
            handoff_id=handoff_id,
            kind=kind,
            body=body,
            author_user_id=author_user_id,
            author_label=author_label,
            question_seq=question_seq,
        )
        self._session.add(message)
        await self._session.commit()
        return message

    async def pending(self, handoff_id: UUID, kind: str) -> list[HandoffMessage]:
        result = await self._session.execute(
            select(HandoffMessage)
            .where(
                HandoffMessage.handoff_id == handoff_id,
                HandoffMessage.kind == kind,
                HandoffMessage.delivered_at.is_(None),
            )
            .order_by(HandoffMessage.created_at.asc())
        )
        return list(result.scalars().all())

    async def latest(self, handoff_id: UUID, kind: str) -> HandoffMessage | None:
        result = await self._session.execute(
            select(HandoffMessage)
            .where(HandoffMessage.handoff_id == handoff_id, HandoffMessage.kind == kind)
            .order_by(HandoffMessage.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def mark_delivered(self, message_ids: Iterable[UUID]) -> None:
        ids = list(message_ids)
        if not ids:
            return
        await self._session.execute(
            update(HandoffMessage)
            .where(HandoffMessage.id.in_(ids), HandoffMessage.delivered_at.is_(None))
            .values(delivered_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        await self._session.commit()
