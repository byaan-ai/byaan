from __future__ import annotations

import asyncio
import os
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)
from claude_agent_sdk.types import TextBlock, ToolUseBlock

from server.config.storage import handoffs_root
from server.schemas.handoff import HandoffPaths
from server.services.handoff_checks import path_allowed
from server.utils.custom_logger import get_logger
from server.utils.general import build_expanded_path, find_claude_cli_path

logger = get_logger(__name__)

MCP_SERVER = "byaan_handoff"
_PROMPT_KEY = "__handoff_prompt__"
STALL_SECONDS = 600
POLL_SECONDS = 5
_FILE_TOOLS_READ = {"Read", "Glob", "Grep", "LS"}
_FILE_TOOLS_WRITE = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "PWD",
    "LANG",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_AGENT_SDK_VERSION",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS",
    "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING",
    "CLAUDE_CODE_SDK_READS_SESSION_STATE",
)


class AgentUnavailable(Exception):
    pass


@dataclass
class AgentRunSpec:
    cwd: Path
    home: Path
    prompt: str
    system_prompt: str
    oauth_token: str
    paths: HandoffPaths
    minutes: int
    model: str | None = None
    resume_session_id: str | None = None
    data_tools: list[Any] = field(default_factory=list)
    data_context: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentRunResult:
    outcome: str
    session_id: str | None = None
    cost_usd: float = 0.0
    error: str | None = None
    final_text: str | None = None


class RunHooks(Protocol):
    async def on_tool(self, name: str, target: str | None) -> None: ...
    async def report_progress(self, step: str, note: str, estimate: float | None) -> str: ...
    async def ask_human(self, question: str, options: list[str]) -> str: ...
    async def submit_result(self, title: str, summary: str, tests: str, risks: str, steps: list[str]) -> str: ...
    async def pending_followups(self) -> list[tuple[Any, str]]: ...
    async def mark_delivered(self, message_ids: list[Any]) -> None: ...
    async def stop_requested(self) -> bool: ...
    def waiting_seconds(self) -> float: ...
    def is_waiting(self) -> bool: ...


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(block.text for block in content if isinstance(block, TextBlock))


def _scrubbed_cli(claude_bin: str) -> str:
    """Wrapper that drops every inherited variable except the allowlist; the SDK always merges os.environ."""
    bin_dir = handoffs_root() / ".bin"
    bin_dir.mkdir(exist_ok=True)
    os.chmod(bin_dir, 0o700)
    script = bin_dir / "claude-scrubbed"
    allowed = "|".join(_ENV_ALLOWLIST)
    content = (
        "#!/bin/sh\n"
        "for name in $(env | sed -n 's/^\\([A-Za-z_][A-Za-z0-9_]*\\)=.*/\\1/p'); do\n"
        f'  case "$name" in {allowed}) ;; *) unset "$name" ;; esac\n'
        "done\n"
        "export DISABLE_AUTOUPDATER=1\n"
        "umask 0002\n"
        f'exec "{claude_bin}" "$@"\n'
    )
    if not script.exists() or script.read_text() != content:
        tmp = script.with_suffix(".tmp")
        tmp.write_text(content)
        os.chmod(tmp, 0o700)
        tmp.replace(script)
    return str(script)


def _bundled_cli() -> str | None:
    import claude_agent_sdk

    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    return str(bundled) if bundled.is_file() and os.access(bundled, os.X_OK) else None


def resolve_cli() -> tuple[str, bool]:
    agent_cli = os.getenv("HANDOFF_AGENT_CLI")
    if agent_cli:
        # A deployment that asks for the isolated agent user must never fall back to running Claude as Byaan itself
        if not os.access(agent_cli, os.X_OK):
            raise AgentUnavailable(f"The isolated Claude Code wrapper {agent_cli} is missing or not executable")
        return agent_cli, True
    claude_bin = find_claude_cli_path(search_path=build_expanded_path()) or shutil.which("claude") or _bundled_cli()
    if not claude_bin:
        raise AgentUnavailable("Claude Code CLI not found on this Byaan host")
    return _scrubbed_cli(claude_bin), False


def _tool_target(tool_input: dict[str, Any]) -> str | None:
    for key in ("file_path", "path", "notebook_path", "pattern", "command"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value[:200]
    return None


def _relative(path: str, root: Path) -> str | None:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return str(candidate.resolve().relative_to(root.resolve()))
    except ValueError:
        return None


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def build_path_guard(root: Path, paths: HandoffPaths) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def guard(hook_input: dict[str, Any], _tool_use_id: str | None, _context: Any) -> dict[str, Any]:
        name = hook_input.get("tool_name", "")
        tool_input = hook_input.get("tool_input") or {}
        if name not in _FILE_TOOLS_READ and name not in _FILE_TOOLS_WRITE:
            return {}
        raw = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path")
        if not raw:
            return {}
        rel = _relative(raw, root)
        if rel is None:
            return _deny("Only files inside the repository working copy may be accessed.")
        if name in _FILE_TOOLS_WRITE and not path_allowed(rel, paths):
            return _deny(f"Editing {rel} is not allowed by this repository's handoff policy.")
        return {}

    return guard


def _build_mcp_server(hooks: RunHooks, data_tools: list[Any], data_context: dict[str, Any]):
    def schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {"type": "object", "properties": properties, "required": required}

    @tool(
        "report_progress",
        "Report a milestone to the people who requested this fix. Call it after meaningful steps.",
        schema(
            {
                "step": {"type": "string", "description": "Short name of the milestone"},
                "note": {"type": "string", "description": "One or two sentences on what was done"},
                "estimate": {"type": "number", "description": "Rough completion between 0 and 1"},
            },
            ["step", "note"],
        ),
    )
    async def report_progress(args: dict[str, Any]) -> dict[str, Any]:
        estimate = args.get("estimate")
        reply = await hooks.report_progress(
            str(args.get("step", "")),
            str(args.get("note", "")),
            float(estimate) if isinstance(estimate, int | float) else None,
        )
        return {"content": [{"type": "text", "text": reply}]}

    @tool(
        "ask_human",
        "Ask the requesters a question when you are blocked. Waits up to 10 minutes for an answer.",
        schema(
            {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            },
            ["question"],
        ),
    )
    async def ask_human(args: dict[str, Any]) -> dict[str, Any]:
        options = [str(o) for o in (args.get("options") or [])][:5]
        reply = await hooks.ask_human(str(args.get("question", "")), options)
        return {"content": [{"type": "text", "text": reply}]}

    @tool(
        "submit_result",
        "Submit the final result when the fix is complete. It becomes the pull request description.",
        schema(
            {
                "title": {"type": "string", "description": "Pull request title, under 70 characters"},
                "summary": {"type": "string"},
                "tests": {"type": "string", "description": "Tests added or changed"},
                "risks": {"type": "string", "description": "Risks and assumptions"},
                "steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ordered list of what you did, one short line each, including your final review",
                },
            },
            ["title", "summary", "steps"],
        ),
    )
    async def submit_result(args: dict[str, Any]) -> dict[str, Any]:
        reply = await hooks.submit_result(
            str(args.get("title", "")),
            str(args.get("summary", "")),
            str(args.get("tests", "")),
            str(args.get("risks", "")),
            [str(step) for step in (args.get("steps") or [])][:30],
        )
        return {"content": [{"type": "text", "text": reply}]}

    tools = [report_progress, ask_human, submit_result]
    if data_tools:
        from server.services.claude_mcp_converter import convert_openai_tools_batch

        tools.extend(convert_openai_tools_batch(data_tools, context_getter=lambda: data_context))
    return create_sdk_mcp_server(name=MCP_SERVER, version="1.0.0", tools=tools)


def _is_rate_limited(message: ResultMessage) -> bool:
    text = " ".join(str(e) for e in (message.errors or [])) + " " + str(message.result or "")
    return message.api_error_status == 429 or "rate limit" in text.lower() or "usage limit" in text.lower()


class ClaudeCodeAdapter:
    name = "claude_code"

    async def run(self, spec: AgentRunSpec, hooks: RunHooks) -> AgentRunResult:
        cli_path, _ = resolve_cli()

        options = ClaudeAgentOptions(
            cwd=str(spec.cwd),
            cli_path=cli_path,
            model=spec.model,
            permission_mode="bypassPermissions",
            setting_sources=[],
            strict_mcp_config=True,
            mcp_servers={MCP_SERVER: _build_mcp_server(hooks, spec.data_tools, spec.data_context)},
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[build_path_guard(spec.cwd, spec.paths)])]},
            system_prompt={"type": "preset", "preset": "claude_code", "append": spec.system_prompt},
            env={
                "HOME": str(spec.home),
                "PATH": build_expanded_path(),
                "CLAUDE_CODE_OAUTH_TOKEN": spec.oauth_token,
            },
            resume=spec.resume_session_id,
            extra_args={"replay-user-messages": None},
            stderr=lambda line: logger.debug(f"[HANDOFF CLI] {line[:300]}"),
        )
        return await self._drive(options, spec, hooks)

    async def _drive(self, options: ClaudeAgentOptions, spec: AgentRunSpec, hooks: RunHooks) -> AgentRunResult:
        outstanding: dict[Any, str] = {_PROMPT_KEY: spec.prompt}
        last_message = [time.monotonic()]
        result = AgentRunResult(outcome="error", error="Claude stopped without a result")

        async with ClaudeSDKClient(options) as client:
            await client.query(spec.prompt)

            async def pump() -> None:
                async for message in client.receive_messages():
                    last_message[0] = time.monotonic()
                    if isinstance(message, UserMessage):
                        echoed = _message_text(message.content)
                        match = next((mid for mid, text in outstanding.items() if text == echoed), None)
                        if match is not None:
                            outstanding.pop(match)
                            if match != _PROMPT_KEY:
                                await hooks.mark_delivered([match])
                    elif isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, ToolUseBlock):
                                await hooks.on_tool(block.name, _tool_target(block.input or {}))
                            elif isinstance(block, TextBlock):
                                result.final_text = block.text
                    elif isinstance(message, ResultMessage):
                        result.session_id = message.session_id
                        result.cost_usd += message.total_cost_usd or 0.0
                        if message.is_error:
                            detail = str(message.result or "") or ", ".join(map(str, message.errors or []))
                            result.error = detail[:1000] or "Claude reported an error"
                            if _PROMPT_KEY in outstanding:
                                continue
                            result.outcome = "rate_limited" if _is_rate_limited(message) else "error"
                            return
                        result.outcome, result.error = "success", None
                        if not outstanding:
                            return

            async def supervise() -> str:
                started = time.monotonic()
                while True:
                    await asyncio.sleep(POLL_SECONDS)
                    if await hooks.stop_requested():
                        return "cancelled"
                    for message_id, text in await hooks.pending_followups():
                        if message_id not in outstanding:
                            outstanding[message_id] = text
                            await client.query(text)
                    if hooks.is_waiting():
                        last_message[0] = time.monotonic()
                        continue
                    now = time.monotonic()
                    if now - last_message[0] > STALL_SECONDS:
                        return "stalled"
                    if now - started - hooks.waiting_seconds() > spec.minutes * 60:
                        return "timeout"

            pump_task = asyncio.create_task(pump())
            supervise_task = asyncio.create_task(supervise())
            try:
                await asyncio.wait({pump_task, supervise_task}, return_when=asyncio.FIRST_COMPLETED)
                if supervise_task.done() and not supervise_task.cancelled() and supervise_task.exception() is None:
                    reason = supervise_task.result()
                    pump_task.cancel()
                    try:
                        await client.interrupt()
                    except Exception as exc:
                        logger.warning(f"[HANDOFF] Interrupt failed: {exc}")
                    result.outcome = reason
                    result.error = {
                        "cancelled": "Stopped by a person",
                        "stalled": f"No activity from Claude for {STALL_SECONDS // 60} minutes",
                        "timeout": f"Time limit of {spec.minutes} minutes reached",
                    }[reason]
                else:
                    supervise_task.cancel()
                    for task in (pump_task, supervise_task):
                        if task.done() and not task.cancelled() and task.exception() is not None:
                            raise task.exception()
            finally:
                for task in (pump_task, supervise_task):
                    task.cancel()
                await asyncio.gather(pump_task, supervise_task, return_exceptions=True)
        return result
