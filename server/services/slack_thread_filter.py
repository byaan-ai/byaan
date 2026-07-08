"""Cheap heuristic + state gating for Slack thread follow-up messages.

Layer 1 gate runs before any LLM classifier call. Rejects reactions, cross-user
replies, cold threads, and messages in threads Byaan does not own. Also handles
mute/resume keyword commands so users can silence the bot without leaving the
thread.
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.slack_conversation import SlackConversation
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

REACTION_ONLY_TOKENS = {
    "lol",
    "ok",
    "okay",
    "k",
    "kk",
    "nice",
    "cool",
    "thanks",
    "thx",
    "ty",
    "ta",
    "hmm",
    "hm",
    "wow",
    "haha",
    "lmao",
    "rofl",
    "great",
    "awesome",
    "perfect",
    "got it",
    "gotcha",
    "np",
    "bye",
    "cya",
    "brb",
    "afk",
}


MUTE_KEYWORDS = (
    "mute byaan",
    "byaan mute",
)


RESUME_KEYWORDS = (
    "resume byaan",
    "byaan resume",
)


SLACK_USER_MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]+)?>")
EMOJI_ONLY = re.compile(r"^(:[a-z0-9_+\-]+:|\s)+$", re.IGNORECASE)


class SkipReason:
    TOO_SHORT = "too_short"
    EMOJI_ONLY = "emoji_only"
    REACTION_TOKEN = "reaction_token"
    OTHER_HUMAN_TARGETED = "other_human_targeted"
    THREAD_NEVER_ACTIVE = "thread_never_active"
    THREAD_NOT_OWNED = "thread_not_owned"
    THREAD_MUTED = "thread_muted"


async def get_conversation(
    workspace_id: UUID,
    channel_id: str,
    thread_ts: str,
    session: AsyncSession,
) -> SlackConversation | None:
    """Load conversation row for a workspace+channel+thread."""
    stmt = (
        select(SlackConversation)
        .where(SlackConversation.slack_workspace_id == workspace_id)
        .where(SlackConversation.slack_channel_id == channel_id)
        .where(SlackConversation.slack_thread_ts == thread_ts)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _matches_command(text: str, keywords: tuple[str, ...]) -> bool:
    """Match keyword only when it's the whole message or a standalone command prefix.

    Prevents accidental triggers like "let's discuss the mute byaan feature".
    """
    lowered = text.strip().lower().rstrip("!.?,")
    if lowered in keywords:
        return True
    return any(lowered.startswith(f"{kw} ") or lowered.startswith(f"{kw}!") for kw in keywords)


def check_mute_keyword(text: str) -> bool:
    return _matches_command(text, MUTE_KEYWORDS)


def check_resume_keyword(text: str) -> bool:
    return _matches_command(text, RESUME_KEYWORDS)


def _is_reaction_only(text: str) -> bool:
    stripped = text.strip().lower()
    if not stripped:
        return True
    condensed = re.sub(r"[!.?,\s]+", " ", stripped).strip()
    return condensed in REACTION_ONLY_TOKENS


def _targets_other_human(text: str, bot_user_id: str | None) -> bool:
    mentions = SLACK_USER_MENTION.findall(text)
    if not mentions:
        return False
    for user_id in mentions:
        if bot_user_id and user_id == bot_user_id:
            continue
        return True
    return False


def layer1_should_skip(
    text: str,
    bot_user_id: str | None,
    conversation: SlackConversation | None,
) -> tuple[bool, str | None]:
    """Return (skip?, reason). Pure function, no I/O."""
    if len(text.strip()) < 2:
        return True, SkipReason.TOO_SHORT

    stripped = text.strip()

    if EMOJI_ONLY.match(stripped):
        return True, SkipReason.EMOJI_ONLY

    if _is_reaction_only(text):
        return True, SkipReason.REACTION_TOKEN

    if _targets_other_human(text, bot_user_id):
        return True, SkipReason.OTHER_HUMAN_TARGETED

    if conversation is None:
        return True, SkipReason.THREAD_NEVER_ACTIVE

    if conversation.auto_follow_muted:
        return True, SkipReason.THREAD_MUTED

    if not conversation.bot_owned:
        return True, SkipReason.THREAD_NOT_OWNED

    return False, None
