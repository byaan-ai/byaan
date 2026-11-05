from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.tenant_context import tenant_context
from server.db.session import AsyncSessionFactory
from server.models.github_repository import GitHubRepository
from server.models.handoff import ACTIVE_STATUSES, TERMINAL_STATUSES, WORKING_STATUSES, Handoff, HandoffEvent
from server.models.slack_conversation import SlackConversation
from server.models.slack_workspace import SlackWorkspace
from server.repositories.handoffs import (
    HandoffEventRepository,
    HandoffRepository,
    utcnow,
)
from server.schemas.handoff import HandoffRepoConfig
from server.services.chat_delivery import post_slack_with_fallback, slack_client_for_workspace, write_notebook_message
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

PROGRESS_POST_INTERVAL = timedelta(minutes=10)
CARD_MARKER = "[[HANDOFF_CARD:{id}]]"
RUN_MARKER = "[[HANDOFF_RUN:{id}|{seq}]]"
MARKER_RE = re.compile(r"[ \t]*\[\[HANDOFF_(?:CARD|RUN):[^\]]*\]\]")
RUN_START_STATUSES = {"queued", "fixing"}

STATUS_LABELS = {
    "proposed": "Draft · waiting for Start",
    "queued": "Queued",
    "preparing": "Preparing workspace",
    "running": "Claude is working",
    "waiting_on_human": "Waiting for an answer",
    "pushing": "Opening the pull request",
    "ci_running": "PR open · CI running",
    "fixing": "Fixing CI failures",
    "done": "Done",
    "dismissed": "Dismissed",
    "cancelled": "Stopped",
    "failed": "Failed",
    "ci_failed": "CI still failing",
    "taken_over": "Handed to a person",
}

HANDOFF_WORKING_STATUS = "is working…"
INDICATOR_STATUSES = tuple(s for s in ACTIVE_STATUSES if s != "waiting_on_human")

_REPLY_STATUSES = {"running", "ci_running", "fixing", "done", "failed", "ci_failed", "cancelled", "taken_over"}
_MIRROR_STATUSES = {"running", "ci_running", "done", "failed", "ci_failed"}


async def notify_event(handoff_id: UUID, seq: int) -> None:
    try:
        async with AsyncSessionFactory() as session:
            await _dispatch(session, handoff_id, seq)
    except Exception as exc:
        logger.error(f"[HANDOFF] Notification failed for {handoff_id}#{seq}: {exc}", exc_info=True)


async def _dispatch(session: AsyncSession, handoff_id: UUID, seq: int) -> None:
    handoff = await HandoffRepository(session).get_unscoped(handoff_id)
    event = (
        await session.execute(
            select(HandoffEvent).where(HandoffEvent.handoff_id == handoff_id, HandoffEvent.seq == seq)
        )
    ).scalar_one_or_none()
    if not handoff or not event:
        return

    reply = await _reply_text(session, handoff, event)
    try:
        slack = await _slack_target(session, handoff)
        if slack and not _deferred_to_slack_reply(handoff):
            await _deliver_slack(session, handoff, event, reply, *slack)
    except Exception as exc:
        logger.warning(f"[HANDOFF] Slack delivery failed for {handoff.id} event {seq}: {exc}")
    if handoff.notebook_id:
        await _deliver_notebook(session, handoff, event, reply)


def _deferred_to_slack_reply(handoff: Handoff) -> bool:
    """Inside a Slack agent run a new draft's card is posted after Byaan's reply, not mid-stream."""
    from server.services.slack_context import get_slack_context

    return handoff.status == "proposed" and not handoff.slack_card_ts and get_slack_context() is not None


async def post_pending_cards(session: AsyncSession, slack_conversation_id: UUID) -> None:
    result = await session.execute(
        select(Handoff.id).where(
            Handoff.slack_conversation_id == slack_conversation_id,
            Handoff.status == "proposed",
            Handoff.slack_card_ts.is_(None),
        )
    )
    for handoff_id in result.scalars().all():
        latest = await HandoffEventRepository(session).latest_of_kind(handoff_id, "status")
        if latest:
            await _dispatch_slack_only(session, handoff_id, latest.seq)


async def _reply_text(session: AsyncSession, handoff: Handoff, event: HandoffEvent) -> str | None:
    data = event.data or {}
    if event.kind == "progress":
        return f"*Progress:* {event.message}" if await _should_post_progress(session, handoff, event) else None
    if event.kind == "question":
        return f"*Claude has a question:* {event.message}\nReply in this thread or pick an option on the handoff card."
    if event.kind == "review":
        count = len(data.get("new_ids", []))
        return f"{count} new review comment{'s' if count != 1 else ''} on <{handoff.pr_url}|PR #{handoff.pr_number}>."
    if event.kind != "status":
        return None
    status = data.get("status")
    if status not in _REPLY_STATUSES or data.get("resumed"):
        return None
    if status == "running" and data.get("first_start"):
        return f"Started. Claude Code is working on `{handoff.branch}` in `{handoff.repo_full_name}`. Ask me for progress any time."
    if status == "running":
        return None
    if status == "ci_running":
        return _result_text(handoff, data, "CI running") if _pushed_work(data) else event.message
    if status == "done" and _pushed_work(data):
        return _result_text(
            handoff, data, "no CI configured, so this PR is marked UNTESTED. Please test before merging"
        )
    if status == "taken_over":
        if not data.get("checkout"):
            return f"{event.message}."
        return f"{event.message}. Continue locally with:\n```{data['checkout']}```"
    return event.message or STATUS_LABELS.get(status, status)


def _pushed_work(data: dict[str, Any]) -> bool:
    return bool(data.get("pr_opened") or data.get("pr_updated"))


def _result_text(handoff: Handoff, data: dict[str, Any], tail: str) -> str:
    stats = data.get("stats") or {}
    verb = "is ready" if data.get("pr_opened") else "updated"
    headline = f"<{handoff.pr_url}|PR #{handoff.pr_number}> {verb}: {handoff.title}"
    numbers = f"{stats.get('files', 0)} files · +{stats.get('added', 0)} −{stats.get('removed', 0)} · {tail}"
    parts = [headline, numbers]
    if data.get("summary"):
        parts.append(str(data["summary"])[:1500])
    if data.get("risks"):
        parts.append(f"*Needs a human call:* {str(data['risks'])[:500]}")
    return "\n".join(parts)


async def _should_post_progress(session: AsyncSession, handoff: Handoff, event: HandoffEvent) -> bool:
    events = await HandoffEventRepository(session).recent(handoff.id, 200)
    cutoff = utcnow() - PROGRESS_POST_INTERVAL
    for previous in reversed(events):
        if previous.seq >= event.seq:
            continue
        if previous.kind == "notify" and (previous.data or {}).get("progress_posted") and previous.created_at >= cutoff:
            return False
    await HandoffEventRepository(session).append(handoff.id, "system", "notify", "", {"progress_posted": event.seq})
    return True


async def _slack_target(
    session: AsyncSession, handoff: Handoff
) -> tuple[Any, SlackConversation, HandoffRepoConfig] | None:
    if not handoff.slack_conversation_id:
        return None
    conversation = await session.get(SlackConversation, handoff.slack_conversation_id)
    if not conversation:
        return None
    workspace = await session.get(SlackWorkspace, conversation.slack_workspace_id)
    if not workspace or not workspace.is_active:
        return None
    repo = await session.get(GitHubRepository, handoff.github_repository_id) if handoff.github_repository_id else None
    client = await slack_client_for_workspace(workspace, session)
    return client, conversation, HandoffRepoConfig.from_db(repo.handoff_config if repo else None)


async def _deliver_slack(
    session: AsyncSession,
    handoff: Handoff,
    event: HandoffEvent,
    reply: str | None,
    client: Any,
    conversation: SlackConversation,
    config: HandoffRepoConfig,
) -> None:
    channel, thread_ts = conversation.slack_channel_id, conversation.slack_thread_ts
    context = await _card_context(session, handoff)
    card_text = f"Handoff: {handoff.title} ({STATUS_LABELS.get(handoff.status, handoff.status)})"
    card = [
        {
            "color": card_color(handoff, bool(context.get("pending_revision"))),
            "fallback": card_text,
            "blocks": build_card_blocks(handoff, context),
        }
    ]

    async def post_card() -> None:
        _, card_ts, _ = await post_slack_with_fallback(
            client, channel=channel, text="", blocks=None, thread_ts=thread_ts, attachments=card
        )
        await HandoffRepository(session).update_fields(handoff.id, slack_card_ts=card_ts)

    if handoff.slack_card_ts and (event.data or {}).get("revision"):
        frozen = [
            {
                "color": card_color(handoff),
                "fallback": card_text,
                "blocks": build_card_blocks(handoff, {**context, "superseded": True}),
            }
        ]
        try:
            await client.update_message(channel, handoff.slack_card_ts, "", [], attachments=frozen)
        except Exception as exc:
            logger.warning(f"[HANDOFF] Could not freeze the earlier card for {handoff.id}: {exc}")
        await post_card()
    elif handoff.slack_card_ts:
        try:
            await client.update_message(channel, handoff.slack_card_ts, "", [], attachments=card)
        except Exception as exc:
            logger.warning(f"[HANDOFF] Card update failed for {handoff.id}: {exc}")
    elif handoff.status in ("proposed", "queued"):
        await post_card()

    if reply:
        blocks = _reply_blocks(handoff, event, reply)
        await post_slack_with_fallback(client, channel=channel, text=reply, blocks=blocks, thread_ts=thread_ts)

    await sync_thread_indicator(session, client, conversation)

    status = (event.data or {}).get("status")
    if config.mirror_channel and event.kind == "status" and status in _MIRROR_STATUSES and reply:
        await _mirror(client, config.mirror_channel, channel, thread_ts, handoff, reply)


async def _mirror(
    client: Any, mirror_channel: str, channel: str, thread_ts: str | None, handoff: Handoff, reply: str
) -> None:
    try:
        permalink = await client.get_permalink(channel, thread_ts) if thread_ts else None
        link = f" (<{permalink}|thread>)" if permalink else ""
        await post_slack_with_fallback(
            client, channel=mirror_channel, text=f"*{handoff.title}*{link}\n{reply}", blocks=None, thread_ts=None
        )
    except Exception as exc:
        logger.warning(f"[HANDOFF] Mirror post to {mirror_channel} failed for {handoff.id}: {exc}")


async def _deliver_notebook(session: AsyncSession, handoff: Handoff, event: HandoffEvent, reply: str | None) -> None:
    data = event.data or {}
    status = data.get("status") if event.kind == "status" else None
    proposed_outside_chat = status == "proposed" and (handoff.schedule_id or data.get("deliver_card"))
    if proposed_outside_chat:
        await write_notebook_message(
            session,
            handoff.notebook_id,
            CARD_MARKER.format(id=handoff.id),
            {"source": "handoff_card", "handoff_id": str(handoff.id)},
        )
        return
    if status in RUN_START_STATUSES and not data.get("resumed"):
        await write_notebook_message(
            session,
            handoff.notebook_id,
            RUN_MARKER.format(id=handoff.id, seq=event.seq),
            {"source": "handoff_run", "handoff_id": str(handoff.id), "seq": event.seq},
        )
        return
    if status == "running":
        return
    if reply:
        await write_notebook_message(
            session,
            handoff.notebook_id,
            _slack_to_markdown(reply),
            {"source": "handoff_update", "handoff_id": str(handoff.id)},
        )


async def thread_indicator_status(session: AsyncSession, slack_conversation_id: UUID) -> str:
    """The Slack thread status to show while a handoff in the thread is working, otherwise none."""
    result = await session.execute(
        select(Handoff.id)
        .where(Handoff.slack_conversation_id == slack_conversation_id, Handoff.status.in_(INDICATOR_STATUSES))
        .limit(1)
    )
    return HANDOFF_WORKING_STATUS if result.scalar_one_or_none() else ""


async def sync_thread_indicator(session: AsyncSession, client: Any, conversation: SlackConversation) -> None:
    from server.services.slack_agent_service import SlackAgentService
    from server.services.slack_context import get_slack_context

    replying = get_slack_context()
    if not conversation.slack_thread_ts or (
        replying
        and (replying.channel_id, replying.thread_ts) == (conversation.slack_channel_id, conversation.slack_thread_ts)
    ):
        return
    workspace = await session.get(SlackWorkspace, conversation.slack_workspace_id)
    if not workspace or not await SlackAgentService._is_assistant_enabled(
        workspace, client, conversation.slack_channel_id, conversation.slack_thread_ts
    ):
        return
    status = await thread_indicator_status(session, conversation.id)
    await client.set_assistant_status(conversation.slack_channel_id, conversation.slack_thread_ts, status)


async def refresh_thread_indicators(session: AsyncSession) -> None:
    result = await session.execute(
        select(Handoff).where(Handoff.status.in_(INDICATOR_STATUSES), Handoff.slack_conversation_id.is_not(None))
    )
    seen: set[UUID] = set()
    for handoff in result.scalars().all():
        if handoff.slack_conversation_id in seen:
            continue
        seen.add(handoff.slack_conversation_id)
        token = tenant_context.set(handoff.tenant_id)
        try:
            target = await _slack_target(session, handoff)
            if target:
                await sync_thread_indicator(session, target[0], target[1])
        except Exception as exc:
            logger.warning(f"[HANDOFF] Indicator refresh failed for {handoff.id}: {exc}")
        finally:
            tenant_context.reset(token)


def _slack_to_markdown(text: str) -> str:
    text = re.sub(r"<([^|>]+)\|([^>]+)>", r"[\2](\1)", text)
    return re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"**\1**", text)


async def _card_context(session: AsyncSession, handoff: Handoff) -> dict[str, Any]:
    events = await HandoffEventRepository(session).recent(handoff.id, 60)
    progress = next((e for e in reversed(events) if e.kind == "progress"), None)
    ci = next((e for e in reversed(events) if e.kind == "ci"), None)
    question = next((e for e in reversed(events) if e.kind == "question"), None)
    repo = await session.get(GitHubRepository, handoff.github_repository_id) if handoff.github_repository_id else None
    queue_position = None
    if handoff.status == "queued":
        queue_position = await HandoffRepository(session).queue_position(handoff)
    from server.services.handoff_service import pending_revision

    revision = await pending_revision(session, handoff.id)
    return {
        "pending_revision": revision["text"] if revision else None,
        "revision_title": revision["title"] if revision else None,
        "revision_brief": revision["brief"] if revision else None,
        "progress": progress.message if progress else None,
        "ci": (ci.data or {}) if ci else None,
        "question": question if handoff.status == "waiting_on_human" else None,
        "minutes": HandoffRepoConfig.from_db(repo.handoff_config if repo else None).minutes,
        "queue_position": queue_position,
    }


def _button(text: str, action_id: str, value: str, style: str | None = None, url: str | None = None) -> dict:
    button: dict[str, Any] = {"type": "button", "text": {"type": "plain_text", "text": text}, "action_id": action_id}
    if url:
        button["url"] = url
    else:
        button["value"] = value
    if style:
        button["style"] = style
    return button


_CARD_COLORS = {"working": "#2563EB", "revision": "#D97706", "done": "#16A34A", "failed": "#DC2626", "idle": "#9CA3AF"}


def card_color(handoff: Handoff, pending_revision: bool = False) -> str:
    if pending_revision:
        return _CARD_COLORS["revision"]
    if handoff.status == "done":
        return _CARD_COLORS["done"]
    if handoff.status in ("failed", "ci_failed"):
        return _CARD_COLORS["failed"]
    if handoff.status in ("cancelled", "dismissed", "taken_over"):
        return _CARD_COLORS["idle"]
    return _CARD_COLORS["working"]


def _task_summary(brief: dict[str, Any], title: str, limit: int = 200) -> str:
    problem = " ".join(str(brief.get("problem") or "").split())
    sentence = re.split(r"(?<=[.!?])\s+(?=[A-Z])", problem, maxsplit=1)[0] if problem else ""
    if not sentence:
        return title[:limit]
    if len(sentence) <= limit:
        return sentence
    return sentence[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def build_card_blocks(handoff: Handoff, context: dict[str, Any]) -> list[dict]:
    hid = str(handoff.id)
    superseded = bool(context.get("superseded"))
    revised = bool(context.get("pending_revision")) and not superseded
    brief = (context.get("revision_brief") if revised else None) or handoff.brief or {}
    title = (context.get("revision_title") if revised else None) or handoff.title
    label = STATUS_LABELS.get(handoff.status, handoff.status)
    if superseded:
        label = f"{label} · newer card below"
    elif revised:
        label = f"Revision ready · Re-start applies it{f' to PR #{handoff.pr_number}' if handoff.pr_number else ''}"
    if handoff.status == "queued" and context.get("queue_position"):
        label = f"Queued · position {context['queue_position']}"

    repo_line = f"`{handoff.repo_full_name}` from `{handoff.base_branch}`"
    rows = [("Repo", repo_line), ("Agent", "Claude Code"), ("Task", _task_summary(brief, title))]
    files = brief.get("suspected_files") or []
    if files:
        more = f" +{len(files) - 4} more" if len(files) > 4 else ""
        rows.append(("Scope", ", ".join(f"`{f}`" for f in files[:4]) + more))
    rows += [("Limit", f"{context.get('minutes', 30)} min"), ("Status", label)]
    heading = "Handoff draft" if handoff.status == "proposed" else "Handoff"
    lines = "\n".join(f"*{name}:* {value}" for name, value in rows)
    blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{heading}*\n\n{lines}"}}]

    details = []
    if handoff.branch:
        details.append(f"branch `{handoff.branch}`")
    if handoff.pr_url:
        details.append(f"<{handoff.pr_url}|PR #{handoff.pr_number}>")
    if handoff.untested and handoff.pr_url:
        details.append("UNTESTED")
    if handoff.ci_fix_round and handoff.status in ("fixing", "ci_running"):
        details.append(f"CI fix round {handoff.ci_fix_round}/3")
    if context.get("progress") and handoff.status in WORKING_STATUSES:
        details.append(f"last update: {context['progress'][:200]}")
    if handoff.error and handoff.status in TERMINAL_STATUSES:
        details.append(f"_{handoff.error[:300]}_")
    if details:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": " · ".join(details)}]})

    question = context.get("question")
    if question is not None:
        options = (question.data or {}).get("options") or []
        if options:
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        _button(o[:70], f"handoff_answer_{i}", json.dumps({"h": hid, "q": question.seq, "a": o[:500]}))
                        for i, o in enumerate(options[:5])
                    ],
                }
            )

    if superseded:
        if not handoff.pr_url:
            return blocks
        return [
            *blocks,
            {"type": "actions", "elements": [_button("Open PR", "handoff_open_pr", hid, url=handoff.pr_url)]},
        ]

    actions = _actions_for(handoff, hid, revised)
    if actions:
        blocks.append({"type": "actions", "elements": actions})
    return blocks


def _actions_for(handoff: Handoff, hid: str, pending_revision: bool = False) -> list[dict]:
    status = handoff.status
    open_pr = [_button("Open PR", "handoff_open_pr", hid, url=handoff.pr_url)] if handoff.pr_url else []
    restart = [_button("Re-start", "handoff_restart", hid, "primary" if pending_revision else None)]
    if status == "proposed":
        start = [_button("Start handoff", "handoff_start", hid, "primary")] if handoff.github_repository_id else []
        return [*start, _button("View & edit brief", "handoff_edit", hid), _button("Not now", "handoff_dismiss", hid)]
    return [
        *_status_actions(handoff, hid, pending_revision, open_pr, restart),
        _button("View brief", "handoff_view_brief", hid),
    ]


def _status_actions(
    handoff: Handoff, hid: str, pending_revision: bool, open_pr: list[dict], restart: list[dict]
) -> list[dict]:
    status = handoff.status
    if status == "queued":
        return [_button("Stop", "handoff_stop", hid, "danger")]
    if status in ("preparing", "running", "waiting_on_human", "fixing"):
        take_over = [_button("Take over", "handoff_take_over", hid)] if handoff.branch else []
        return [*take_over, _button("Stop", "handoff_stop", hid, "danger")]
    if status == "ci_running":
        return [
            *open_pr,
            _button("Take over", "handoff_take_over", hid),
            _button("Stop", "handoff_stop", hid, "danger"),
        ]
    if status in ("done", "ci_failed") and handoff.pr_url:
        return [
            *(restart if pending_revision else []),
            *open_pr,
            _button("Ask for changes", "handoff_ask_changes", hid),
        ]
    if status in ("failed", "cancelled"):
        return [*restart, *open_pr]
    return open_pr


def _reply_blocks(handoff: Handoff, event: HandoffEvent, reply: str) -> list[dict]:
    blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": reply[:2900]}}]
    if event.kind == "review":
        blocks.append(
            {
                "type": "actions",
                "elements": [_button("Send to Claude", "handoff_send_review", str(handoff.id), "primary")],
            }
        )
    return blocks


async def deliver_monitor_proposals(
    session: AsyncSession, *, schedule_id: UUID, workspace: SlackWorkspace, channel_id: str, thread_ts: str | None
) -> None:
    """Attach a Monitor's pending proposal cards to the Slack thread the run just posted."""
    result = await session.execute(
        select(Handoff).where(
            Handoff.schedule_id == schedule_id, Handoff.status == "proposed", Handoff.slack_conversation_id.is_(None)
        )
    )
    proposals = list(result.scalars().all())
    if not proposals:
        return
    conversation = (
        (
            await session.execute(
                select(SlackConversation).where(
                    SlackConversation.slack_workspace_id == workspace.id,
                    SlackConversation.slack_channel_id == channel_id,
                    SlackConversation.slack_thread_ts == thread_ts,
                )
            )
        )
        .scalars()
        .first()
    )
    if conversation is None:
        conversation = SlackConversation(
            slack_workspace_id=workspace.id,
            slack_channel_id=channel_id,
            slack_thread_ts=thread_ts,
            notebook_id=proposals[0].notebook_id,
            slack_user_id=workspace.bot_user_id or "byaan",
            bot_owned=True,
        )
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)
    for proposal in proposals:
        await HandoffRepository(session).update_fields(proposal.id, slack_conversation_id=conversation.id)
        latest = await HandoffEventRepository(session).latest_of_kind(proposal.id, "status")
        if latest:
            async with AsyncSessionFactory() as fresh:
                await _dispatch_slack_only(fresh, proposal.id, latest.seq)


async def _dispatch_slack_only(session: AsyncSession, handoff_id: UUID, seq: int) -> None:
    handoff = await HandoffRepository(session).get_unscoped(handoff_id)
    event = (
        await session.execute(
            select(HandoffEvent).where(HandoffEvent.handoff_id == handoff_id, HandoffEvent.seq == seq)
        )
    ).scalar_one_or_none()
    slack = await _slack_target(session, handoff) if handoff else None
    if handoff and event and slack:
        await _deliver_slack(session, handoff, event, None, *slack)
