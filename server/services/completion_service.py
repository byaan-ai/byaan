"""Unified completion service that handles both LiteLLM and Claude Code auth paths."""

import re
from typing import Literal
from uuid import UUID

from litellm import acompletion
from litellm.exceptions import (
    AuthenticationError,
    BadRequestError,
    ContextWindowExceededError,
    RateLimitError,
)
from sqlalchemy.ext.asyncio import AsyncSession

from server.services.claude_mcp_service import DISALLOWED_BUILTIN_TOOLS, stream_claude_with_mcp_tools
from server.services.llm_service import ModelService
from server.services.unified_agent import is_using_claude_code_auth
from server.utils.custom_logger import get_logger
from server.utils.litellm_utils import supports_custom_temperature

logger = get_logger(__name__)

ALL_BUILTIN_TOOLS = [*DISALLOWED_BUILTIN_TOOLS, "Read", "ToolSearch"]
_TOOL_CALL_RE = re.compile(r"\[\[TOOL_CALL:.*?\]\](?=\[\[TOOL_CALL|[^\[\]]|$)", re.DOTALL)

CompletionErrorReason = Literal[
    "auth",
    "rate_limit",
    "context_overflow",
    "empty",
    "model_unavailable",
    "unknown",
]


class CompletionError(Exception):
    """Structured error from CompletionService so callers can surface the real cause."""

    def __init__(self, reason: CompletionErrorReason, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason
        self.message = message


def _classify(exc: BaseException) -> CompletionError:
    if isinstance(exc, CompletionError):
        return exc
    if isinstance(exc, AuthenticationError):
        return CompletionError("auth", str(exc))
    if isinstance(exc, RateLimitError):
        return CompletionError("rate_limit", str(exc))
    if isinstance(exc, ContextWindowExceededError):
        return CompletionError("context_overflow", str(exc))
    if isinstance(exc, BadRequestError):
        msg = str(exc).lower()
        if "context" in msg or "token" in msg and "max" in msg:
            return CompletionError("context_overflow", str(exc))
        return CompletionError("unknown", str(exc))
    return CompletionError("unknown", str(exc))


class CompletionService:
    """Service for simple LLM completions that works with both LiteLLM and Claude Code auth."""

    @staticmethod
    async def complete(
        prompt: str,
        llm_connection_id: UUID | str,
        session: AsyncSession,
        system_prompt: str | None = None,
        use_claude_sdk: bool | None = None,
        model: str | None = None,
    ) -> str | None:
        """
        Get a completion from the appropriate LLM based on connection type.

        Args:
            prompt: The user prompt/message
            llm_connection_id: The LLM connection to use
            session: Database session
            system_prompt: Optional system instructions
            use_claude_sdk: If provided, skip the DB auth check and use this value directly
            model: Optional model identifier to pass to the underlying provider

        Returns:
            The completion text, or None if failed
        """
        llm_id = str(llm_connection_id)

        try:
            if use_claude_sdk is None:
                use_claude_sdk = await is_using_claude_code_auth(llm_id, session)

            if use_claude_sdk:
                return await CompletionService._complete_with_claude_sdk(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    model=model,
                )
            else:
                return await CompletionService._complete_with_litellm(
                    prompt=prompt,
                    llm_connection_id=llm_id,
                    system_prompt=system_prompt,
                    model=model,
                )
        except Exception as e:
            err = _classify(e)
            logger.error(f"Completion failed [{err.reason}]: {err.message}", exc_info=True)
            raise err from e

    @staticmethod
    async def _complete_with_claude_sdk(
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
    ) -> str:
        """Complete using Claude Code SDK with all builtin tools disabled.

        If the model emits only tool calls (post-strip result is empty), retry once with a
        stricter instruction. If still empty, raise CompletionError('empty', ...).
        """
        result = await CompletionService._run_claude_sdk_once(prompt, system_prompt, model)
        if result:
            return result

        retry_system = ((system_prompt + "\n\n") if system_prompt else "") + (
            "Output only the final markdown document. Do not call any tools. "
            "Do not emit '[[TOOL_CALL:...]]'. Begin your response with the first line of the markdown."
        )
        result = await CompletionService._run_claude_sdk_once(prompt, retry_system, model)
        if result:
            return result
        raise CompletionError("empty", "Claude SDK produced only tool-call output")

    @staticmethod
    async def _run_claude_sdk_once(prompt: str, system_prompt: str | None, model: str | None = None) -> str:
        result = ""
        gen = stream_claude_with_mcp_tools(
            prompt=prompt,
            tools=None,
            model=model,
            instructions=system_prompt,
            context=None,
            disallowed_tools_override=ALL_BUILTIN_TOOLS,
            max_turns=1,
        )
        try:
            async for event in gen:
                if event.get("type") == "content":
                    text = event.get("text", "")
                    if "[[TOOL_CALL:" in text or text.strip() == "Tool executed successfully":
                        continue
                    result += text
                elif event.get("type") == "done":
                    break
        finally:
            try:
                await gen.aclose()
            except Exception:
                pass

        result = _TOOL_CALL_RE.sub("", result)
        result = result.replace("\n\nTool executed successfully\n\n", "")
        return result.strip()

    @staticmethod
    async def _complete_with_litellm(
        prompt: str,
        llm_connection_id: str,
        system_prompt: str | None = None,
        model: str | None = None,
    ) -> str | None:
        """Complete using LiteLLM."""
        model_instance = await ModelService.get_litellm_model_instance(llm_connection_id, model=model)
        if not model_instance:
            raise CompletionError(
                "model_unavailable",
                f"Could not create model instance for LLM connection {llm_connection_id}",
            )

        from server.services.codex_responses_model import CodexResponsesModel

        if isinstance(model_instance, CodexResponsesModel):
            return await CompletionService._complete_with_agent_model(model_instance, prompt, system_prompt)

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        completion_kwargs: dict = {
            "model": model_instance.model,
            "messages": messages,
        }
        if getattr(model_instance, "api_key", None):
            completion_kwargs["api_key"] = model_instance.api_key
        if getattr(model_instance, "base_url", None):
            completion_kwargs["base_url"] = model_instance.base_url
        if supports_custom_temperature(model_instance.model):
            completion_kwargs["temperature"] = 0

        response = await acompletion(**completion_kwargs)
        content = response.choices[0].message.content  # type: ignore[union-attr]
        stripped = content.strip() if content else ""
        if not stripped:
            raise CompletionError("empty", "LiteLLM returned empty content")
        return stripped

    @staticmethod
    async def _complete_with_agent_model(model_instance, prompt: str, system_prompt: str | None) -> str:
        """Single-turn completion through the Agents SDK for models that need its request handling (Codex)."""
        from agents import Agent, ModelSettings, Runner

        agent = Agent(
            name="completion",
            instructions=system_prompt or "You are a helpful assistant. Answer directly with the requested output.",
            model=model_instance,
            model_settings=ModelSettings(store=False),
        )
        result = Runner.run_streamed(agent, prompt, max_turns=1)
        async for _ in result.stream_events():
            pass
        output = result.final_output.strip() if isinstance(result.final_output, str) else ""
        if not output:
            raise CompletionError("empty", "Agent model returned empty content")
        return output
