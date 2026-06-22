from __future__ import annotations

import asyncio
import re
from typing import Any

from litellm import acompletion
from sqlalchemy.ext.asyncio import AsyncSession

from server.services.claude_mcp_service import stream_claude_with_mcp_tools
from server.services.llm_service import ModelService
from server.tools.agentic import search_datasets
from server.utils.custom_logger import get_logger
from server.utils.litellm_utils import supports_custom_temperature

logger = get_logger(__name__)

TITLE_SYSTEM_PROMPT = (
    "You are a concise title generator. Produce a short 3-5 word Title Case title. No quotes or extra punctuation."
)


def _build_title_prompt(user_message: str, assistant_response: str) -> str:
    user_truncated = user_message[:500]
    assistant_truncated = assistant_response[:500]

    if assistant_response.strip():
        return f"Generate a concise 3-5 word title for this conversation. Only respond with the title, no quotes or punctuation.\n\nUser: {user_truncated}\n\nAssistant: {assistant_truncated}"
    return f"Generate a concise 3-5 word title based on this user's question. Only respond with the title, no quotes or punctuation.\n\nUser: {user_truncated}"


def _build_title_messages(user_message: str, assistant_response: str) -> list[dict]:
    user_truncated = user_message[:500]
    assistant_truncated = assistant_response[:500]

    if assistant_response.strip():
        user_content = f"Generate a title for this conversation:\n\nUser: {user_truncated}\n\nAssistant: {assistant_truncated}\n\nTitle:"
    else:
        user_content = f"Generate a title based on this user question:\n\nUser: {user_truncated}\n\nTitle:"

    return [
        {"role": "system", "content": TITLE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _fallback_title(user_message: str) -> str:
    fallback = user_message[:50].strip()
    if len(user_message) > 50:
        fallback += "..."
    return fallback or "New Conversation"


def _clean_title(title: str, user_message: str) -> str:
    title = re.sub(
        r"\[\[TOOL_CALL:.*?\]\](?=\[\[TOOL_CALL|[^\[\]]|$)",
        "",
        title,
        flags=re.DOTALL,
    )
    title = re.sub(r"Tool executed successfully\s*", "", title)
    title = title.strip().strip('"').strip("'")
    if len(title) > 100:
        title = title[:100]
    return title if title else _fallback_title(user_message)


async def _generate_title_codex(
    user_message: str,
    assistant_response: str,
    llm_connection_id: str,
    session: AsyncSession,
) -> str:
    from openai import AsyncOpenAI

    from server.services.codex_oauth_service import get_valid_codex_token

    access_token, account_id = await get_valid_codex_token(llm_connection_id, session)

    codex_headers = {
        "OpenAI-Beta": "responses=experimental",
        "originator": "codex_cli_rs",
    }
    if account_id:
        codex_headers["ChatGPT-Account-Id"] = account_id

    client = AsyncOpenAI(
        api_key=access_token,
        base_url="https://chatgpt.com/backend-api/codex",
        default_headers=codex_headers,
    )

    prompt = _build_title_prompt(user_message, assistant_response)
    stream = await client.responses.create(
        model="gpt-5.4",
        instructions=TITLE_SYSTEM_PROMPT,
        input=[{"role": "user", "content": prompt}],
        store=False,
        stream=True,
    )

    title = ""
    async for event in stream:
        if event.type == "response.output_text.delta":
            title += event.delta
        elif event.type == "response.completed":
            break

    logger.info(f"Generated title via Codex: {title}")
    return _clean_title(title, user_message)


async def _generate_title_claude_sdk(
    user_message: str,
    assistant_response: str,
    model: str | None,
) -> str:
    prompt = _build_title_prompt(user_message, assistant_response)

    async def _protected():
        generated_title = ""
        sdk_error: str | None = None
        async for event in stream_claude_with_mcp_tools(
            prompt=prompt,
            tools=[search_datasets],
            model=model,
            instructions=TITLE_SYSTEM_PROMPT,
            context={},
        ):
            etype = event.get("type")
            if etype == "content":
                generated_title += event.get("text", "")
            elif etype == "error":
                sdk_error = event.get("error") or "Unknown Claude SDK error"
            elif etype == "done":
                break
        return generated_title, sdk_error

    generated_title, sdk_error = await asyncio.shield(_protected())
    if sdk_error and not generated_title.strip():
        logger.warning(f"[TITLE GEN] Claude SDK error, using fallback: {sdk_error}")
        return _fallback_title(user_message)
    logger.info(f"Generated title via Claude SDK: {generated_title!r}")
    return _clean_title(generated_title, user_message)


def _extract_content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                btype = block.get("type")
                if btype in ("text", "output_text") or btype is None:
                    text = block.get("text") or block.get("content") or ""
                    if isinstance(text, str):
                        parts.append(text)
        return "".join(parts)
    return str(content)


async def _generate_title_litellm(
    user_message: str,
    assistant_response: str,
    llm_connection_id: str,
    model: str | None,
) -> str:
    model_instance = await ModelService.get_litellm_model_instance(llm_connection_id, model)
    if not model_instance:
        raise ValueError(f"Failed to get model instance for connection: {llm_connection_id}")

    messages = _build_title_messages(user_message, assistant_response)

    completion_kwargs: dict = {
        "model": model_instance.model,
        "messages": messages,
        "max_tokens": 2048,
    }
    if supports_custom_temperature(model_instance.model):
        completion_kwargs["temperature"] = 0.7

    response = await acompletion(**completion_kwargs)

    message = response.choices[0].message
    raw_content = getattr(message, "content", None)
    content = _extract_content_text(raw_content).strip()
    if not content:
        reasoning = (getattr(message, "reasoning_content", None) or "").strip()
        if reasoning:
            lines = [line.strip() for line in reasoning.splitlines() if line.strip()]
            content = lines[-1] if lines else ""

    if not content:
        logger.warning(
            f"[TITLE GEN] LiteLLM returned empty content. model={model_instance.model} "
            f"finish_reason={getattr(response.choices[0], 'finish_reason', None)} "
            f"raw_content_type={type(raw_content).__name__} raw={repr(raw_content)[:500]}"
        )

    title = content.strip()
    logger.info(f"Generated title via LiteLLM: {title!r}")
    return _clean_title(title, user_message)


async def generate_notebook_title(
    user_message: str,
    assistant_response: str,
    llm_connection_id: str,
    model: str | None = None,
    session: AsyncSession | None = None,
    use_claude_sdk: bool = False,
) -> str:
    try:
        logger.info("Generating notebook title")

        if use_claude_sdk:
            return await _generate_title_claude_sdk(user_message, assistant_response, model)

        if session:
            from server.repositories.llm_connections import LLMConnectionRepository

            repo = LLMConnectionRepository(session)
            connection = await repo.get(llm_connection_id)
            if connection and connection.type == "codex":
                return await _generate_title_codex(user_message, assistant_response, llm_connection_id, session)

        return await _generate_title_litellm(user_message, assistant_response, llm_connection_id, model)

    except asyncio.CancelledError:
        logger.warning("[TITLE GEN] Title generation cancelled, using fallback")
        return _fallback_title(user_message)
    except Exception as e:
        logger.error(f"Error generating title: {e}", exc_info=True)
        return _fallback_title(user_message)
