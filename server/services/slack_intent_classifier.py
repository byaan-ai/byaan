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

STRICTNESS_HINT = (
    "TRUE when: (1) data/analytics question, (2) dashboard or chart tweak, "
    "(3) direct follow-up on Byaan's previous reply (including short answers "
    "like 'yes', 'no', 'the second one' etc when Byaan just asked something), "
    "(4) correction or clarification of the user's earlier question to Byaan, "
    "(5) meta question about Byaan's capabilities or available data. "
    "FALSE when: side chatter between humans, thank-you or acknowledgment "
    "replies, messages addressed to a named teammate (e.g. 'Hey John...'), "
    "or messages about topics Byaan clearly has no data for."
)


SYSTEM_PROMPT = (
    "You are a strict binary classifier for the Byaan analytics Slack bot. "
    "Read the Slack thread excerpt and decide if the newest message expects "
    "a Byaan reply. Output exactly ONE token: TRUE or FALSE. No punctuation, "
    "no reasoning, no other words."
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


def _parse_decision(raw: str | None) -> bool:
    if not raw:
        return False
    token = raw.strip().upper()
    token = re.sub(r"[^A-Z]", "", token)[:5]
    return token.startswith("TRUE")


async def classify_intent(
    text: str,
    history: list[dict],
    llm_connection_id: UUID,
    session: AsyncSession,
    model: str | None = None,
) -> tuple[bool, str]:
    """Return (should_respond, decision_source).

    decision_source is one of: 'llm_true', 'llm_false', 'timeout', 'error'.
    """
    prompt = f"""Strictness policy: {STRICTNESS_HINT}

Recent thread (oldest to newest):
{_format_history(history)}

Newest message from user:
\"\"\"{text.strip()}\"\"\"

Is the newest message directed at Byaan and expecting a response? Answer TRUE or FALSE."""

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
        return False, "timeout"
    except Exception as e:
        logger.warning(f"Slack intent classifier error: {e}")
        return False, "error"

    decision = _parse_decision(raw)
    return decision, "llm_true" if decision else "llm_false"
