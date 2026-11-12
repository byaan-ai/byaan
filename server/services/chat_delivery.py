from __future__ import annotations

from typing import Any
from uuid import UUID

from slack_sdk.errors import SlackApiError
from sqlalchemy.ext.asyncio import AsyncSession

from server.services.slack_service import SlackService
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

LOST_THREAD_ERRORS = {"thread_not_found", "not_in_channel", "channel_not_found"}


async def ensure_thread_id(session: AsyncSession, notebook_id: Any) -> Any:
    """Return the thread id for ``notebook_id``, creating the thread row if the notebook has never been chatted in."""
    from server.repositories.threads import ThreadRepository

    thread_repo = ThreadRepository(session)
    thread = await thread_repo.get(notebook_id)
    if thread:
        return notebook_id
    existing = await thread_repo.list(filters={"notebook_id": notebook_id})
    if existing:
        return existing[0].id
    await thread_repo.create({"id": notebook_id, "notebook_id": notebook_id, "thread_title": None})
    return notebook_id


async def write_notebook_message(
    session: AsyncSession, notebook_id: UUID, content: str, metadata: dict[str, Any] | None = None
) -> None:
    from server.repositories.messages import MessageRepository

    thread_id = await ensure_thread_id(session, notebook_id)
    await MessageRepository(session).create(
        {"thread_id": thread_id, "role": "assistant", "content": content, "metadata_": metadata or {}}
    )


async def post_slack_with_fallback(
    slack_client: SlackService,
    *,
    channel: str,
    text: str,
    blocks: list | None,
    thread_ts: str | None,
    reply_broadcast: bool = False,
    attachments: list | None = None,
) -> tuple[str, str | None, bool]:
    """Post into the thread, falling back to the channel root when it's gone; returns (channel, ts, fell_back)."""
    try:
        response = await slack_client.post_message(
            channel=channel,
            text=text,
            blocks=blocks,
            thread_ts=thread_ts,
            reply_broadcast=reply_broadcast if thread_ts else False,
            attachments=attachments,
        )
        return channel, _ts(response), False
    except SlackApiError as e:
        error = (e.response or {}).get("error", "")
        if not thread_ts or error not in LOST_THREAD_ERRORS:
            raise
        logger.warning(f"Slack thread post failed ({error}); falling back to channel root")
        response = await slack_client.post_message(channel=channel, text=text, blocks=blocks, attachments=attachments)
        return channel, _ts(response), True


def _ts(response: Any) -> str | None:
    return response.get("ts") if isinstance(response, dict) else None


async def slack_client_for_workspace(workspace: Any, session: AsyncSession) -> SlackService:
    from server.services.crypto_service import CryptoService
    from server.services.slack_service import cache_bot_token, cached_bot_token

    workspace_id = str(workspace.id)
    token = cached_bot_token(workspace_id)
    if not token:
        decrypted = await CryptoService.decrypt_config(workspace.bot_token_encrypted, session)
        token = decrypted.get("bot_token", decrypted) if isinstance(decrypted, dict) else decrypted
        cache_bot_token(workspace_id, token)
    return SlackService(token)
