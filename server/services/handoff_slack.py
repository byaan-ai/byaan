from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from server.auth.tenant_context import set_tenant_id
from server.db.session import AsyncSessionFactory
from server.repositories.slack_workspace import SlackWorkspaceRepository
from server.schemas.handoff import HandoffBrief
from server.services import handoff_service
from server.services.chat_delivery import slack_client_for_workspace
from server.services.handoff_service import Actor, HandoffError
from server.services.slack_agent_service import SlackAgentService
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

EDIT_CALLBACK = "handoff_edit_submit"
CHANGES_CALLBACK = "handoff_changes_submit"
_MODAL_ACTIONS = {"handoff_edit", "handoff_ask_changes", "handoff_view_brief", "handoff_copy_brief"}


def is_handoff_action(action_id: str | None) -> bool:
    return bool(action_id) and action_id.startswith("handoff_") and action_id != "handoff_open_pr"


def is_handoff_view(callback_id: str | None) -> bool:
    return callback_id in (EDIT_CALLBACK, CHANGES_CALLBACK)


def needs_modal(action_id: str) -> bool:
    return action_id in _MODAL_ACTIONS


def _decode(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
        return decoded if isinstance(decoded, dict) else {"h": value}
    except (TypeError, ValueError):
        return {"h": value}


def _plain_input(block_id: str, label: str, initial: str = "", multiline: bool = False, optional: bool = False) -> dict:
    element: dict[str, Any] = {"type": "plain_text_input", "action_id": "value", "multiline": multiline}
    if initial:
        element["initial_value"] = initial[:2900]
    return {
        "type": "input",
        "block_id": block_id,
        "optional": optional,
        "label": {"type": "plain_text", "text": label},
        "element": element,
    }


def brief_text(title: str, repo: str, base_branch: str, brief: dict[str, Any]) -> str:
    parts = [f"# {title}", f"Repository: {repo} (base branch: {base_branch})"]
    if brief.get("problem"):
        parts.append(f"## Task\n{brief['problem']}")
    if brief.get("evidence"):
        parts.append("## Evidence\n" + "\n".join(f"- {item}" for item in brief["evidence"]))
    if brief.get("suspected_files"):
        parts.append("## Start by looking at\n" + "\n".join(f"- {item}" for item in brief["suspected_files"]))
    if brief.get("acceptance_check"):
        parts.append(f"## Done when\n{brief['acceptance_check']}")
    return "\n\n".join(parts)


def _copy_blocks(text: str, limit: int = 2900) -> list[dict]:
    chunks = [""]
    for line in text.split("\n"):
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        if len(chunks[-1]) + len(line) + 1 > limit:
            chunks.append("")
        chunks[-1] += f"{line}\n"
    return [
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "Full brief for Claude Code. Use the copy button on the box."}],
        },
        {
            "type": "rich_text",
            "elements": [
                {"type": "rich_text_preformatted", "elements": [{"type": "text", "text": chunk.rstrip()}]}
                for chunk in chunks
                if chunk.strip()
            ],
        },
    ]


def _brief_view(text: str) -> dict:
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": "Handoff brief"},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": _copy_blocks(text),
    }


async def open_modal(team_id: str, payload: dict[str, Any], action: dict[str, Any]) -> None:
    """Open the modal inline: Slack's trigger_id expires after 3 seconds."""
    handoff_id = _decode(action.get("value", "")).get("h")
    metadata = json.dumps(
        {
            "h": handoff_id,
            "c": (payload.get("channel") or {}).get("id"),
            "t": (payload.get("message") or {}).get("thread_ts") or (payload.get("message") or {}).get("ts"),
        }
    )
    async with AsyncSessionFactory() as session:
        workspace = await SlackWorkspaceRepository(session).get_by_team_id(team_id)
        if not workspace or not workspace.is_active or (payload.get("user") or {}).get("team_id") != team_id:
            return
        set_tenant_id(workspace.tenant_id)
        client = await slack_client_for_workspace(workspace, session)
        if action["action_id"] == "handoff_ask_changes":
            view = {
                "type": "modal",
                "callback_id": CHANGES_CALLBACK,
                "private_metadata": metadata,
                "title": {"type": "plain_text", "text": "Ask for changes"},
                "submit": {"type": "plain_text", "text": "Send to Claude"},
                "blocks": [_plain_input("changes", "What should change?", multiline=True)],
            }
        elif action["action_id"] in ("handoff_view_brief", "handoff_copy_brief"):
            handoff = await handoff_service.get_for_tenant(session, workspace.tenant_id, UUID(handoff_id))
            revision = await handoff_service.pending_revision(session, handoff.id) or {}
            title = revision.get("title") or handoff.title
            brief = revision.get("brief") or handoff.brief or {}
            view = _brief_view(brief_text(title, handoff.repo_full_name, handoff.base_branch, brief))
        else:
            handoff = await handoff_service.get_for_tenant(session, workspace.tenant_id, UUID(handoff_id))
            brief = handoff.brief or {}
            view = {
                "type": "modal",
                "callback_id": EDIT_CALLBACK,
                "private_metadata": metadata,
                "title": {"type": "plain_text", "text": "View & edit brief"},
                "submit": {"type": "plain_text", "text": "Save"},
                "blocks": [
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "Edit the brief, then Save."},
                        "accessory": {
                            "type": "button",
                            "action_id": "handoff_copy_brief",
                            "text": {"type": "plain_text", "text": ":clipboard: Copy brief"},
                            "value": json.dumps({"h": handoff_id}),
                        },
                    },
                    _plain_input("title", "Title", handoff.title),
                    _plain_input("problem", "Task", brief.get("problem", ""), multiline=True),
                    _plain_input(
                        "evidence", "Evidence (one per line)", "\n".join(brief.get("evidence", [])), True, True
                    ),
                    _plain_input(
                        "files", "Scope / files (one per line)", "\n".join(brief.get("suspected_files", [])), True, True
                    ),
                    _plain_input("check", "Done when", brief.get("acceptance_check", ""), True, True),
                ],
            }
        if action["action_id"] == "handoff_copy_brief":
            await client.push_view(payload["trigger_id"], view)
        else:
            await client.open_view(payload["trigger_id"], view)


async def handle_action(team_id: str, payload: dict[str, Any], action: dict[str, Any]) -> None:
    action_id = action.get("action_id", "")
    data = _decode(action.get("value", ""))
    channel = (payload.get("channel") or {}).get("id")
    thread_ts = (payload.get("message") or {}).get("thread_ts")
    await _authorized_call(team_id, payload["user"], channel, thread_ts, action_id, data)


async def handle_view(team_id: str, payload: dict[str, Any]) -> None:
    view = payload.get("view") or {}
    metadata = _decode(view.get("private_metadata", ""))
    values = view.get("state", {}).get("values", {})

    def field(block: str) -> str:
        return ((values.get(block) or {}).get("value") or {}).get("value") or ""

    if view.get("callback_id") == CHANGES_CALLBACK:
        data = {"h": metadata.get("h"), "text": field("changes")}
        action_id = "handoff_ask_changes_submit"
    else:
        data = {
            "h": metadata.get("h"),
            "title": field("title"),
            "brief": {
                "problem": field("problem"),
                "evidence": [line for line in field("evidence").splitlines() if line.strip()],
                "suspected_files": [line for line in field("files").splitlines() if line.strip()],
                "acceptance_check": field("check"),
            },
        }
        action_id = "handoff_edit_submit"
    await _authorized_call(team_id, payload["user"], metadata.get("c"), metadata.get("t"), action_id, data)


async def _authorized_call(
    team_id: str,
    user: dict[str, Any],
    channel: str | None,
    thread_ts: str | None,
    action_id: str,
    data: dict[str, Any],
) -> None:
    slack_user_id = user["id"]
    async with AsyncSessionFactory() as session:
        workspace = await SlackWorkspaceRepository(session).get_by_team_id(team_id)
        if not workspace or not workspace.is_active:
            return
        set_tenant_id(workspace.tenant_id)
        client = await slack_client_for_workspace(workspace, session)

        async def tell(text: str) -> None:
            if channel:
                await client.post_ephemeral(channel, slack_user_id, text, thread_ts)

        user_id, name, rejection = await SlackAgentService.resolve_slack_actor(
            workspace, slack_user_id, user.get("team_id"), client, session
        )
        if rejection:
            await tell(rejection)
            return
        actor = Actor(user_id=user_id, label=name)
        try:
            handoff_id = UUID(str(data.get("h")))
        except ValueError:
            await tell("That handoff couldn't be found.")
            return

        try:
            message = await _dispatch(session, workspace.tenant_id, handoff_id, action_id, data, actor)
        except HandoffError as exc:
            await tell(str(exc))
            return
        except ValidationError as exc:
            await tell(f"That brief isn't valid: {exc.errors()[0]['msg']}")
            return
        if message:
            await tell(message)


async def _dispatch(
    session, tenant_id: UUID, handoff_id: UUID, action_id: str, data: dict[str, Any], actor: Actor
) -> str | None:
    if action_id == "handoff_start":
        await handoff_service.start(session, tenant_id, handoff_id, actor)
        return None
    if action_id == "handoff_dismiss":
        await handoff_service.dismiss(session, tenant_id, handoff_id, actor)
        return None
    if action_id == "handoff_stop":
        await handoff_service.cancel(session, tenant_id, handoff_id, actor)
        return None
    if action_id == "handoff_take_over":
        await handoff_service.take_over(session, tenant_id, handoff_id, actor)
        return None
    if action_id == "handoff_restart":
        await handoff_service.restart(session, tenant_id, handoff_id, actor)
        return None
    if action_id.startswith("handoff_answer_"):
        handoff = await handoff_service.get_for_tenant(session, tenant_id, handoff_id)
        if handoff.status != "waiting_on_human":
            raise HandoffError("Claude isn't waiting for that answer anymore.")
        await handoff_service.add_message(session, tenant_id, handoff_id, str(data.get("a", "")), actor)
        return None
    if action_id == "handoff_send_review":
        await handoff_service.send_review_feedback(session, tenant_id, handoff_id, actor)
        return "Sent the review comments to Claude."
    if action_id == "handoff_ask_changes_submit":
        outcome = await handoff_service.add_message(session, tenant_id, handoff_id, str(data.get("text", "")), actor)
        if outcome.startswith("new_handoff:"):
            return "The earlier PR is closed, so I drafted a new handoff in this thread."
        return "Sent to Claude. It will push to the same branch."
    if action_id == "handoff_edit_submit":
        brief = HandoffBrief.model_validate(data.get("brief") or {})
        await handoff_service.edit_brief(session, tenant_id, handoff_id, brief, data.get("title"))
        return None
    raise HandoffError("Unknown handoff action.")
