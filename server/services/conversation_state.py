import copy
from typing import Any, Protocol, runtime_checkable

from server.utils.conversation_items import conversation_items_to_messages
from server.utils.custom_logger import get_logger
from server.utils.litellm_utils import supports_custom_temperature

logger = get_logger(__name__)


@runtime_checkable
class SessionProtocol(Protocol):
    """Protocol defining the session interface for backend implementations."""

    async def get_items(self, limit: int | None = None) -> list[Any]: ...
    async def add_items(self, items: list[Any]) -> None: ...
    async def clear_session(self) -> None: ...


class ConversationStateSession:
    """
    Session wrapper that filters blocked tool calls before persisting history.

    Supports both SQLite (local mode) and PostgreSQL (hosted mode) backends
    via composition with the appropriate session implementation.
    """

    blocked_tools = {
        "get_existing_html",
        "dashboard_search_replace",
        "apply_html_patch",
    }
    redacted_arguments = "{}"
    redacted_content = "[omitted] for context. use new tool calls to get existing html or apply search/replace/patch"

    def __init__(self, session_id: str, backend: SessionProtocol):
        """
        Initialize with a backend session.

        Args:
            session_id: The session identifier (typically notebook UUID)
            backend: The underlying session implementation (SQLiteSession or SQLAlchemySession)
        """
        self._session_id = session_id
        self._backend = backend
        self.filtered_tool_call_ids: set[str] = set()

    @property
    def session_id(self) -> str:
        return self._session_id

    async def get_items(self, limit: int | None = None) -> list[Any]:
        """Retrieve conversation history from backend."""
        return await self._backend.get_items(limit)

    async def clear_session(self) -> None:
        """Clear all items from backend session."""
        await self._backend.clear_session()

    @staticmethod
    def _clone_item(item: Any) -> Any:
        try:
            return copy.deepcopy(item)
        except Exception:
            return copy.copy(item)

    @staticmethod
    def _get_tool_calls(item: Any) -> list[Any]:
        if hasattr(item, "tool_calls"):
            return item.tool_calls or []
        if isinstance(item, dict):
            return item.get("tool_calls") or []
        return []

    @staticmethod
    def _set_tool_calls(item: Any, tool_calls: list[Any]) -> None:
        if hasattr(item, "tool_calls"):
            item.tool_calls = tool_calls
        elif isinstance(item, dict):
            item["tool_calls"] = tool_calls

    @staticmethod
    def _set_content(item: Any, content: Any) -> None:
        if hasattr(item, "content"):
            item.content = content
        elif isinstance(item, dict):
            item["content"] = content

    @staticmethod
    def _get_content(item: Any) -> Any:
        if hasattr(item, "content"):
            return item.content
        if isinstance(item, dict):
            return item.get("content")
        return None

    @staticmethod
    def _get_tool_call_name(tool_call: Any) -> str | None:
        if hasattr(tool_call, "function") and hasattr(tool_call.function, "name"):
            return tool_call.function.name
        if isinstance(tool_call, dict):
            function_data = tool_call.get("function") or {}
            return function_data.get("name")
        return None

    @staticmethod
    def _get_tool_call_id(tool_call: Any) -> str | None:
        if hasattr(tool_call, "id"):
            return tool_call.id
        if isinstance(tool_call, dict):
            return tool_call.get("id")
        return None

    @staticmethod
    def _get_message_tool_call_id(item: Any) -> str | None:
        if hasattr(item, "tool_call_id"):
            return item.tool_call_id
        if isinstance(item, dict):
            return item.get("tool_call_id")
        return None

    def _redact_tool_call_arguments(self, tool_call: Any) -> None:
        if hasattr(tool_call, "function"):
            func = tool_call.function
            if hasattr(func, "arguments"):
                func.arguments = self.redacted_arguments
            return

        if isinstance(tool_call, dict):
            function_data = tool_call.get("function") or {}
            if "arguments" in function_data:
                function_data["arguments"] = (
                    self.redacted_arguments if isinstance(function_data.get("arguments"), str) else {}
                )
            tool_call["function"] = function_data

    def _filter_tool_calls(self, tool_calls: list[Any]) -> tuple[list[Any], bool]:
        redacted_calls: list[Any] = []
        changed = False

        for tool_call in tool_calls:
            name = self._get_tool_call_name(tool_call)
            call_id = self._get_tool_call_id(tool_call)

            if name in self.blocked_tools:
                changed = True
                if call_id:
                    self.filtered_tool_call_ids.add(call_id)
                redacted_call = self._clone_item(tool_call)
                self._redact_tool_call_arguments(redacted_call)
                redacted_calls.append(redacted_call)
                continue

            redacted_calls.append(tool_call)

        return redacted_calls, changed

    async def add_items(self, items: list[Any]) -> None:
        if not items:
            return

        filtered_items: list[Any] = []

        for item in items:
            tool_calls = self._get_tool_calls(item)

            if tool_calls:
                redacted_calls, changed = self._filter_tool_calls(tool_calls)

                if changed:
                    updated_item = self._clone_item(item)
                    self._set_tool_calls(updated_item, redacted_calls)
                    filtered_items.append(updated_item)
                else:
                    filtered_items.append(item)
                continue

            tool_call_id = self._get_message_tool_call_id(item)
            if tool_call_id and tool_call_id in self.filtered_tool_call_ids:
                updated_item = self._clone_item(item)
                self._set_content(updated_item, self.redacted_content)
                filtered_items.append(updated_item)
                continue

            filtered_items.append(item)

        if filtered_items:
            await self._backend.add_items(filtered_items)

    async def compact_conversation(
        self,
        model: str = "gpt-4",
        token_threshold: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """
        Compact conversation history to reduce token usage.

        This method removes redundant tool calls while preserving:
        - All user messages
        - All assistant responses
        - Tool call integrity (no broken references)
        - Latest saved_query_schema results
        - All save_query calls

        Args:
            model: Model name for token counting (default: gpt-4)
            token_threshold: Only compact if current tokens exceed this threshold.
                           If None, always perform compaction.
            dry_run: If True, return stats without modifying conversation

        Returns:
            Dictionary with compaction statistics including:
            - tokens_before: Token count before compaction
            - tokens_after: Token count after compaction
            - tokens_saved: Number of tokens saved
            - reduction_percentage: Percentage reduction
            - items_removed: Number of items removed
            - items_redacted: Number of items with redacted content
            - compaction_performed: Whether compaction was actually performed
        """
        items = await self.get_items()

        if not items:
            return {
                "message": "No items to compact",
                "compaction_performed": False,
                "tokens_before": 0,
                "tokens_after": 0,
            }

        # Check if compaction is needed based on threshold
        if token_threshold is not None:
            from server.utils.token_utils import count_conversation_tokens

            # Convert items to messages for token counting
            messages = self._items_to_messages_for_counting(items)
            token_stats = count_conversation_tokens(messages, model)
            current_tokens = token_stats["total_tokens"]

            if current_tokens < token_threshold:
                logger.info(f"Compaction not needed: {current_tokens} tokens < {token_threshold} threshold")
                return {
                    "message": "Compaction not needed - below threshold",
                    "compaction_performed": False,
                    "current_tokens": current_tokens,
                    "threshold": token_threshold,
                    "tokens_before": current_tokens,
                    "tokens_after": current_tokens,
                }

        # Perform compaction
        from server.services.conversation_compactor import ConversationCompactor

        compactor = ConversationCompactor()
        compacted_items, stats = compactor.compact(items, model, dry_run)

        logger.info(
            f"Conversation compaction {'would save' if dry_run else 'saved'} "
            f"{stats.get('tokens_saved', 0)} tokens "
            f"({stats.get('reduction_percentage', 0)}% reduction)"
        )

        if not dry_run and compacted_items != items:
            # Replace conversation with compacted version
            await self.clear_session()
            await self.add_items(compacted_items)
            logger.info(
                f"Compacted conversation: removed {stats.get('items_removed', 0)} items, "
                f"redacted {stats.get('items_redacted', 0)} items"
            )

        return {
            "compaction_performed": not dry_run and compacted_items != items,
            "dry_run": dry_run,
            **stats,
        }

    def _items_to_messages_for_counting(self, items: list[Any]) -> list[dict[str, Any]]:
        return conversation_items_to_messages(items)

    async def prune_tool_outputs(self, dry_run: bool = False) -> dict[str, Any]:
        """
        Tier 1: Prune old tool outputs (runs every turn).

        Args:
            dry_run: If True, only calculate stats without modifying

        Returns:
            Dictionary with pruning statistics
        """
        from server.services.conversation_pruner import ConversationPruner

        items = await self.get_items()

        if not items:
            return {
                "message": "No items to prune",
                "pruning_performed": False,
                "tokens_before": 0,
                "tokens_after": 0,
            }

        pruner = ConversationPruner()
        pruned_items, stats = pruner.prune(items, dry_run)

        if not dry_run and stats.get("pruning_performed"):
            # Replace session with pruned version
            await self.clear_session()
            await self.add_items(pruned_items)
            logger.info(
                f"Tier 1 pruning saved {stats.get('tokens_saved', 0)} tokens "
                f"(pruned {stats.get('outputs_pruned', 0)} tool outputs)"
            )

        return stats

    async def session_handoff(
        self,
        model: str = "gpt-4",
        llm_connection_id: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """
        Tier 2: Session handoff via summarization (triggers at 90% context).

        Args:
            model: Model name for handoff summary generation
            llm_connection_id: LLM connection to use for summarization
            dry_run: If True, only generate summary without clearing history

        Returns:
            Dictionary with handoff statistics and summary
        """
        from server.prompts.handoff_prompt import format_handoff_request
        from server.utils.token_utils import count_conversation_tokens

        items = await self.get_items()

        if not items:
            return {
                "message": "No items for handoff",
                "handoff_performed": False,
            }

        # Count tokens before
        messages = self._items_to_messages_for_counting(items)
        token_stats = count_conversation_tokens(messages, model)
        tokens_before = token_stats["total_tokens"]

        # Generate handoff summary using a fast, cheap model
        try:
            # Use a cheaper model for summarization (e.g., gpt-4o-mini or haiku)
            summary_model = "gpt-4o-mini" if "gpt" in model.lower() else "claude-3-5-haiku-20241022"

            handoff_prompt = format_handoff_request(items)

            # Call LLM for summary
            handoff_summary = await self._generate_handoff_summary(handoff_prompt, summary_model, llm_connection_id)

            if not dry_run:
                last_user_msg = None
                for item in reversed(items):
                    if isinstance(item, dict) and item.get("role") == "user":
                        last_user_msg = item
                        break
                    if hasattr(item, "role") and getattr(item, "role", None) == "user":
                        last_user_msg = item
                        break

                # Clear session
                await self.clear_session()

                # Rebuild with: [Handoff Note] + [Last User Message]
                new_items = []

                # Add handoff note as system message
                handoff_msg = {
                    "role": "system",
                    "content": f"PREVIOUS SESSION CONTEXT:\n\n{handoff_summary}",
                }
                new_items.append(handoff_msg)

                # Add last user message
                if last_user_msg:
                    new_items.append(last_user_msg)

                await self.add_items(new_items)

                # Count tokens after
                messages_after = self._items_to_messages_for_counting(new_items)
                token_stats_after = count_conversation_tokens(messages_after, model)
                tokens_after = token_stats_after["total_tokens"]

                logger.info(
                    f"Tier 2 handoff completed: {tokens_before} → {tokens_after} tokens "
                    f"({tokens_before - tokens_after} tokens saved, "
                    f"{round((tokens_before - tokens_after) / tokens_before * 100, 1)}% reduction)"
                )

                return {
                    "handoff_performed": True,
                    "tokens_before": tokens_before,
                    "tokens_after": tokens_after,
                    "tokens_saved": tokens_before - tokens_after,
                    "reduction_percentage": round((tokens_before - tokens_after) / tokens_before * 100, 2),
                    "handoff_summary": handoff_summary,
                    "items_before": len(items),
                    "items_after": len(new_items),
                }
            else:
                return {
                    "handoff_performed": False,
                    "dry_run": True,
                    "tokens_before": tokens_before,
                    "estimated_tokens_after": 500,  # Handoff note ~500 tokens
                    "handoff_summary": handoff_summary,
                }

        except Exception as e:
            logger.error(f"Session handoff failed: {e}", exc_info=True)
            return {
                "handoff_performed": False,
                "error": str(e),
                "tokens_before": tokens_before,
            }

    async def _generate_handoff_summary(self, prompt: str, model: str, llm_connection_id: str | None) -> str:
        """
        Generate handoff summary using LLM.

        This uses litellm directly to generate a summary of the conversation
        for the handoff process.
        """
        if not llm_connection_id:
            return (
                "[Handoff summary: LLM connection ID not provided. Session was reset but context was not summarized.]"
            )

        try:
            from litellm import acompletion

            from server.services.llm_service import ModelService

            # Get model instance
            model_instance = await ModelService.get_litellm_model_instance(llm_connection_id, model)

            if not model_instance:
                return "[Handoff summary: Failed to create model instance. Session was reset but context was not summarized.]"

            completion_kwargs: dict = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1000,
            }
            if supports_custom_temperature(model):
                completion_kwargs["temperature"] = 0.3

            response = await acompletion(**completion_kwargs)

            # Extract summary from response (handle different response formats)
            try:
                summary = response.choices[0].message.content  # type: ignore
            except (AttributeError, IndexError, TypeError):
                try:
                    summary = response["choices"][0]["message"]["content"]  # type: ignore
                except (KeyError, IndexError, TypeError):
                    summary = ""

            return summary or "[Handoff summary: No summary generated]"

        except Exception as e:
            logger.error(f"Failed to generate handoff summary: {e}", exc_info=True)
            return f"[Handoff summary error: {str(e)}. Session was reset but context was not summarized.]"
