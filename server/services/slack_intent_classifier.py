"""LLM intent gate for Slack thread follow-up messages.

Runs after Layer 1 heuristics pass and before the heavy agent pipeline. Uses a
cheap completion with a strict boolean output. On timeout or error the gate
returns False (safe skip) so ambiguity never triggers unwanted bot replies.
"""

from __future__ import annotations

import asyncio
import re
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from server.services.completion_service import CompletionService
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)


CLASSIFIER_TIMEOUT_SEC = 20.0

INTENT_TRUE_TEXT = "true_text"
INTENT_TRUE_REACT = "true_react"
INTENT_FALSE_REACT = "false_react"
INTENT_SKIP = "skip"

CLASSIFIER_POLICY = (
    "You decide two things for the newest message in one shot:\n"
    "(a) should_respond — does Byaan run the agent and post a content "
    "reply?\n"
    "(b) ack_mode — how to acknowledge receipt: a text ack ('Working on "
    "it…'), a 👍 reaction, or nothing.\n\n"
    "Output exactly ONE of these four tokens:\n"
    "TRUE_TEXT  → run agent AND post a text ack. Use for a fresh, "
    "standalone data/analytics/dashboard request that opens a new line "
    "of work.\n"
    "TRUE_REACT → run agent AND add a 👍 reaction (no text ack). Use "
    "for a mid-thread continuation that still asks Byaan to produce "
    "content: follow-up ask, correction, redirection, "
    "approval+instruction, rejection+instruction, or any warm-thread ask.\n"
    "FALSE_REACT → do NOT run agent, add a 👍 reaction only. Use for "
    "messages directed at Byaan that need no content reply — "
    "approvals, rejections, confirmations, gestures, sentiment, "
    "closing remarks, lightweight nudges with no new ask.\n"
    "SKIP → do nothing. Use when the message is not directed at Byaan: "
    "side chatter between humans, message to a named teammate, "
    "off-topic noise."
)


SYSTEM_PROMPT = (
    "You are a strict intent classifier for the Byaan analytics Slack bot. "
    "Read the Slack thread excerpt and classify the newest message. Output "
    "exactly ONE token from this set: TRUE_TEXT, TRUE_REACT, FALSE_REACT, "
    "SKIP. No punctuation, no reasoning, no other words."
)


def _format_history(history: list[dict]) -> str:
    if not history:
        return "(no prior messages in thread)"
    lines: list[str] = []
    for msg in history[-6:]:
        author = msg.get("author") or "user"
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"{author}: {text}")
    return "\n".join(lines) if lines else "(no prior messages in thread)"


def _parse_decision(raw: str | None) -> str:
    if not raw:
        return INTENT_SKIP
    token = re.sub(r"[^A-Z_]", "", raw.strip().upper())
    if token.startswith("TRUE_TEXT"):
        return INTENT_TRUE_TEXT
    if token.startswith("TRUE_REACT"):
        return INTENT_TRUE_REACT
    if token.startswith("FALSE_REACT"):
        return INTENT_FALSE_REACT
    return INTENT_SKIP


async def classify_intent(
    text: str,
    history: list[dict],
    llm_connection_id: UUID,
    session: AsyncSession,
    model: str | None = None,
    attachment_count: int = 0,
) -> tuple[str, str]:
    """Return (intent, decision_source).

    intent is one of: 'true_text', 'true_react', 'false_react', 'skip'.
    decision_source mirrors the intent prefixed by 'llm_', or 'timeout' / 'error'.

    ``attachment_count`` is a signal (not file content) so the classifier can
    weigh the presence of user-attached files when deciding intent. File content
    is not sent to the classifier. The main agent can only process image
    attachments (PNG/JPEG/WebP); non-image files are ignored by the agent but
    still count here because their presence is a signal that the user is
    directing something at Byaan.
    """
    attachment_hint = ""
    if attachment_count > 0:
        noun = "file" if attachment_count == 1 else "files"
        attachment_hint = (
            f"\nNote: the user also attached {attachment_count} {noun} to this message. "
            "Treat the attachment as part of what the user wants Byaan to look at — "
            "if the accompanying text (or empty text) is consistent with asking "
            "Byaan to inspect, analyze, or act on the attachment, lean toward TRUE_*. "
            "If none of the attachments are images Byaan can view, the agent will "
            "still see the text and respond accordingly."
        )

    prompt = f"""Classification policy:
{CLASSIFIER_POLICY}

Recent thread (oldest to newest):
{_format_history(history)}

Newest message from user:
\"\"\"{text.strip()}\"\"\"{attachment_hint}

Output TRUE_TEXT, TRUE_REACT, FALSE_REACT, or SKIP."""

    try:
        raw = await asyncio.wait_for(
            CompletionService.complete(
                prompt=prompt,
                llm_connection_id=llm_connection_id,
                session=session,
                system_prompt=SYSTEM_PROMPT,
                model=model,
            ),
            timeout=CLASSIFIER_TIMEOUT_SEC,
        )
    except TimeoutError:
        logger.info("Slack intent classifier timed out; defaulting to skip")
        return INTENT_SKIP, "timeout"
    except Exception as e:
        logger.warning(f"Slack intent classifier error: {e}")
        return INTENT_SKIP, "error"

    intent = _parse_decision(raw)
    return intent, f"llm_{intent}"
