"""
Claude Agent SDK with MCP Tools - Fixed Version
------------------------------------------------
Uses claude-agent-sdk (>= 0.1.37) with ProcessTransport fix.
Converts OpenAI Agents SDK tools to Claude MCP format.
"""

import asyncio
import json
import os
import signal
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

from agents import FunctionTool
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, SystemMessage, create_sdk_mcp_server
from claude_agent_sdk._errors import CLINotFoundError
from claude_agent_sdk._internal.message_parser import MessageParseError

from server.services.claude_mcp_converter import convert_openai_tools_batch
from server.services.claude_oauth_service import (
    get_active_token_with_refresh,
    get_auth_method,
)
from server.tools.tool_friendly_descriptions import get_user_friendly_tool_description
from server.utils.custom_logger import get_logger
from server.utils.general import build_expanded_path, find_claude_cli_path

logger = get_logger(__name__)


def _install_safe_message_parser():
    """Monkey-patch the SDK's parse_message to gracefully handle unknown message types."""
    import claude_agent_sdk._internal.message_parser as mp

    if getattr(mp, "_byaan_patched", False):
        return

    _original_parse = mp.parse_message

    def _safe_parse(raw_data):
        try:
            return _original_parse(raw_data)
        except MessageParseError:
            msg_type = raw_data.get("type", "unknown") if isinstance(raw_data, dict) else "unknown"
            logger.warning(f"[CLAUDE MCP] Ignoring unknown message type from SDK: {msg_type}")
            return SystemMessage(subtype="unknown", data=raw_data if isinstance(raw_data, dict) else {})

    mp.parse_message = _safe_parse
    mp._byaan_patched = True


def _is_cli_not_found_error(error: Exception) -> bool:
    if isinstance(error, CLINotFoundError):
        return True
    error_msg = str(error).lower()
    if "no such file or directory" not in error_msg:
        return False
    stripped = error_msg.rstrip("'\" ")
    return stripped.endswith("/claude") or stripped.endswith("'claude") or stripped == "claude"


DISALLOWED_BUILTIN_TOOLS = [
    "WebSearch",
    "WebFetch",
    # "Read", # Allow Read for file access
    "Write",
    "Edit",
    "Bash",
    "Glob",
    "Grep",
    "Task",
    "NotebookEdit",
    "AskUserQuestion",
    "TodoWrite",
]


async def stream_claude_with_mcp_tools(
    prompt: str,
    tools: list[FunctionTool] | None = None,
    model: str | None = None,
    instructions: str | None = None,
    context: dict | None = None,
    session_id: str | None = None,
    agent_session: Any = None,
    resume_session_id: (str | None) = None,  # Claude SDK session ID for conversation continuity
    attachments: list[dict[str, str]] | None = None,  # Image attachments for multimodal
    disallowed_tools_override: list[str] | None = None,
    max_turns: int | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Stream Claude responses using ClaudeSDKClient with MCP tools and native session management.

    Uses ClaudeSDKClient with SDK's built-in session resume:
    - Conversation history is automatically loaded via the 'resume' parameter
    - Claude SDK stores sessions in ~/.claude/ and loads them when resume_session_id is provided
    - SDK handles conversation history internally - no manual injection needed
    - Multi-turn tool execution with full context awareness


    Args:
        prompt: Current user message (only the new message, not history)
        tools: List of OpenAI FunctionTools to convert to MCP
        model: Model name (optional)
        instructions: System prompt
        context: Dict with notebook_id, database_schemas, etc.
        session_id: Notebook session ID (for logging/identification)
        agent_session: Agent session for compatibility (not used with Claude SDK resume)
        resume_session_id: Claude SDK session ID to resume (enables conversation history)
        attachments: List of image attachments with keys: file_name, mime_type, file_data (base64)

    Yields:
        SSE events matching OpenAI Agents SDK format, plus session_id event
    """
    client = None
    try:
        _install_safe_message_parser()
        logger.info(f"[CLAUDE MCP] Starting with {len(tools) if tools else 0} tools")

        # Create context getter function that returns current context
        def get_current_context():
            return context or {}

        # Step 1: Convert OpenAI tools to Claude MCP format
        mcp_tools = []
        if tools:
            logger.info("[CLAUDE MCP] Converting OpenAI tools to MCP format")
            mcp_tools = convert_openai_tools_batch(tools, context_getter=get_current_context)
            logger.info(f"[CLAUDE MCP] Converted {len(mcp_tools)} tools successfully")

        # Step 2: Create MCP server with converted tools
        mcp_server = None
        if mcp_tools:
            logger.info("[CLAUDE MCP] Creating SDK MCP server")
            mcp_server = create_sdk_mcp_server(name="byaan_tools", version="1.0.0", tools=mcp_tools)
            logger.info("[CLAUDE MCP] MCP server created successfully")

        # Step 3: Configure Claude options
        # Tool names are prefixed with mcp__<server_name>__<tool_name>
        mcp_servers_dict: dict = {}
        allowed_tools_list: list[str] = []

        if mcp_server:
            mcp_servers_dict = {"byaan_tools": mcp_server}
            allowed_tools_list = [f"mcp__byaan_tools__{tool.name}" for tool in mcp_tools]
            logger.info(f"[CLAUDE MCP] Allowed tools: {allowed_tools_list[:3]}...")

        # Block tools from the global "byaan" stdio MCP server (defined in ~/.claude.json).
        # Claude CLI auto-loads it, creating duplicate tools with mcp__byaan__ prefix that
        # lack the correct notebook/dataset context. Only mcp__byaan_tools__ should be used.
        disallowed_stdio_tools = []
        if mcp_tools:
            disallowed_stdio_tools = [f"mcp__byaan__{tool.name}" for tool in mcp_tools]

        # Stderr callback to capture CLI errors for debugging
        def stderr_callback(line: str):
            logger.warning(f"[CLAUDE CLI STDERR] {line}")

        # Build env dict for Claude CLI subprocess
        cli_env: dict[str, str] = {}

        # Give the SDK subprocess the same comprehensive PATH that Tauri gives its sidecar,
        # so the CLI can resolve node/npm at runtime (e.g. for MCP servers).
        expanded_path = build_expanded_path()
        cli_env["PATH"] = expanded_path

        # Set CLAUDE_CODE_OAUTH_TOKEN from active token (long-term or OAuth access token)
        # This also refreshes expired OAuth tokens automatically
        active_token = await get_active_token_with_refresh()
        auth_method = get_auth_method()
        if active_token:
            cli_env["CLAUDE_CODE_OAUTH_TOKEN"] = active_token
            logger.info(f"[CLAUDE MCP] Using {auth_method} authentication")
        else:
            logger.warning("[CLAUDE MCP] No active Claude Code authentication found")

        options_kwargs = {
            "system_prompt": instructions if instructions else None,
            "mcp_servers": mcp_servers_dict,
            "allowed_tools": allowed_tools_list,
            "disallowed_tools": (disallowed_tools_override or DISALLOWED_BUILTIN_TOOLS) + disallowed_stdio_tools,
            "include_partial_messages": True,
            "max_turns": max_turns if max_turns is not None else 50,
            "stderr": stderr_callback,
            "env": cli_env,
        }

        if model:
            sdk_model = model.split("/", 1)[1] if model.startswith("claude_code/") else model
            options_kwargs["model"] = sdk_model
            logger.info(f"[CLAUDE MCP] Passing model to SDK: {sdk_model}")

        # Auto-detect CLI path using the same expanded PATH so discovery and launch agree
        cli_path = find_claude_cli_path(search_path=expanded_path)
        if cli_path:
            options_kwargs["cli_path"] = cli_path
            logger.info(f"[CLAUDE MCP] Using Claude CLI at: {cli_path}")
        else:
            logger.warning(
                "[CLAUDE MCP] Claude CLI not found in PATH or common locations. "
                "Install with: curl -fsSL https://claude.ai/install.sh | bash  "
                "or: npm install -g @anthropic-ai/claude-code"
            )
            logger.debug(f"[CLAUDE MCP] PATH searched: {expanded_path[:500]}")

        # Add resume parameter if we have a previous session
        if resume_session_id:
            options_kwargs["resume"] = resume_session_id
            logger.info(f"[CLAUDE MCP] Resuming session: {resume_session_id}")
        else:
            logger.info("[CLAUDE MCP] Starting new session (no resume_session_id)")

        options = ClaudeAgentOptions(**options_kwargs)

        # Step 4: Create client (resume will auto-load conversation history)
        logger.info("[CLAUDE MCP] Creating ClaudeSDKClient")
        client = ClaudeSDKClient(options)
        cli_process = None
        cli_process_pid: int | None = None

        # Step 5: Connect with just the new user message
        logger.info("[CLAUDE MCP] Connecting to Claude (SDK will auto-load history if resuming)")
        max_connect_retries = 3 if resume_session_id else 2
        for retry in range(max_connect_retries):
            try:
                await client.connect()
                transport = getattr(client, "_transport", None)
                cli_process = getattr(transport, "_process", None) if transport else None
                cli_process_pid = cli_process.pid if cli_process and cli_process.pid else None
                break
            except Exception as connect_error:
                error_msg = str(connect_error) or type(connect_error).__name__
                if _is_cli_not_found_error(connect_error):
                    logger.error(f"[CLAUDE MCP] Claude CLI not found. PATH: {expanded_path[:500]}")
                    yield {
                        "type": "error",
                        "error": (
                            "Claude CLI not found. Install with: curl -fsSL https://claude.ai/install.sh | bash  "
                            "or: npm install -g @anthropic-ai/claude-code"
                        ),
                    }
                    return
                if retry < max_connect_retries - 1:
                    # If resume failed, fall back to a fresh session
                    if resume_session_id and "resume" in options_kwargs:
                        logger.warning(
                            f"[CLAUDE MCP] Session resume failed (attempt {retry + 1}): {connect_error}. "
                            f"Falling back to new session..."
                        )
                        del options_kwargs["resume"]
                        resume_session_id = None
                        options = ClaudeAgentOptions(**options_kwargs)
                    else:
                        logger.warning(f"[CLAUDE MCP] Connect attempt {retry + 1} failed: {connect_error}. Retrying...")
                    await asyncio.sleep(0.5)
                    # Recreate client for retry
                    client = ClaudeSDKClient(options)
                else:
                    logger.error(
                        f"[CLAUDE MCP] Failed to connect to Claude CLI after {max_connect_retries} attempts: {connect_error}",
                        exc_info=True,
                    )
                    yield {
                        "type": "error",
                        "error": f"Failed to connect to Claude: {error_msg}",
                    }
                    return

        logger.info("[CLAUDE MCP] Successfully connected to Claude CLI")

        # Small delay to ensure CLI process is fully initialized
        await asyncio.sleep(0.1)

        # Send the current user message using query()
        # SDK maintains all conversation context internally
        logger.info(f"[CLAUDE MCP] Model: {model if model else 'default'}")

        # Build multimodal content if attachments are present
        try:
            if attachments:
                logger.info(f"[CLAUDE MCP] Processing {len(attachments)} image attachments")

                # Build content array with text and image blocks
                # The Claude SDK query() expects either:
                # - A string (simple text prompt)
                # - An AsyncIterable of properly formatted messages
                # For multimodal, we need to construct the content array properly
                content_blocks = [{"type": "text", "text": prompt}]

                for attachment in attachments:
                    logger.info(
                        f"[CLAUDE MCP] Added image: {attachment.get('file_name', 'unknown')} ({attachment['mime_type']})"
                    )
                    content_blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": attachment["mime_type"],
                                "data": attachment["file_data"],
                            },
                        }
                    )

                # Create async generator that yields a single properly formatted message
                async def multimodal_message():
                    yield {
                        "type": "user",
                        "message": {"role": "user", "content": content_blocks},
                        "parent_tool_use_id": None,
                    }

                logger.info(f"[CLAUDE MCP] Sending multimodal message with {len(content_blocks)} content blocks")
                await client.query(multimodal_message())
            else:
                logger.info(f"[CLAUDE MCP] Sending user message: {prompt[:100]}...")
                await client.query(prompt)
        except Exception as query_error:
            error_msg = str(query_error) or type(query_error).__name__
            error_type = type(query_error).__name__
            # Check for common error patterns
            if "stdin" in error_msg.lower() or error_type in (
                "BrokenPipeError",
                "ClosedResourceError",
                "CLIConnectionError",
            ):
                logger.error(
                    f"[CLAUDE MCP] Claude CLI process terminated unexpectedly ({error_type}). "
                    f"This usually indicates an MCP server initialization failure or authentication issue. "
                    f"Original error: {query_error}",
                    exc_info=True,
                )
                yield {
                    "type": "error",
                    "error": "Claude CLI process terminated unexpectedly. Please try again. If the issue persists, check that Claude Code is properly authenticated (run 'claude' in terminal to verify).",
                }
                return
            raise  # Re-raise other exceptions to be caught by outer handler

        # Step 6: Receive and stream messages
        full_response = ""
        message_count = 0
        captured_session_id = None

        # HTML edit tracking
        HTML_EDIT_START_TOOLS = {
            "start_html_generation",
            "apply_html_patch",
            "dashboard_search_replace",
        }
        HTML_CONTEXT_FETCH_TOOLS = {"get_existing_html"}
        QUERY_EXECUTION_TOOLS = {
            "execute_sql_query",
            "execute_mongo_query",
            "execute_duckdb_query",
        }
        html_edit_session_id = None
        html_context_request_id = None
        last_html_tool_id = None
        html_edit_occurred = False
        pending_datasource_events: dict[str, dict] = {}
        pending_plan_tool_ids: set[str] = set()
        pending_query_saved_events: dict[str, str] = {}
        pending_memory_updated_events: set[str] = set()
        pending_screenshot_events: dict[str, str] = {}

        async for message in client.receive_messages():
            message_count += 1
            message_type = type(message).__name__

            if message_type == "SystemMessage":
                if hasattr(message, "data"):
                    api_source = message.data.get("apiKeySource", "unknown")
                    mcp_servers_info = message.data.get("mcp_servers", [])

                    # Capture the Claude-generated session ID for conversation continuity
                    if "session_id" in message.data:
                        captured_session_id = message.data.get("session_id")

                        if captured_session_id != resume_session_id:
                            logger.info(f"[CLAUDE MCP] Captured new session ID: {captured_session_id}")

                        yield {
                            "type": "claude_session_id",
                            "session_id": captured_session_id,
                        }

                    logger.info(f"[CLAUDE MCP] API Source: {api_source}")
                    logger.info(f"[CLAUDE MCP] MCP Servers: {len(mcp_servers_info)}")

                    actual_model = message.data.get("model")
                    if actual_model:
                        logger.info(f"[CLAUDE MCP] Active model (from SDK init): {actual_model}")

            elif message_type == "StreamEvent":
                if hasattr(message, "event"):
                    event_data = message.event
                    event_type = (
                        event_data.get("type", "") if isinstance(event_data, dict) else getattr(event_data, "type", "")
                    )

                    if event_type == "content_block_delta":
                        delta_obj = event_data.get("delta", {})
                        delta_type = delta_obj.get("type") if isinstance(delta_obj, dict) else None

                        if delta_type == "text_delta":
                            text_delta = delta_obj.get("text", "")
                            if text_delta:
                                full_response += text_delta
                                yield {"type": "content", "text": text_delta}

            elif message_type == "AssistantMessage":
                # Handle tool use/result blocks only
                # Text content is already fully streamed via StreamEvent deltas
                if hasattr(message, "content"):
                    for idx, block in enumerate(message.content):
                        block_type = type(block).__name__
                        logger.info(f"[CLAUDE MCP] Block {idx}: {block_type}")

                        if hasattr(block, "text"):
                            # Capture text from TextBlock as fallback when streaming didn't work
                            text_content = block.text
                            if text_content and text_content not in full_response:
                                full_response += text_content
                                yield {"type": "content", "text": text_content}

                        # Check for tool use blocks
                        elif block_type == "ToolUseBlock":
                            tool_name = getattr(block, "name", "unknown")
                            tool_id = getattr(block, "id", "unknown")
                            tool_input = getattr(block, "input", {})

                            logger.info(f"[CLAUDE MCP] ToolUseBlock: {tool_name}, ID: {tool_id}")

                            display_name = tool_name.replace("mcp__byaan_tools__", "").replace("mcp__byaan__", "")

                            friendly_desc = get_user_friendly_tool_description(display_name)

                            if display_name in HTML_CONTEXT_FETCH_TOOLS:
                                html_context_request_id = str(uuid4())
                                logger.info(f"[CLAUDE MCP] HTML context fetch started: {display_name}")
                                yield {
                                    "type": "html_context_refresh",
                                    "stage": "start",
                                    "message": "Fetching latest dashboard HTML...",
                                    "tool_name": display_name,
                                    "tool_friendly_description": friendly_desc,
                                    "context_id": html_context_request_id,
                                    "edit_session_id": html_edit_session_id,
                                }

                            if display_name in HTML_EDIT_START_TOOLS:
                                if not html_edit_session_id:
                                    html_edit_session_id = str(uuid4())
                                if display_name in {
                                    "apply_html_patch",
                                    "dashboard_search_replace",
                                }:
                                    html_edit_occurred = True
                                last_html_tool_id = tool_id
                                logger.info(f"[CLAUDE MCP] HTML edit detected: {display_name}")
                                yield {
                                    "type": "html_edit_detected",
                                    "message": "Applying changes to HTML...",
                                    "tool_name": display_name,
                                    "tool_friendly_description": friendly_desc,
                                    "edit_session_id": html_edit_session_id,
                                }

                            if display_name in QUERY_EXECUTION_TOOLS:
                                pending_datasource_events[tool_id] = {
                                    "tool_name": display_name,
                                    "tool_input": tool_input,
                                }

                            if display_name == "emit_plan_status" and tool_input:
                                plan_event = {
                                    "type": "plan_status",
                                    "action": tool_input.get("action", ""),
                                    "notebook_id": str(context.get("notebook_id", "")) if context else None,
                                }
                                action = tool_input.get("action", "")
                                if action == "start_plan" and tool_input.get("steps_json"):
                                    try:
                                        steps = json.loads(tool_input["steps_json"])
                                        formatted = [
                                            {
                                                "name": s.get("name", f"Step {i + 1}"),
                                                "description": s.get("description", ""),
                                            }
                                            if isinstance(s, dict)
                                            else {"name": s, "description": ""}
                                            for i, s in enumerate(steps)
                                        ]
                                        plan_event["steps"] = formatted
                                        plan_event["total_steps"] = len(formatted)
                                    except json.JSONDecodeError:
                                        pass
                                elif action in ("start_step", "complete_step", "fail_step"):
                                    plan_event["step_number"] = tool_input.get("step_number", 0)
                                pending_plan_tool_ids.add(tool_id)
                                yield plan_event

                            if display_name == "save_query":
                                pending_query_saved_events[tool_id] = display_name

                            if display_name in (
                                "add_instruction",
                                "remove_instruction",
                                "add_learning",
                                "update_learning",
                                "remove_learning",
                            ):
                                pending_memory_updated_events.add(tool_id)

                            if display_name == "generate_dashboard_screenshot":
                                pending_screenshot_events[tool_id] = json.dumps(tool_input) if tool_input else "{}"

                            skill_description_override = None
                            if display_name in [
                                "execute_mongo_query",
                                "execute_sql_query",
                                "execute_duckdb_query",
                                "save_query",
                            ]:
                                args_json = json.dumps(tool_input) if tool_input else "{}"
                            elif display_name == "get_skill_definition" and tool_input:
                                skill_name = tool_input.get("skill_name", "")
                                skill_description_override = f"Loading {skill_name} skill documentation"
                                args_json = "{}"
                            elif display_name == "update_custom_skill" and tool_input:
                                skill_name = tool_input.get("skill_name", "")
                                skill_description_override = f"Updating {skill_name} skill"
                                args_json = "{}"
                            elif display_name == "execute_skill_api" and tool_input:
                                from server.services.skill_discovery import (
                                    SkillDiscovery,
                                )

                                skill_name = tool_input.get("skill_name", "")
                                skill_config = SkillDiscovery.get_skill_config(skill_name)
                                skill_display_name = skill_config.display_name if skill_config else skill_name
                                emoji = skill_config.emoji if skill_config else "🔧"
                                is_graphql = tool_input.get("is_graphql", False)
                                display_info = {
                                    "skill_name": skill_name,
                                    "display_name": skill_display_name,
                                    "emoji": emoji,
                                    "is_graphql": is_graphql,
                                    "url": tool_input.get("url", ""),
                                    "method": tool_input.get("method", "GET"),
                                }
                                if is_graphql:
                                    if tool_input.get("graphql_query"):
                                        display_info["query"] = tool_input.get("graphql_query")
                                else:
                                    if tool_input.get("body"):
                                        display_info["body"] = tool_input.get("body")
                                args_json = json.dumps(display_info)
                                skill_description_override = f"Executing {skill_display_name} API"
                            else:
                                args_json = json.dumps(tool_input) if tool_input else "{}"

                            final_desc = skill_description_override or friendly_desc
                            tool_marker = f"[[TOOL_CALL:{tool_id}:{display_name}|{final_desc}|{args_json}]]"

                            full_response += tool_marker

                            yield {
                                "type": "content",
                                "text": tool_marker,
                                "tool_friendly_description": friendly_desc,
                            }

                        elif block_type == "ToolResultBlock":
                            tool_id = getattr(block, "tool_use_id", "unknown")
                            content = getattr(block, "content", "")
                            logger.info(
                                f"[CLAUDE MCP] ToolResultBlock for ID: {tool_id}, content: {str(content)[:200]}..."
                            )

                            if html_context_request_id and tool_id:
                                logger.info("[CLAUDE MCP] HTML context fetch completed")
                                yield {
                                    "type": "html_context_refresh",
                                    "stage": "complete",
                                    "message": "Latest dashboard HTML loaded",
                                    "context_id": html_context_request_id,
                                    "edit_session_id": html_edit_session_id,
                                }
                                html_context_request_id = None

                            if last_html_tool_id and tool_id == last_html_tool_id:
                                logger.info("[CLAUDE MCP] HTML tool result received")
                                last_html_tool_id = None

                            if tool_id in pending_datasource_events:
                                ds_info = pending_datasource_events.pop(tool_id)
                                tool_input = ds_info.get("tool_input", {})
                                connection_id = tool_input.get("connection_id")
                                dataset_id = tool_input.get("dataset_id")
                                datasource_id = connection_id or dataset_id

                                if datasource_id:
                                    datasource_name = ""
                                    datasource_type = ""
                                    try:
                                        from server.db.session import get_async_session
                                        from server.repositories.connections import (
                                            ConnectionRepository,
                                        )
                                        from server.repositories.datasets import (
                                            DatasetRepository,
                                        )

                                        async for ds_session in get_async_session():
                                            if connection_id:
                                                conn_repo = ConnectionRepository(ds_session)
                                                connection = await conn_repo.get(connection_id)
                                                if connection:
                                                    datasource_name = connection.name or "Unnamed"
                                                    datasource_type = connection.type or "unknown"
                                            elif dataset_id:
                                                ds_repo = DatasetRepository(ds_session)
                                                dataset = await ds_repo.get(dataset_id)
                                                if dataset:
                                                    datasource_name = dataset.name or "Unnamed"
                                                    datasource_type = dataset.type or "file"
                                            break
                                    except Exception as ds_err:
                                        logger.warning(f"[CLAUDE MCP] Could not fetch datasource info: {ds_err}")

                                    logger.info(
                                        f"[CLAUDE MCP] Emitting datasource_selected: {datasource_id} ({datasource_name})"
                                    )
                                    yield {
                                        "type": "datasource_selected",
                                        "datasource_id": str(datasource_id),
                                        "datasource_name": datasource_name,
                                        "datasource_type": datasource_type,
                                    }

                            if tool_id in pending_query_saved_events:
                                pending_query_saved_events.pop(tool_id)
                                logger.info("[CLAUDE MCP] Query saved, emitting query_saved event")
                                yield {
                                    "type": "query_saved",
                                    "message": "Query saved successfully",
                                }

                            if tool_id in pending_memory_updated_events:
                                pending_memory_updated_events.discard(tool_id)
                                logger.info("[CLAUDE MCP] Memory updated, emitting memory_updated event")
                                yield {"type": "memory_updated"}

                            if tool_id in pending_screenshot_events:
                                pending_screenshot_events.pop(tool_id)
                                try:
                                    if isinstance(content, str):
                                        result_data = json.loads(content)
                                    elif isinstance(content, list) and len(content) > 0:
                                        first_item = content[0]
                                        text_content = getattr(first_item, "text", None)
                                        if text_content:
                                            result_data = json.loads(text_content)
                                        else:
                                            result_data = {}
                                    else:
                                        result_data = {}

                                    if result_data.get("success") and result_data.get("image_url"):
                                        logger.info(
                                            "[CLAUDE MCP] Screenshot generated, emitting dashboard_screenshot event"
                                        )
                                        yield {
                                            "type": "dashboard_screenshot",
                                            "message": "Dashboard screenshot generated",
                                            "image_url": result_data["image_url"],
                                            "size_bytes": result_data.get("size_bytes", 0),
                                        }
                                except Exception as e:
                                    logger.warning(f"[CLAUDE MCP] Failed to emit dashboard_screenshot event: {e}")

                            if tool_id in pending_plan_tool_ids:
                                pending_plan_tool_ids.discard(tool_id)
                            else:
                                tool_completion_msg = "\n\nTool executed successfully\n\n"
                                full_response += tool_completion_msg
                                yield {"type": "content", "text": tool_completion_msg}

            elif message_type == "UserMessage":
                # UserMessage contains tool results in MCP flow
                if hasattr(message, "content") and isinstance(message.content, list):
                    for block in message.content:
                        block_type = type(block).__name__

                        if block_type == "ToolResultBlock":
                            tool_use_id = getattr(block, "tool_use_id", "unknown")
                            logger.info(f"[CLAUDE MCP] UserMessage ToolResultBlock for ID: {tool_use_id}")

                            if tool_use_id in pending_datasource_events:
                                ds_info = pending_datasource_events.pop(tool_use_id)
                                tool_input = ds_info.get("tool_input", {})
                                connection_id = tool_input.get("connection_id")
                                dataset_id = tool_input.get("dataset_id")
                                datasource_id = connection_id or dataset_id

                                if datasource_id:
                                    datasource_name = ""
                                    datasource_type = ""
                                    try:
                                        from server.db.session import get_async_session
                                        from server.repositories.connections import (
                                            ConnectionRepository,
                                        )
                                        from server.repositories.datasets import (
                                            DatasetRepository,
                                        )

                                        async for ds_session in get_async_session():
                                            if connection_id:
                                                conn_repo = ConnectionRepository(ds_session)
                                                connection = await conn_repo.get(connection_id)
                                                if connection:
                                                    datasource_name = connection.name or "Unnamed"
                                                    datasource_type = connection.type or "unknown"
                                            elif dataset_id:
                                                ds_repo = DatasetRepository(ds_session)
                                                dataset = await ds_repo.get(dataset_id)
                                                if dataset:
                                                    datasource_name = dataset.name or "Unnamed"
                                                    datasource_type = dataset.type or "file"
                                            break
                                    except Exception as ds_err:
                                        logger.warning(f"[CLAUDE MCP] Could not fetch datasource info: {ds_err}")

                                    logger.info(
                                        f"[CLAUDE MCP] Emitting datasource_selected from UserMessage: {datasource_id}"
                                    )
                                    yield {
                                        "type": "datasource_selected",
                                        "datasource_id": str(datasource_id),
                                        "datasource_name": datasource_name,
                                        "datasource_type": datasource_type,
                                    }

                            if tool_use_id in pending_query_saved_events:
                                pending_query_saved_events.pop(tool_use_id)
                                logger.info("[CLAUDE MCP] Query saved (UserMessage), emitting query_saved event")
                                yield {
                                    "type": "query_saved",
                                    "message": "Query saved successfully",
                                }

                            if tool_use_id in pending_memory_updated_events:
                                pending_memory_updated_events.discard(tool_use_id)
                                logger.info("[CLAUDE MCP] Memory updated (UserMessage), emitting memory_updated event")
                                yield {"type": "memory_updated"}

                            if tool_use_id in pending_plan_tool_ids:
                                pending_plan_tool_ids.discard(tool_use_id)
                            else:
                                tool_completion_msg = "\n\nTool executed successfully\n\n"
                                full_response += tool_completion_msg
                                yield {"type": "content", "text": tool_completion_msg}

            elif message_type == "ResultMessage":
                # Log result metadata
                if hasattr(message, "data"):
                    cost = message.data.get("cost", 0)
                    is_error = message.data.get("is_error", False)
                    logger.info(f"[CLAUDE MCP] Cost: ${cost:.4f}, Error: {is_error}")

                break

            else:
                logger.debug(f"[CLAUDE MCP] Skipping unhandled message type: {message_type}")

        logger.info(f"[CLAUDE MCP] Stream complete: {message_count} messages, {len(full_response)} chars")

        # Return the full response text to save it
        yield {"type": "response_complete", "full_response": full_response}

        # Emit html_edit_complete if any HTML edits occurred during this stream
        if html_edit_occurred and html_edit_session_id:
            logger.info("[CLAUDE MCP] Emitting final html_edit_complete for session")
            yield {
                "type": "html_edit_complete",
                "message": "HTML edit applied successfully",
                "edit_session_id": html_edit_session_id,
            }

        # Emit done event
        yield {"type": "done"}

    except GeneratorExit:
        logger.info("[CLAUDE MCP] Stream cancelled, cleaning up")
        raise
    except Exception as e:
        logger.error(f"[CLAUDE MCP] Error: {e}", exc_info=True)
        yield {"type": "error", "error": str(e)}

    finally:
        cleanup_task = asyncio.current_task()
        cancel_requested = bool(
            cleanup_task
            and (cleanup_task.cancelled() or (hasattr(cleanup_task, "cancelling") and cleanup_task.cancelling()))
        )

        if client:
            try:
                await client.disconnect()
                logger.info("[CLAUDE MCP] Disconnected from Claude")
            except Exception as disconnect_error:
                logger.warning(f"[CLAUDE MCP] Disconnect warning: {disconnect_error}")

            try:
                transport = getattr(client, "_transport", None)
                process = getattr(transport, "_process", None) if transport else None
                if not process and cli_process:
                    process = cli_process
                if process and process.returncode is None:
                    if cancel_requested:
                        logger.debug("[CLAUDE MCP] Terminating process due to cancellation")
                        process.kill()
                        logger.info("[CLAUDE MCP] Skipping process wait due to cancellation")
                    else:
                        logger.warning("[CLAUDE MCP] Process still running after disconnect, sending SIGTERM")
                        process.terminate()
                        try:
                            await asyncio.wait_for(process.wait(), timeout=3.0)
                            logger.info("[CLAUDE MCP] Process terminated gracefully")
                        except TimeoutError:
                            logger.warning("[CLAUDE MCP] SIGTERM timed out, sending SIGKILL")
                            process.kill()
                            await asyncio.wait_for(process.wait(), timeout=5.0)
                            logger.info("[CLAUDE MCP] Process force-killed successfully")
                elif not process and cli_process_pid:
                    try:
                        os.kill(cli_process_pid, signal.SIGTERM)
                        logger.info(
                            "[CLAUDE MCP] Process terminated (pid=%s)",
                            cli_process_pid,
                        )
                    except ProcessLookupError:
                        pass
            except TimeoutError:
                logger.error("[CLAUDE MCP] Timeout waiting for process to terminate")
            except Exception as kill_error:
                logger.error(f"[CLAUDE MCP] Process cleanup failed: {kill_error}")
