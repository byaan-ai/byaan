"""Chat agent tools for Byaan Handoffs: propose, inspect, message, cancel and list code-fix handoffs."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from agents import function_tool
from agents.run_context import RunContextWrapper
from pydantic import ValidationError
from sqlalchemy import select

from server.auth.tenant_context import set_tenant_id
from server.db.session import get_async_session
from server.models.handoff import ACTIVE_STATUSES, Handoff
from server.models.slack_conversation import SlackConversation
from server.repositories.handoffs import HandoffRepository
from server.schemas.handoff import HandoffBrief
from server.services import handoff_service
from server.services.handoff_notifier import CARD_MARKER
from server.services.handoff_service import Actor, HandoffError, RepoChoiceNeeded
from server.services.slack_context import get_slack_context
from server.utils.custom_logger import get_logger
from server.utils.deployment import is_feature_enabled

logger = get_logger(__name__)

DRAFTED_THIS_TURN = "handoffs_drafted_this_turn"


def _ok(data: dict[str, Any]) -> str:
    return json.dumps({"ok": True, **data}, default=str)


def _err(message: str, code: str = "error") -> str:
    return json.dumps({"ok": False, "error": {"code": code, "message": message}})


def _uuid(value: Any) -> UUID | None:
    if not value:
        return None
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except ValueError:
        return None


def _context(ctx: RunContextWrapper[Any]) -> dict[str, Any]:
    return ctx.context if ctx and isinstance(ctx.context, dict) else {}


async def _actor(session, ctx: RunContextWrapper[Any]) -> Actor:
    from server.models.user import User

    slack_ctx = get_slack_context()
    user_id = _uuid(_context(ctx).get("user_id")) or (slack_ctx.byaan_user_id if slack_ctx else None)
    user = await session.get(User, user_id) if user_id else None
    label = (user.full_name or user.email) if user else None
    if slack_ctx:
        label = await _slack_display_name(session, slack_ctx) or label
    return Actor(user_id=user_id, label=label)


async def _slack_display_name(session, slack_ctx) -> str | None:
    """Actions taken in Slack are credited to the Slack name, matching what button clicks show."""
    from server.models.slack_workspace import SlackWorkspace
    from server.services.chat_delivery import slack_client_for_workspace

    try:
        workspace = await session.get(SlackWorkspace, slack_ctx.workspace_id)
        if not workspace:
            return None
        client = await slack_client_for_workspace(workspace, session)
        info = await client.get_user_info(slack_ctx.slack_user_id)
    except Exception as exc:
        logger.warning(f"[HANDOFF] Slack name lookup failed for {slack_ctx.slack_user_id}: {exc}")
        return None
    return (info or {}).get("name")


async def _slack_conversation_id(session) -> UUID | None:
    slack_ctx = get_slack_context()
    if not slack_ctx:
        return None
    result = await session.execute(
        select(SlackConversation.id)
        .where(
            SlackConversation.slack_workspace_id == slack_ctx.workspace_id,
            SlackConversation.slack_channel_id == slack_ctx.channel_id,
            SlackConversation.slack_thread_ts == slack_ctx.thread_ts,
        )
        .order_by(SlackConversation.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _target(session, ctx: RunContextWrapper[Any], tenant_id: UUID, handoff_id: str | None) -> Handoff:
    if handoff_id:
        parsed = _uuid(handoff_id)
        if not parsed:
            raise HandoffError("Invalid handoff_id.")
        handoff = await handoff_service.get_for_tenant(session, tenant_id, parsed)
        viewer = _uuid(_context(ctx).get("user_id"))
        visible = (
            handoff.origin == "slack"
            if get_slack_context()
            else viewer is not None and await HandoffRepository(session).is_visible(handoff.id, viewer)
        )
        if not visible:
            raise HandoffError("Handoff not found")
        return handoff
    conversation_id = await _slack_conversation_id(session)
    notebook_id = _uuid(_context(ctx).get("notebook_id"))
    handoff = await HandoffRepository(session).latest_for_origin(
        tenant_id, slack_conversation_id=conversation_id, notebook_id=None if conversation_id else notebook_id
    )
    if not handoff:
        raise HandoffError("No handoff found in this conversation. Pass a handoff_id or use list_handoffs.")
    return handoff


def _guard(ctx: RunContextWrapper[Any]) -> tuple[UUID | None, str | None]:
    if not is_feature_enabled("handoffs_enabled"):
        return None, _err("Handoffs are not enabled for this deployment.", code="feature_disabled")
    tenant_id = _uuid(_context(ctx).get("tenant_id"))
    if not tenant_id:
        slack_ctx = get_slack_context()
        tenant_id = slack_ctx.tenant_id if slack_ctx else None
    if not tenant_id:
        return None, _err("Missing tenant context.", code="no_tenant")
    set_tenant_id(tenant_id)
    return tenant_id, None


@function_tool
async def propose_handoff(
    ctx: RunContextWrapper[Any],
    title: str,
    problem: str,
    acceptance_check: str,
    evidence: list[str] | None = None,
    suspected_files: list[str] | None = None,
    repo: str | None = None,
    new_task: bool = False,
    handoff_id: str | None = None,
) -> str:
    """Draft a code-fix handoff to Claude Code and show a card with a Start button. Never starts work by itself.

    Use when someone asks to hand a code bug to Claude Code, or when you traced a bug to code.
    Put references only in the brief (query ids, row counts, schema names, file:line). Never include
    row-level data such as names, records or identifiers.

    One task per conversation and repository: if this conversation already has a handoff for the same
    repository, the draft revises that handoff (same branch, pull request and Claude Code session) instead of
    creating another. A revision of a finished, failed or stopped handoff is saved and applied when a person
    clicks Re-start; a revision of a running one is delivered to it. Set new_task=True only when this is a
    different task from that handoff.

    Args:
        title: Short task title, under 80 characters.
        problem: What is wrong and what the fix should do.
        acceptance_check: A check that fails today and passes after the fix.
        evidence: Supporting references, e.g. "query q-12 returns 0 reminders for 214 patients".
        suspected_files: Likely locations, ideally "path/to/file.py:88".
        repo: Repository full name (owner/name). Required when several repositories have handoffs on and neither
            the person, the channel nor the code you read points to one; ask the person instead of guessing.
        new_task: True only when this is a different task from the conversation's existing handoff.
        handoff_id: The handoff to revise when the conversation has more than one for the same repository.
    """
    tenant_id, error = _guard(ctx)
    if error:
        return error
    context = _context(ctx)
    schedule_id = _uuid(context.get("schedule_id")) if context.get("scheduled_run") else None
    try:
        brief = HandoffBrief(
            problem=problem,
            evidence=evidence or [],
            suspected_files=suspected_files or [],
            acceptance_check=acceptance_check,
        )
    except ValidationError as exc:
        return _err(f"Invalid brief: {exc.errors()[0]['msg']}", code="invalid_brief")

    revise_id = None
    if handoff_id and schedule_id:
        return _err("Scheduled runs can only propose new handoffs.", code="read_only")
    if handoff_id:
        revise_id = _uuid(handoff_id)
        if revise_id is None:
            return _err("Invalid handoff_id.", code="invalid_handoff_id")
    slack_ctx = get_slack_context()
    async for session in get_async_session():
        try:
            actor = await _actor(session, ctx)
            outcome = await handoff_service.propose(
                session,
                tenant_id=tenant_id,
                requested_by=None if schedule_id else actor.user_id,
                origin="slack" if slack_ctx else "notebook",
                title=title,
                brief=brief,
                repo_hint=repo,
                channel_id=slack_ctx.channel_id if slack_ctx else None,
                slack_conversation_id=await _slack_conversation_id(session),
                notebook_id=_uuid(context.get("notebook_id")),
                schedule_id=schedule_id,
                proposed_by_byaan=bool(schedule_id),
                new_task=new_task,
                actor=actor,
                revise_handoff_id=revise_id,
            )
        except RepoChoiceNeeded as exc:
            if schedule_id:
                return _err("Monitors can only propose a handoff when the repository is unambiguous.", "needs_repo")
            return json.dumps(
                {
                    "ok": False,
                    "error": {"code": "needs_repo", "message": str(exc)},
                    "repo_options": exc.options,
                    "note": "Nothing was drafted. Ask the person which of these repositories they mean, read the "
                    "relevant files there, then call propose_handoff again with repo set.",
                }
            )
        except HandoffError as exc:
            return _err(str(exc), code="handoff_rejected")
        handoff = outcome.handoff
        _context(ctx).setdefault(DRAFTED_THIS_TURN, set()).add(str(handoff.id))
        notes = {
            "created": "New handoff drafted. Include the card marker verbatim; a person must click Start.",
            "revised_draft": "Revised the existing draft in place. Include the card marker; a person must click Start.",
            "pending_revision": "Saved as a revision of the existing handoff. Include the card marker; a person clicks "
            "Re-start to apply it on the same branch and pull request.",
            "sent_to_running": "The handoff is running, so the revision was delivered to it as a follow-up.",
        }
        result: dict[str, Any] = {
            "handoff_id": str(handoff.id),
            "action": outcome.action,
            "status": handoff.status,
            "pr_url": handoff.pr_url,
            "repo": handoff.repo_full_name,
            "card": CARD_MARKER.format(id=handoff.id),
            "note": notes[outcome.action],
        }
        if schedule_id:
            del result["card"]
            result["note"] = "Handoff drafted. Byaan posts its card with this run's result; a person must click Start."
        return _ok(result)
    return _err("Could not open a database session.", code="session_error")


@function_tool
async def get_handoff_status(ctx: RunContextWrapper[Any], handoff_id: str | None = None) -> str:
    """Get progress of a code-fix handoff: status, latest milestone, recent events, PR and CI state.

    Defaults to the most recent handoff in this thread or notebook. Use it for "how's it going?".

    Args:
        handoff_id: Optional handoff id.
    """
    tenant_id, error = _guard(ctx)
    if error:
        return error
    async for session in get_async_session():
        try:
            handoff = await _target(session, ctx, tenant_id, handoff_id)
        except HandoffError as exc:
            return _err(str(exc), code="not_found")
        return _ok(await handoff_service.status_summary(session, handoff))
    return _err("Could not open a database session.", code="session_error")


@function_tool
async def message_handoff(ctx: RunContextWrapper[Any], text: str, handoff_id: str | None = None) -> str:
    """Send a follow-up or an answer to a handoff that is still in progress ("also flag the patient", answers).

    For a finished, failed or stopped handoff, use propose_handoff with its handoff_id instead; that saves the change
    as a revision a person applies with Re-start. Byaan delivers the text to the same Claude Code session on the same
    branch and pull request. Each run also
    gets the pull request context and, when CI is failing, the full output of the failing checks, attached by Byaan.
    Claude Code cannot fetch CI logs itself, so it relies on this.

    Args:
        text: The message for Claude Code.
        handoff_id: Optional handoff id; defaults to the latest handoff in this conversation.
    """
    tenant_id, error = _guard(ctx)
    if error:
        return error
    if _context(ctx).get("scheduled_run"):
        return _err("Scheduled runs cannot message handoffs.", code="read_only")
    async for session in get_async_session():
        try:
            handoff = await _target(session, ctx, tenant_id, handoff_id)
            if handoff.status in handoff_service.RESTARTABLE_STATUSES:
                return _err(
                    f"This handoff is {handoff.status.replace('_', ' ')}. Call propose_handoff with "
                    f"handoff_id={handoff.id} to save the change as a revision; a person applies it with Re-start.",
                    code="use_propose_handoff",
                )
            outcome = await handoff_service.add_message(
                session, tenant_id, handoff.id, text, await _actor(session, ctx)
            )
        except HandoffError as exc:
            return _err(str(exc), code="handoff_rejected")
        result: dict[str, Any] = {"handoff_id": str(handoff.id), "outcome": outcome}
        if outcome.startswith("new_handoff:"):
            new_id = outcome.split(":", 1)[1]
            _context(ctx).setdefault(DRAFTED_THIS_TURN, set()).add(new_id)
            result["card"] = CARD_MARKER.format(id=new_id)
            result["note"] = "The earlier PR is closed; a new handoff was drafted. Include the card marker."
        return _ok(result)
    return _err("Could not open a database session.", code="session_error")


@function_tool
async def start_handoff(ctx: RunContextWrapper[Any], handoff_id: str | None = None) -> str:
    """Start a drafted handoff, exactly like its Start button.

    Call it only when the person explicitly asks to start it in their latest message ("start it", "go ahead and
    start"). Never start one on your own, and never one you drafted in this same reply; the person must see the card
    first.

    Args:
        handoff_id: Optional handoff id; defaults to the latest handoff in this conversation.
    """
    tenant_id, error = _guard(ctx)
    if error:
        return error
    if _context(ctx).get("scheduled_run"):
        return _err("Scheduled runs cannot start handoffs.", code="read_only")
    async for session in get_async_session():
        try:
            handoff = await _target(session, ctx, tenant_id, handoff_id)
            if str(handoff.id) in _context(ctx).get(DRAFTED_THIS_TURN, set()):
                return _err(
                    "This handoff was drafted in this reply. Show the card and wait for the person to ask to start it.",
                    code="needs_review",
                )
            handoff = await handoff_service.start(session, tenant_id, handoff.id, await _actor(session, ctx))
        except HandoffError as exc:
            return _err(str(exc), code="handoff_rejected")
        return _ok({"handoff_id": str(handoff.id), "status": handoff.status, "repo": handoff.repo_full_name})
    return _err("Could not open a database session.", code="session_error")


@function_tool
async def cancel_handoff(ctx: RunContextWrapper[Any], handoff_id: str | None = None, confirmed: bool = False) -> str:
    """Stop a handoff. Nothing further is pushed; an open PR stays open. Requires confirmed=True.

    Args:
        handoff_id: Optional handoff id; defaults to the latest handoff in this conversation.
        confirmed: Must be True to actually stop it.
    """
    tenant_id, error = _guard(ctx)
    if error:
        return error
    if _context(ctx).get("scheduled_run"):
        return _err("Scheduled runs cannot stop handoffs.", code="read_only")
    async for session in get_async_session():
        try:
            handoff = await _target(session, ctx, tenant_id, handoff_id)
            if not confirmed:
                return _ok(
                    {
                        "preview": {"handoff_id": str(handoff.id), "title": handoff.title, "status": handoff.status},
                        "note": "Call cancel_handoff again with confirmed=True to stop it.",
                    }
                )
            handoff = await handoff_service.cancel(session, tenant_id, handoff.id, await _actor(session, ctx))
        except HandoffError as exc:
            return _err(str(exc), code="handoff_rejected")
        return _ok({"handoff_id": str(handoff.id), "status": handoff.status})
    return _err("Could not open a database session.", code="session_error")


@function_tool
async def restart_handoff(ctx: RunContextWrapper[Any], handoff_id: str | None = None) -> str:
    """Re-start a finished, failed or stopped handoff, exactly like its Re-start button.

    It runs again on the same branch, pull request, clone and Claude Code session, applying the handoff's
    pending revision if it has one, otherwise repeating its last request. Call it only when the person explicitly
    asks to re-start. Use list_handoffs to find the right handoff when the conversation has several.

    Args:
        handoff_id: Optional handoff id; defaults to the latest handoff in this conversation.
    """
    tenant_id, error = _guard(ctx)
    if error:
        return error
    if _context(ctx).get("scheduled_run"):
        return _err("Scheduled runs cannot re-start handoffs.", code="read_only")
    async for session in get_async_session():
        try:
            handoff = await _target(session, ctx, tenant_id, handoff_id)
            if str(handoff.id) in _context(ctx).get(DRAFTED_THIS_TURN, set()):
                return _err(
                    "This handoff was changed in this reply. Show the card and wait for the person to ask to re-start it.",
                    code="needs_review",
                )
            handoff, applied = await handoff_service.restart_with_outcome(
                session, tenant_id, handoff.id, await _actor(session, ctx)
            )
        except HandoffError as exc:
            return _err(str(exc), code="handoff_rejected")
        meaning = {
            "pending_revision": "Applied the handoff's pending revision.",
            "repeated_last_request": "No revision was pending, so Claude Code re-runs the last request; if the "
            "pull request already covers it, the handoff ends done with no new commits, not failed.",
            "brief": "Re-running the original brief.",
        }
        return _ok(
            {
                "handoff_id": str(handoff.id),
                "status": handoff.status,
                "pr_url": handoff.pr_url,
                "applied": applied,
                "note": meaning[applied],
            }
        )
    return _err("Could not open a database session.", code="session_error")


@function_tool
async def list_handoffs(ctx: RunContextWrapper[Any], status: str | None = None, repo: str | None = None) -> str:
    """List code-fix handoffs for this workspace ("what's in flight?").

    Args:
        status: Optional filter: "active", or a specific status such as "ci_running" or "done".
        repo: Optional repository full name filter.
    """
    tenant_id, error = _guard(ctx)
    if error:
        return error
    statuses = ACTIVE_STATUSES if status == "active" else ((status,) if status else None)
    in_slack = get_slack_context() is not None
    async for session in get_async_session():
        handoffs = await HandoffRepository(session).list_for_tenant(
            tenant_id,
            statuses=statuses,
            repo_full_name=repo,
            origin="slack" if in_slack else None,
            viewer_id=None if in_slack else _uuid(_context(ctx).get("user_id")),
            limit=50,
        )
        return _ok(
            {
                "handoffs": [
                    {
                        "handoff_id": str(h.id),
                        "title": h.title,
                        "repo": h.repo_full_name,
                        "status": h.status,
                        "pr_url": h.pr_url,
                        "untested": h.untested,
                        "created_at": h.created_at,
                    }
                    for h in handoffs
                ]
            }
        )
    return _err("Could not open a database session.", code="session_error")


HANDOFF_INTERACTIVE_TOOLS = [
    propose_handoff,
    get_handoff_status,
    start_handoff,
    message_handoff,
    restart_handoff,
    cancel_handoff,
    list_handoffs,
]
HANDOFF_SCHEDULED_TOOLS = [propose_handoff]


def get_handoff_tools(scheduled_run: bool = False) -> list:
    return HANDOFF_SCHEDULED_TOOLS if scheduled_run else HANDOFF_INTERACTIVE_TOOLS
