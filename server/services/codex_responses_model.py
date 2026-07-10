"""
Workarounds for Codex API with store=False:

1. Empty-output bug: response.completed returns empty output list even though
   function-call outputs were streamed via ResponseOutputItemDoneEvent events.
   Fix: accumulate output items and patch the completed event.

2. previous_response_id bug: the SDK references a prior response ID on
   subsequent turns, but store=False means Codex never persisted it (404).
   Fix: strip previous_response_id before each API call.

3. Input item IDs bug: the SDK sends back response output items (with server-assigned
   IDs) as input for the next turn. Codex rejects these IDs since store=False.
   Fix: strip 'id' fields from input items before each API call.
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import AsyncIterator
from typing import Any

from agents import ModelSettings
from agents.models.openai_responses import OpenAIResponsesModel
from openai.types.responses import ResponseCompletedEvent, ResponseOutputItemDoneEvent

from server.utils.custom_logger import get_logger

logger = get_logger(__name__)


def _force_store_false(args: tuple, kwargs: dict) -> tuple[tuple, dict]:
    """Codex rejects requests unless store=False; enforce it regardless of caller settings."""

    def fix(ms: ModelSettings) -> ModelSettings:
        return ms if ms.store is False else dataclasses.replace(ms, store=False)

    new_args = tuple(fix(a) if isinstance(a, ModelSettings) else a for a in args)
    if isinstance(kwargs.get("model_settings"), ModelSettings):
        kwargs = {**kwargs, "model_settings": fix(kwargs["model_settings"])}
    return new_args, kwargs


def _strip_item_ids(input_items: list[Any]) -> list[Any]:
    """Remove server-assigned 'id' fields from input items so Codex doesn't try to look them up."""
    cleaned = []
    for item in input_items:
        if isinstance(item, dict):
            item = {k: v for k, v in item.items() if k != "id"}
        elif hasattr(item, "model_copy"):
            item = item.model_copy(update={"id": None})
        elif hasattr(item, "id"):
            item = copy.copy(item)
            try:
                item.id = None
            except (AttributeError, TypeError):
                pass
        cleaned.append(item)
    return cleaned


class CodexResponsesModel(OpenAIResponsesModel):
    """OpenAIResponsesModel with fixes for Codex store=False quirks."""

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        args, kwargs = _force_store_false(args, kwargs)
        return await super().get_response(*args, **kwargs)

    async def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        args, kwargs = _force_store_false(args, kwargs)
        if "previous_response_id" in kwargs and kwargs["previous_response_id"]:
            logger.info("[CODEX FIX] Stripping previous_response_id (store=False)")
            kwargs["previous_response_id"] = None

        # input is the 2nd positional arg (index 1)
        if len(args) >= 2 and isinstance(args[1], list):
            args = list(args)
            stripped = _strip_item_ids(args[1])
            logger.info("[CODEX FIX] Stripped IDs from %d input items", len(stripped))
            args[1] = stripped
            args = tuple(args)
        elif "input" in kwargs and isinstance(kwargs["input"], list):
            kwargs["input"] = _strip_item_ids(kwargs["input"])
            logger.info("[CODEX FIX] Stripped IDs from %d input items", len(kwargs["input"]))

        collected_output_items: list[Any] = []

        async for event in super().stream_response(*args, **kwargs):
            if isinstance(event, ResponseOutputItemDoneEvent):
                collected_output_items.append(event.item)

            if isinstance(event, ResponseCompletedEvent):
                if not event.response.output and collected_output_items:
                    logger.info(
                        "[CODEX FIX] Patching empty response.output with %d accumulated items",
                        len(collected_output_items),
                    )
                    event.response.output = list(collected_output_items)

            yield event
