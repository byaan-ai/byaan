from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, require_scope
from server.auth.scopes import Scope
from server.auth.tenant_context import set_tenant_id
from server.db.session import get_async_session
from server.models.handoff import ACTIVE_STATUSES
from server.repositories.handoffs import HandoffEventRepository, HandoffRepository
from server.schemas.handoff import (
    HandoffBriefUpdate,
    HandoffDetail,
    HandoffEventRead,
    HandoffMessageCreate,
    HandoffRead,
    HandoffRename,
    HandoffTeamSettings,
)
from server.schemas.standard_response import success_response
from server.services import handoff_service
from server.services.handoff_git import Workspace
from server.services.handoff_service import Actor, HandoffError, activity_summary
from server.utils.deployment import is_feature_enabled

router = APIRouter()


def _require_enabled() -> None:
    if not is_feature_enabled("handoffs_enabled"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Handoffs are not enabled")


def _actor(auth: AuthContext) -> Actor:
    user = getattr(auth, "user", None)
    label = getattr(user, "full_name", None) or getattr(user, "email", None)
    return Actor(user_id=auth.user_id, label=label)


def _transcript_path(handoff_id: UUID) -> Path | None:
    """The agent owns this directory, so a planted symlink could point anywhere Byaan can read; only serve a
    real file whose resolved path stays inside the handoff's own home."""
    home = Workspace.for_handoff(handoff_id).home.resolve()
    projects = home / ".claude" / "projects"
    if not projects.is_dir():
        return None
    candidates = []
    for path in projects.glob("*/*.jsonl"):
        if path.is_symlink() or not path.is_file():
            continue
        resolved = path.resolve()
        if home == resolved.parent or home in resolved.parents:
            candidates.append(resolved)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


async def _detail(session: AsyncSession, handoff) -> dict:
    events = await HandoffEventRepository(session).recent(handoff.id, 2000)
    detail = HandoffDetail.model_validate(handoff)
    detail.events = [HandoffEventRead.model_validate(e) for e in events[-200:] if e.kind != "notify"]
    detail.has_transcript = _transcript_path(handoff.id) is not None
    result = next((e for e in reversed(events) if e.kind == "result"), None)
    detail.steps = list((result.data or {}).get("steps", [])) if result else []
    detail.activity = activity_summary(events)["lines"]
    revision = await handoff_service.pending_revision(session, handoff.id)
    if revision:
        detail.pending_revision = revision["text"]
        detail.pending_revision_title = revision["title"]
        detail.pending_revision_brief = revision["brief"]
    detail.has_run = handoff.started_at is not None
    return detail.model_dump(mode="json")


def _viewer(auth: AuthContext) -> UUID | None:
    return None if auth.has_scope(Scope.NOTEBOOK_READ) else auth.user_id


async def _require_visible(session: AsyncSession, auth: AuthContext, handoff_id: UUID) -> None:
    _require_enabled()
    viewer = _viewer(auth)
    try:
        handoff = await handoff_service.get_for_tenant(session, auth.tenant_id, handoff_id)
    except HandoffError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if viewer is not None and not await HandoffRepository(session).is_visible(handoff.id, viewer):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Handoff not found")


async def _run(session: AsyncSession, auth: AuthContext, coro):
    set_tenant_id(auth.tenant_id)
    try:
        handoff = await coro
    except HandoffError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return success_response(data=await _detail(session, handoff))


@router.get("/handoffs")
async def list_handoffs(
    status_filter: str | None = Query(default=None, alias="status"),
    repo_id: UUID | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(require_scope(Scope.NOTEBOOK_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    _require_enabled()
    statuses = ACTIVE_STATUSES if status_filter == "active" else ((status_filter,) if status_filter else None)
    handoffs = await HandoffRepository(session).list_for_tenant(
        auth.tenant_id, statuses=statuses, repo_id=repo_id, viewer_id=_viewer(auth), offset=offset, limit=limit
    )
    return success_response(data=[HandoffRead.model_validate(h).model_dump(mode="json") for h in handoffs])


@router.get("/handoffs/export")
async def export_handoffs(
    date_from: datetime | None = Query(default=None, alias="from"),
    date_to: datetime | None = Query(default=None, alias="to"),
    auth: AuthContext = Depends(require_scope(Scope.NOTEBOOK_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    _require_enabled()
    handoffs = await HandoffRepository(session).list_for_tenant(
        auth.tenant_id, created_from=date_from, created_to=date_to, viewer_id=_viewer(auth), limit=10_000
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "id",
            "created_at",
            "title",
            "repo",
            "origin",
            "requested_by",
            "status",
            "pr_url",
            "untested",
            "ci_fix_rounds",
            "started_at",
            "finished_at",
            "duration_minutes",
            "error",
        ]
    )
    for h in handoffs:
        duration = (
            round((h.finished_at - h.started_at).total_seconds() / 60, 1) if h.started_at and h.finished_at else ""
        )
        writer.writerow(
            [
                h.id,
                h.created_at,
                h.title,
                h.repo_full_name,
                h.origin,
                h.requested_by,
                h.status,
                h.pr_url or "",
                h.untested,
                h.ci_fix_round,
                h.started_at or "",
                h.finished_at or "",
                duration,
                h.error or "",
            ]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="byaan-handoffs.csv"'},
    )


@router.get("/handoffs/requirements")
async def handoff_requirements(
    auth: AuthContext = Depends(require_scope(Scope.NOTEBOOK_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    from server.services.handoff_runner import requirements

    return success_response(
        data={"enabled": is_feature_enabled("handoffs_enabled"), **await requirements(session, auth.tenant_id)}
    )


@router.get("/handoffs/settings")
async def get_team_settings(
    auth: AuthContext = Depends(require_scope(Scope.NOTEBOOK_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    from server.services.handoff_runner import built_in_rules

    _require_enabled()
    settings = await handoff_service.team_settings(session, auth.tenant_id)
    return success_response(
        data={**settings.model_dump(), "built_in_rules": built_in_rules(), "can_edit": bool(auth.is_admin)}
    )


@router.put("/handoffs/settings")
async def update_team_settings(
    body: HandoffTeamSettings,
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    _require_enabled()
    if not auth.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only an admin can change handoff settings")
    settings = await handoff_service.save_team_settings(session, auth.tenant_id, body)
    return success_response(data=settings.model_dump(), message="Handoff settings saved")


@router.get("/handoffs/{handoff_id}")
async def get_handoff(
    handoff_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.NOTEBOOK_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    await _require_visible(session, auth, handoff_id)
    return await _run(session, auth, handoff_service.get_for_tenant(session, auth.tenant_id, handoff_id))


@router.get("/handoffs/{handoff_id}/events")
async def get_handoff_events(
    handoff_id: UUID,
    after_seq: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(require_scope(Scope.NOTEBOOK_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    await _require_visible(session, auth, handoff_id)
    events = await HandoffEventRepository(session).after(handoff_id, after_seq)
    return success_response(
        data=[HandoffEventRead.model_validate(e).model_dump(mode="json") for e in events if e.kind != "notify"]
    )


@router.get("/handoffs/{handoff_id}/transcript")
async def get_handoff_transcript(
    handoff_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.NOTEBOOK_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    await _require_visible(session, auth, handoff_id)
    path = _transcript_path(handoff_id)
    if not path:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="The transcript was removed with the workspace")
    return FileResponse(path, media_type="application/x-ndjson", filename=f"handoff-{handoff_id}.jsonl")


@router.post("/handoffs/{handoff_id}/start")
async def start_handoff(
    handoff_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.NOTEBOOK_UPDATE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    await _require_visible(session, auth, handoff_id)
    return await _run(session, auth, handoff_service.start(session, auth.tenant_id, handoff_id, _actor(auth)))


@router.post("/handoffs/{handoff_id}/dismiss")
async def dismiss_handoff(
    handoff_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.NOTEBOOK_UPDATE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    await _require_visible(session, auth, handoff_id)
    return await _run(session, auth, handoff_service.dismiss(session, auth.tenant_id, handoff_id, _actor(auth)))


@router.post("/handoffs/{handoff_id}/cancel")
async def cancel_handoff(
    handoff_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.NOTEBOOK_UPDATE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    await _require_visible(session, auth, handoff_id)
    return await _run(session, auth, handoff_service.cancel(session, auth.tenant_id, handoff_id, _actor(auth)))


@router.post("/handoffs/{handoff_id}/take-over")
async def take_over_handoff(
    handoff_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.NOTEBOOK_UPDATE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    await _require_visible(session, auth, handoff_id)
    return await _run(session, auth, handoff_service.take_over(session, auth.tenant_id, handoff_id, _actor(auth)))


@router.post("/handoffs/{handoff_id}/restart")
async def restart_handoff(
    handoff_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.NOTEBOOK_UPDATE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    await _require_visible(session, auth, handoff_id)
    return await _run(session, auth, handoff_service.restart(session, auth.tenant_id, handoff_id, _actor(auth)))


@router.post("/handoffs/{handoff_id}/review-feedback")
async def send_review_feedback(
    handoff_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.NOTEBOOK_UPDATE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    await _require_visible(session, auth, handoff_id)
    set_tenant_id(auth.tenant_id)
    try:
        outcome = await handoff_service.send_review_feedback(session, auth.tenant_id, handoff_id, _actor(auth))
    except HandoffError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return success_response(data={"outcome": outcome})


@router.patch("/handoffs/{handoff_id}")
async def rename_handoff(
    handoff_id: UUID,
    body: HandoffRename,
    auth: AuthContext = Depends(require_scope(Scope.NOTEBOOK_UPDATE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    await _require_visible(session, auth, handoff_id)
    return await _run(session, auth, handoff_service.rename(session, auth.tenant_id, handoff_id, body.title))


@router.patch("/handoffs/{handoff_id}/brief")
async def edit_handoff_brief(
    handoff_id: UUID,
    body: HandoffBriefUpdate,
    auth: AuthContext = Depends(require_scope(Scope.NOTEBOOK_UPDATE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    await _require_visible(session, auth, handoff_id)
    return await _run(
        session, auth, handoff_service.edit_brief(session, auth.tenant_id, handoff_id, body.brief, body.title)
    )


@router.post("/handoffs/{handoff_id}/messages")
async def message_handoff(
    handoff_id: UUID,
    body: HandoffMessageCreate,
    auth: AuthContext = Depends(require_scope(Scope.NOTEBOOK_UPDATE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    await _require_visible(session, auth, handoff_id)
    set_tenant_id(auth.tenant_id)
    try:
        outcome = await handoff_service.add_message(session, auth.tenant_id, handoff_id, body.text, _actor(auth))
    except HandoffError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return success_response(data={"outcome": outcome})
