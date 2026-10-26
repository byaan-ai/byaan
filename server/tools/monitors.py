"""Agent tools for conversational Monitors (recurring reports / crons).

Design highlights:
- `create_monitor` persists a row with ``status='draft'`` and the agent then
  renders a confirmation card. Nothing is scheduled until the user confirms via
  ``set_monitor_status(active)`` — either by button in Slack or by replying "turn
  it on" in chat.
- Everything is scoped per-tenant. Slack-origin monitors additionally enforce
  that calls happen inside the same workspace (via `SlackContext`).
- Scheduled runs call a run-only tool ``record_monitor_findings`` so the service
  layer can apply the notify_policy in code instead of in prose.

Tools exported:

- ``create_monitor``: draft a new monitor.
- ``set_monitor_status``: draft→active, active→paused, pause until a date, etc.
- ``update_monitor``: patch fields of an existing monitor.
- ``delete_monitor``: destructive, requires ``confirmed=True``.
- ``list_monitors``: scoped to the current notebook/thread or the whole workspace.
- ``run_monitor_now``: fire a one-off run (``trigger='manual'``).
- ``get_monitor_runs``: fetch recent runs and their findings for a monitor.
- ``record_monitor_findings``: run-only; stores structured findings for the
  current run. Not exposed on interactive agents.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from agents import function_tool
from agents.run_context import RunContextWrapper
from croniter import croniter

from server.auth.tenant_context import set_tenant_id
from server.db.session import get_async_session
from server.models.schedules import MONITOR_STATUSES, NOTIFY_POLICIES
from server.repositories.schedules import ScheduleRepository, ScheduleRunRepository
from server.services.schedule_service import ScheduleService
from server.services.slack_context import get_slack_context
from server.utils.custom_logger import get_logger
from server.utils.deployment import is_feature_enabled

logger = get_logger(__name__)

_MIN_INTERVAL_SECONDS = 3600  # hourly cap per spec
_MAX_ACTIVE_PER_WORKSPACE = 20

# Per-run findings buffer: schedule_id -> {outcome, headline, findings}. The
# scheduled-run executor seeds the key before invoking the agent and reads it
# back after the stream completes. Not used in interactive runs.
RUN_FINDINGS: dict[str, dict[str, Any]] = {}

# Schedule ids queued by run_monitor_now so the executor records the next run
# with trigger='manual' instead of 'scheduled'. Consumed once per run.
MANUAL_RUN_PENDING: set[str] = set()

# Signal to Slack post-processing that an interactive monitor card should be
# posted into the thread. Keyed by thread_ts (or channel_id when no thread).
# Each value is the monitor_id to render. Consumed once per agent turn.
CARD_POST_PENDING: dict[str, str] = {}

# Signal to Slack post-processing that a Block Kit list card should be posted
# after a list_monitors tool call. Keyed by thread_ts (or channel_id when no
# thread). Each value is the tenant_id so the drainer can scope the query.
LIST_CARD_PENDING: dict[str, str] = {}


def _ok(data: dict[str, Any]) -> str:
    return json.dumps({"ok": True, **data}, default=str)


def _err(message: str, code: str = "error") -> str:
    return json.dumps({"ok": False, "error": {"code": code, "message": message}})


def _feature_check() -> str | None:
    if not is_feature_enabled("conversational_schedules_enabled"):
        return _err("Monitors are not enabled for this deployment.", code="feature_disabled")
    return None


def _ctx_tenant_id(ctx: RunContextWrapper[Any]) -> UUID | None:
    tid = ctx.context.get("tenant_id") if ctx and ctx.context else None
    if not tid:
        return None
    return tid if isinstance(tid, UUID) else UUID(str(tid))


def _ctx_user_id(ctx: RunContextWrapper[Any]) -> UUID | None:
    uid = ctx.context.get("user_id") if ctx and ctx.context else None
    if not uid:
        slack_ctx = get_slack_context()
        if slack_ctx and getattr(slack_ctx, "byaan_user_id", None):
            uid = slack_ctx.byaan_user_id
    if not uid:
        return None
    return uid if isinstance(uid, UUID) else UUID(str(uid))


def _validate_cron(expression: str) -> tuple[bool, str | None]:
    try:
        c = croniter(expression)
        a = c.get_next(float)
        b = c.get_next(float)
        if (b - a) < _MIN_INTERVAL_SECONDS:
            return False, "Monitors must run at most once per hour."
        return True, None
    except (ValueError, KeyError, TypeError) as e:
        return False, f"Invalid cron expression: {e}"


def _is_scheduled_run(ctx: RunContextWrapper[Any]) -> bool:
    return bool(ctx and ctx.context and ctx.context.get("scheduled_run"))


@function_tool
async def create_monitor(
    ctx: RunContextWrapper[Any],
    name: str,
    cron_expression: str,
    instruction: str,
    timezone: str | None = None,
    notify_policy: str = "always",
    slack_channel_id: str | None = None,
    delivery: str = "thread",
    notebook_id: str | None = None,
) -> str:
    """
    Draft a new monitor. Writes a row with status='draft'. The caller must
    render a confirmation card and ask the user to confirm; use
    set_monitor_status(id, status='active') to turn it on.

    Args:
        name: Human-readable name.
        cron_expression: Standard 5-field cron. Minimum interval: 1 hour.
        instruction: Short, intent-focused description (1-3 sentences). Describe
            WHAT to monitor and WHEN to alert. Do NOT include SQL, table names,
            column names, join plans, or dataset UUIDs — the scheduled agent
            re-reads schema each run and chooses queries itself so monitors
            survive table / column changes.
        timezone: IANA timezone (e.g. "Asia/Karachi"). **Omit this field unless
            the user explicitly named a timezone.** When omitted, the tool
            resolves the correct timezone itself: Slack user's profile tz when
            invoked from Slack, else the browser timezone the web client sent
            with the request, else UTC. Passing "UTC" yourself defeats the
            browser-tz fallback and almost always produces the wrong default.
        notify_policy: always | on_alert | on_change (on_change is Phase 3).
        slack_channel_id: Target Slack channel. Defaults to current channel when
            invoked from Slack.
        delivery: ``"thread"`` (reply in the current Slack thread, default),
            ``"channel"`` (post to the channel root), or ``"broadcast"`` (reply
            in the thread AND broadcast to the channel).
        notebook_id: Home notebook. Omit to auto-create one.
    """
    if err := _feature_check():
        return err
    if _is_scheduled_run(ctx):
        return _err("Monitors cannot be created from a scheduled run.", code="read_only")

    tenant_id = _ctx_tenant_id(ctx)
    if not tenant_id:
        return _err("Missing tenant context.", code="no_tenant")

    if notify_policy not in NOTIFY_POLICIES:
        return _err(f"notify_policy must be one of {sorted(NOTIFY_POLICIES)}.", code="invalid_notify_policy")
    if delivery not in {"thread", "channel", "broadcast"}:
        return _err("delivery must be one of thread, channel, broadcast.", code="invalid_delivery")

    slack_ctx = get_slack_context()
    if slack_channel_id is None and slack_ctx:
        slack_channel_id = slack_ctx.channel_id

    if delivery == "channel":
        slack_thread_ts = None
        broadcast_to_channel = False
    elif delivery == "broadcast":
        slack_thread_ts = slack_ctx.thread_ts if slack_ctx else None
        broadcast_to_channel = True
    else:  # thread
        slack_thread_ts = slack_ctx.thread_ts if slack_ctx else None
        broadcast_to_channel = False

    # Treat an explicit "UTC" the same as omitted — models love to pass UTC
    # defensively, which overrides the user's real timezone. The tool is the
    # source of truth for the resolved tz.
    if not timezone or timezone == "UTC":
        if slack_ctx and slack_ctx.user_timezone and slack_ctx.user_timezone != "UTC":
            timezone = slack_ctx.user_timezone
        else:
            browser_tz = ctx.context.get("browser_timezone") if ctx and ctx.context else None
            timezone = browser_tz or timezone or "UTC"

    ok, msg = _validate_cron(cron_expression)
    if not ok:
        return _err(msg or "Invalid cron expression.", code="invalid_cron")

    try:
        fires = ScheduleService.preview_fire_times(cron_expression, timezone, count=3)
    except Exception as e:
        return _err(f"Could not compute fire times: {e}", code="cron_preview_failed")

    origin = "slack" if slack_ctx else "notebook_chat"

    async for session in get_async_session():
        set_tenant_id(tenant_id)
        repo = ScheduleRepository(session)

        if slack_ctx:
            active_count = await repo.count_active_for_workspace(slack_ctx.workspace_id)
            if active_count >= _MAX_ACTIVE_PER_WORKSPACE:
                return _err(
                    f"This Slack workspace already has {_MAX_ACTIVE_PER_WORKSPACE} active monitors.",
                    code="quota_exceeded",
                )

        resolved_notebook_id: UUID | None = None
        candidate = notebook_id or (ctx.context.get("notebook_id") if ctx and ctx.context else None)
        if candidate:
            try:
                resolved_notebook_id = UUID(str(candidate))
            except ValueError:
                if notebook_id:
                    return _err("Invalid notebook_id.", code="invalid_notebook_id")
                # Context-provided id was garbled — fall through to auto-create.
                resolved_notebook_id = None
        if resolved_notebook_id is None:
            resolved_notebook_id = await ScheduleService.auto_create_notebook_for_cron(session, tenant_id, name)

        pinned_conn = ctx.context.get("llm_connection_id") if ctx and ctx.context else None
        pinned_model = ctx.context.get("model") if ctx and ctx.context else None
        if pinned_conn and not isinstance(pinned_conn, UUID):
            try:
                pinned_conn = UUID(str(pinned_conn))
            except ValueError:
                pinned_conn = None

        schedule_data: dict[str, Any] = {
            "tenant_id": tenant_id,
            "notebook_id": resolved_notebook_id,
            "name": name,
            "cron_expression": cron_expression,
            "timezone": timezone,
            "is_enabled": False,
            "status": "draft",
            "notify_policy": notify_policy,
            "slack_channel_id": slack_channel_id,
            "slack_thread_ts": slack_thread_ts,
            "slack_workspace_id": slack_ctx.workspace_id if slack_ctx else None,
            "broadcast_to_channel": bool(broadcast_to_channel),
            "origin": origin,
            "instruction": instruction,
            "created_by": _ctx_user_id(ctx),
            "llm_connection_id": pinned_conn,
            "model": pinned_model,
        }
        schedule = await repo.create(schedule_data)
        logger.info(f"Monitor drafted id={schedule.id} tenant={tenant_id} origin={origin}")

        if slack_ctx:
            key = slack_ctx.thread_ts or slack_ctx.channel_id
            if key:
                CARD_POST_PENDING[key] = str(schedule.id)

        return _ok(
            {
                "monitor": {
                    "id": str(schedule.id),
                    "name": schedule.name,
                    "status": schedule.status,
                    "cron_expression": schedule.cron_expression,
                    "timezone": schedule.timezone,
                    "notify_policy": schedule.notify_policy,
                    "slack_channel_id": schedule.slack_channel_id,
                    "slack_thread_ts": schedule.slack_thread_ts,
                    "broadcast_to_channel": schedule.broadcast_to_channel,
                    "notebook_id": str(schedule.notebook_id),
                    "instruction_preview": (schedule.instruction or "")[:400],
                    "next_fire_times": [f.isoformat() for f in fires],
                },
                "note": "Draft created. Call set_monitor_status(id, status='active') after the user confirms.",
            }
        )
    return _err("Could not open a database session.", code="session_error")


@function_tool
async def set_monitor_status(
    ctx: RunContextWrapper[Any],
    monitor_id: str,
    status: str,
    paused_until: str | None = None,
) -> str:
    """
    Change a monitor's status. Valid transitions:
    - draft -> active (turning on a draft)
    - active <-> paused (pause/resume)
    - active -> auto_paused (service-managed only; reject from chat)
    - paused with paused_until='YYYY-MM-DDTHH:MM:SS' (ISO 8601) for timed pauses
    """
    if err := _feature_check():
        return err
    if _is_scheduled_run(ctx):
        return _err("Monitors cannot be modified from a scheduled run.", code="read_only")
    tenant_id = _ctx_tenant_id(ctx)
    if not tenant_id:
        return _err("Missing tenant context.", code="no_tenant")

    if status not in {"draft", "active", "paused"}:
        return _err("status must be one of draft, active, paused.", code="invalid_status")

    pu: datetime | None = None
    if paused_until:
        try:
            pu = datetime.fromisoformat(paused_until)
            if pu.tzinfo is not None:
                pu = pu.astimezone(UTC).replace(tzinfo=None)
        except ValueError:
            return _err("paused_until must be ISO 8601.", code="invalid_paused_until")

    async for session in get_async_session():
        set_tenant_id(tenant_id)
        repo = ScheduleRepository(session)
        schedule = await repo.get_with_notebook(monitor_id)
        if not schedule or schedule.tenant_id != tenant_id:
            return _err("Monitor not found for this tenant.", code="not_found")

        slack_ctx = get_slack_context()
        if slack_ctx and schedule.slack_workspace_id and schedule.slack_workspace_id != slack_ctx.workspace_id:
            return _err("Monitor belongs to a different workspace.", code="workspace_mismatch")

        if status == "active":
            # Re-read to avoid an identity-map cache seeing stale cron/timezone
            # when the caller ran update_monitor in the same conversation turn.
            await session.refresh(schedule)
            next_run_at = ScheduleService.calculate_next_run(schedule.cron_expression, schedule.timezone)
            await repo.update(
                monitor_id,
                {
                    "status": "active",
                    "is_enabled": True,
                    "paused_until": None,
                    "consecutive_failures": 0,
                    "next_run_at": next_run_at,
                },
            )
        else:
            updated = await repo.set_status(UUID(monitor_id), status=status, paused_until=pu)
            if not updated:
                return _err("Monitor not found after update.", code="not_found")

        refreshed = await repo.get_with_notebook(monitor_id)
        if slack_ctx:
            key = slack_ctx.thread_ts or slack_ctx.channel_id
            if key:
                CARD_POST_PENDING[key] = str(refreshed.id)
        return _ok(
            {
                "monitor": {
                    "id": str(refreshed.id),
                    "status": refreshed.status,
                    "paused_until": refreshed.paused_until.isoformat() if refreshed.paused_until else None,
                    "next_run_at": refreshed.next_run_at.isoformat() if refreshed.next_run_at else None,
                }
            }
        )
    return _err("Could not open a database session.", code="session_error")


@function_tool
async def update_monitor(
    ctx: RunContextWrapper[Any],
    monitor_id: str,
    name: str | None = None,
    cron_expression: str | None = None,
    timezone: str | None = None,
    instruction: str | None = None,
    notify_policy: str | None = None,
    slack_channel_id: str | None = None,
    delivery: str | None = None,
    notebook_id: str | None = None,
) -> str:
    """Patch an existing monitor's fields. Only the fields you pass are changed.

    ``delivery`` is the easy way to change where the monitor posts:
      - ``"thread"``   → reply in the Slack thread this monitor was created in.
      - ``"channel"``  → post to the Slack channel root (clears the thread pin).
      - ``"broadcast"`` → reply in the thread AND broadcast to the channel.
    """
    if err := _feature_check():
        return err
    if _is_scheduled_run(ctx):
        return _err("Monitors cannot be modified from a scheduled run.", code="read_only")
    tenant_id = _ctx_tenant_id(ctx)
    if not tenant_id:
        return _err("Missing tenant context.", code="no_tenant")

    async for session in get_async_session():
        set_tenant_id(tenant_id)
        repo = ScheduleRepository(session)
        schedule = await repo.get_with_notebook(monitor_id)
        if not schedule or schedule.tenant_id != tenant_id:
            return _err("Monitor not found for this tenant.", code="not_found")

        slack_ctx = get_slack_context()
        if slack_ctx and schedule.slack_workspace_id and schedule.slack_workspace_id != slack_ctx.workspace_id:
            return _err("Monitor belongs to a different workspace.", code="workspace_mismatch")

        if cron_expression:
            ok, msg = _validate_cron(cron_expression)
            if not ok:
                return _err(msg or "Invalid cron expression.", code="invalid_cron")
        if notify_policy and notify_policy not in NOTIFY_POLICIES:
            return _err(f"notify_policy must be one of {sorted(NOTIFY_POLICIES)}.", code="invalid_notify_policy")
        if delivery and delivery not in {"thread", "channel", "broadcast"}:
            return _err("delivery must be one of thread, channel, broadcast.", code="invalid_delivery")

        patch_raw = {
            "name": name,
            "cron_expression": cron_expression,
            "timezone": timezone,
            "instruction": instruction,
            "notify_policy": notify_policy,
            "slack_channel_id": slack_channel_id,
        }
        patch: dict[str, Any] = {k: v for k, v in patch_raw.items() if v is not None}
        if notebook_id:
            try:
                patch["notebook_id"] = UUID(notebook_id)
            except ValueError:
                return _err("Invalid notebook_id.", code="invalid_notebook_id")
        if delivery == "thread":
            patch["broadcast_to_channel"] = False
            # Keep existing slack_thread_ts; if unset, inherit from Slack context.
            if not schedule.slack_thread_ts and slack_ctx:
                patch["slack_thread_ts"] = slack_ctx.thread_ts
        elif delivery == "channel":
            patch["slack_thread_ts"] = None
            patch["broadcast_to_channel"] = False
        elif delivery == "broadcast":
            patch["broadcast_to_channel"] = True
            if not schedule.slack_thread_ts and slack_ctx:
                patch["slack_thread_ts"] = slack_ctx.thread_ts

        if cron_expression or timezone:
            new_cron = cron_expression or schedule.cron_expression
            new_tz = timezone or schedule.timezone
            patch["next_run_at"] = ScheduleService.calculate_next_run(new_cron, new_tz)

        updated = await repo.update(monitor_id, patch)
        if slack_ctx:
            key = slack_ctx.thread_ts or slack_ctx.channel_id
            if key:
                CARD_POST_PENDING[key] = str(updated.id)
        return _ok(
            {
                "monitor": {
                    "id": str(updated.id),
                    "name": updated.name,
                    "cron_expression": updated.cron_expression,
                    "timezone": updated.timezone,
                    "notify_policy": updated.notify_policy,
                    "status": updated.status,
                    "next_run_at": updated.next_run_at.isoformat() if updated.next_run_at else None,
                }
            }
        )
    return _err("Could not open a database session.", code="session_error")


@function_tool
async def delete_monitor(ctx: RunContextWrapper[Any], monitor_id: str, confirmed: bool = False) -> str:
    """Delete a monitor. Requires confirmed=True."""
    if err := _feature_check():
        return err
    if _is_scheduled_run(ctx):
        return _err("Monitors cannot be deleted from a scheduled run.", code="read_only")
    tenant_id = _ctx_tenant_id(ctx)
    if not tenant_id:
        return _err("Missing tenant context.", code="no_tenant")

    async for session in get_async_session():
        set_tenant_id(tenant_id)
        repo = ScheduleRepository(session)
        schedule = await repo.get_with_notebook(monitor_id)
        if not schedule or schedule.tenant_id != tenant_id:
            return _err("Monitor not found for this tenant.", code="not_found")

        slack_ctx = get_slack_context()
        if slack_ctx and schedule.slack_workspace_id and schedule.slack_workspace_id != slack_ctx.workspace_id:
            return _err("Monitor belongs to a different workspace.", code="workspace_mismatch")

        if not confirmed:
            return _ok(
                {
                    "preview": {
                        "id": str(schedule.id),
                        "name": schedule.name,
                        "cron_expression": schedule.cron_expression,
                    },
                    "note": "Call delete_monitor again with confirmed=True to permanently delete.",
                }
            )

        await repo.delete(monitor_id)
        return _ok({"id": monitor_id, "deleted": True})
    return _err("Could not open a database session.", code="session_error")


@function_tool
async def list_monitors(
    ctx: RunContextWrapper[Any],
    scope: str = "all",
    status: str | None = None,
) -> str:
    """
    List monitors visible to this tenant.

    Args:
        scope: 'all' (workspace-wide, default), 'thread' (only this Slack thread),
            or 'notebook' (only the current notebook/thread's notebook).
        status: optional filter — draft | active | paused | auto_paused.
    """
    if err := _feature_check():
        return err
    tenant_id = _ctx_tenant_id(ctx)
    if not tenant_id:
        return _err("Missing tenant context.", code="no_tenant")
    if status and status not in MONITOR_STATUSES:
        return _err(f"status must be one of {sorted(MONITOR_STATUSES)}.", code="invalid_status")

    slack_ctx = get_slack_context()
    notebook_id_ctx = ctx.context.get("notebook_id") if ctx and ctx.context else None

    async for session in get_async_session():
        set_tenant_id(tenant_id)
        repo = ScheduleRepository(session)
        schedules = await repo.get_all_for_tenant(tenant_id)

        if scope == "thread" and slack_ctx:
            schedules = [
                s
                for s in schedules
                if s.slack_channel_id == slack_ctx.channel_id and s.slack_thread_ts == slack_ctx.thread_ts
            ]
        elif scope == "notebook" and notebook_id_ctx:
            schedules = [s for s in schedules if str(s.notebook_id) == str(notebook_id_ctx)]
        if status:
            schedules = [s for s in schedules if s.status == status]

        if slack_ctx:
            key = slack_ctx.thread_ts or slack_ctx.channel_id
            if key:
                LIST_CARD_PENDING[key] = str(tenant_id)

        return _ok(
            {
                "monitors": [
                    {
                        "id": str(s.id),
                        "name": s.name,
                        "status": s.status,
                        "cron_expression": s.cron_expression,
                        "timezone": s.timezone,
                        "notify_policy": s.notify_policy,
                        "slack_channel_id": s.slack_channel_id,
                        "slack_thread_ts": s.slack_thread_ts,
                        "origin": s.origin,
                        "next_run_at": s.next_run_at.isoformat() if s.next_run_at else None,
                        "instruction_preview": (s.instruction or "")[:200],
                    }
                    for s in schedules
                ]
            }
        )
    return _err("Could not open a database session.", code="session_error")


@function_tool
async def run_monitor_now(ctx: RunContextWrapper[Any], monitor_id: str) -> str:
    """Queue a one-off manual run of a monitor. Returns immediately."""
    if err := _feature_check():
        return err
    if _is_scheduled_run(ctx):
        return _err("Scheduled runs cannot trigger other monitors.", code="read_only")
    tenant_id = _ctx_tenant_id(ctx)
    if not tenant_id:
        return _err("Missing tenant context.", code="no_tenant")

    async for session in get_async_session():
        set_tenant_id(tenant_id)
        repo = ScheduleRepository(session)
        schedule = await repo.get_with_notebook(monitor_id)
        if not schedule or schedule.tenant_id != tenant_id:
            return _err("Monitor not found for this tenant.", code="not_found")

        slack_ctx = get_slack_context()
        if slack_ctx and schedule.slack_workspace_id and schedule.slack_workspace_id != slack_ctx.workspace_id:
            return _err("Monitor belongs to a different workspace.", code="workspace_mismatch")

        due_at = datetime.now(UTC).replace(tzinfo=None)
        await repo.update(monitor_id, {"next_run_at": due_at})
        MANUAL_RUN_PENDING.add(monitor_id)
        logger.info(f"Manual run queued for monitor {monitor_id}")
        return _ok({"id": monitor_id, "queued_for": due_at.isoformat(), "trigger": "manual"})
    return _err("Could not open a database session.", code="session_error")


@function_tool
async def get_monitor_runs(ctx: RunContextWrapper[Any], monitor_id: str, limit: int = 10) -> str:
    """Return recent runs for a monitor with outcome, headline and findings."""
    if err := _feature_check():
        return err
    tenant_id = _ctx_tenant_id(ctx)
    if not tenant_id:
        return _err("Missing tenant context.", code="no_tenant")
    if limit < 1 or limit > 50:
        limit = 10

    async for session in get_async_session():
        set_tenant_id(tenant_id)
        sched_repo = ScheduleRepository(session)
        schedule = await sched_repo.get_with_notebook(monitor_id)
        if not schedule or schedule.tenant_id != tenant_id:
            return _err("Monitor not found for this tenant.", code="not_found")

        run_repo = ScheduleRunRepository(session)
        runs = await run_repo.get_recent_runs(monitor_id, limit=limit)
        return _ok(
            {
                "runs": [
                    {
                        "id": str(r.id),
                        "status": r.status,
                        "trigger": r.trigger,
                        "outcome": r.outcome,
                        "headline": r.headline,
                        "findings": r.findings or [],
                        "started_at": r.started_at.isoformat() if r.started_at else None,
                        "duration_ms": r.duration_ms,
                        "error_message": r.error_message,
                    }
                    for r in runs
                ]
            }
        )
    return _err("Could not open a database session.", code="session_error")


@function_tool
async def record_monitor_findings(
    ctx: RunContextWrapper[Any],
    outcome: str,
    headline: str,
    findings_json: str = "[]",
) -> str:
    """
    Record the structured findings of this scheduled run. Call this exactly once
    at the end of a scheduled run. Only available inside scheduled runs.

    Args:
        outcome: 'ok' | 'alert' | 'no_change'.
        headline: Single short line summarizing the run.
        findings_json: JSON-encoded list of {entity, check, value, previous_value, why}.
            Use "[]" when there is nothing specific to flag.
    """
    if not _is_scheduled_run(ctx):
        return _err("record_monitor_findings is only available in scheduled runs.", code="not_in_scheduled_run")

    if outcome not in {"ok", "alert", "no_change"}:
        return _err("outcome must be ok | alert | no_change.", code="invalid_outcome")

    schedule_id = ctx.context.get("schedule_id") if ctx and ctx.context else None
    if not schedule_id:
        return _err("No schedule_id in run context.", code="no_schedule_id")

    try:
        parsed = json.loads(findings_json) if findings_json else []
        if not isinstance(parsed, list):
            return _err("findings_json must decode to a JSON list.", code="invalid_findings")
    except json.JSONDecodeError as e:
        return _err(f"findings_json must be valid JSON: {e}", code="invalid_findings")

    payload = {
        "outcome": outcome,
        "headline": headline[:500] if headline else "",
        "findings": parsed,
    }
    ctx.context["monitor_findings"] = payload
    RUN_FINDINGS[str(schedule_id)] = payload
    return _ok({"recorded": True, "outcome": outcome})


MONITOR_INTERACTIVE_TOOLS = [
    create_monitor,
    set_monitor_status,
    update_monitor,
    delete_monitor,
    list_monitors,
    run_monitor_now,
    get_monitor_runs,
]

MONITOR_RUN_ONLY_TOOLS = [record_monitor_findings]


def get_monitor_tools(scheduled_run: bool = False) -> list:
    """Return the monitor tools for an agent run.

    Interactive runs get the 7 chat tools; scheduled runs get only the run-only
    findings recorder (plus the read helpers list/get_runs are blocked at the
    tool via ``_is_scheduled_run``).
    """
    return MONITOR_RUN_ONLY_TOOLS if scheduled_run else MONITOR_INTERACTIVE_TOOLS


# Backwards-compat alias so existing imports from cron_tools keep working during
# the rename. New code should import from ``server.tools.monitors`` directly.
__all__ = [
    "create_monitor",
    "set_monitor_status",
    "update_monitor",
    "delete_monitor",
    "list_monitors",
    "run_monitor_now",
    "get_monitor_runs",
    "record_monitor_findings",
    "MONITOR_INTERACTIVE_TOOLS",
    "MONITOR_RUN_ONLY_TOOLS",
    "get_monitor_tools",
]
