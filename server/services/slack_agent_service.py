"""Slack agent service for processing Slack messages through Byaan."""

from __future__ import annotations

import json
import re
from datetime import datetime
from uuid import UUID

import litellm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

litellm.drop_params = True

from server.constants.models import MODELS_BY_PROVIDER, SLACK_CLASSIFIER_MODEL_BY_PROVIDER
from server.models.slack_conversation import SlackConversation
from server.models.slack_event_log import SlackEventLog
from server.models.slack_workspace import SlackWorkspace
from server.repositories.custom_skill import CustomSkillRepository
from server.repositories.llm_connections import LLMConnectionRepository
from server.schemas.agent import AgentRequest
from server.services.completion_service import CompletionError, CompletionService
from server.services.crypto_service import CryptoService
from server.services.export_service import CompiledHtmlExportService
from server.services.screenshot_service import ScreenshotService, ScreenshotServiceError
from server.services.slack_intent_classifier import classify_intent
from server.services.slack_service import SlackService, strip_bot_mentions
from server.services.slack_thread_filter import (
    check_mute_keyword,
    check_resume_keyword,
    get_conversation,
    layer1_should_skip,
)
from server.services.unified_agent import stream_handoff_agent_response
from server.utils.custom_logger import get_logger
from server.utils.slack_block_elements import SlackBlockBuilder
from server.utils.slack_chart_detector import SlackChartDetector
from server.utils.slack_formatter import markdown_to_slack_blocks
from server.utils.slack_table_parser import SlackTableParser
from server.utils.slack_thread_lock import acquire_thread_lock

logger = get_logger(__name__)


class SlackAgentService:
    """Service for processing Slack messages through the AI agent."""

    CONFIRMATION_MESSAGE = "Hey, I'm working on your request. I'll let you know once it's ready."
    SLACK_MENTION_HINT = "\n\n_Reply here or @Byaan to continue the conversation_"

    @staticmethod
    async def _resolve_classifier_model(
        llm_connection_id: UUID,
        session: AsyncSession,
        fallback_model: str | None,
    ) -> str | None:
        """Return a cheap classifier model for the connection's provider.

        Falls back to the workspace default when the provider has no override
        (Azure, Bedrock, Groq, xAI).
        """
        connection = await LLMConnectionRepository(session).get(llm_connection_id)
        if not connection:
            return fallback_model
        return SLACK_CLASSIFIER_MODEL_BY_PROVIDER.get(connection.type, fallback_model)

    @staticmethod
    async def _resolve_model_for_connection(
        llm_connection_id: UUID,
        session: AsyncSession,
    ) -> str | None:
        """Resolve the model string for a Slack LLM connection.

        Order of preference:
        1. Model stored in connection.config["model"] (user-selected at connection creation)
        2. First entry in MODELS_BY_PROVIDER for the connection type (latest model)
        """
        connection = await LLMConnectionRepository(session).get(llm_connection_id)
        if not connection:
            return None

        try:
            cfg = await CryptoService.decrypt_config(connection.config, session) if connection.config else {}
        except Exception:
            cfg = {}

        stored_model = cfg.get("model") if isinstance(cfg, dict) else None
        if stored_model:
            return stored_model

        provider_models = MODELS_BY_PROVIDER.get(connection.type, [])
        return provider_models[0] if provider_models else None

    @staticmethod
    async def process_mention(
        workspace: SlackWorkspace,
        channel_id: str,
        thread_ts: str | None,
        user_id: str,
        text: str,
        event_ts: str,
        event_id: str,
        session: AsyncSession,
    ) -> None:
        """Process a Slack @mention event."""
        event_log = SlackEventLog(
            slack_workspace_id=workspace.id,
            event_type="app_mention",
            event_id=event_id,
            slack_channel_id=channel_id,
            slack_user_id=user_id,
            processing_status="processing",
        )
        session.add(event_log)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            logger.info(f"Duplicate Slack event: {event_id}")
            return

        bot_token = await SlackAgentService._get_bot_token(workspace, session)
        slack_client = SlackService(bot_token)

        try:
            await slack_client.post_message(
                channel=channel_id,
                text=SlackAgentService.CONFIRMATION_MESSAGE,
                thread_ts=thread_ts or event_ts,
            )

            question = SlackAgentService._extract_question(text, workspace.bot_user_id)

            conversation = await SlackAgentService._get_or_create_conversation(
                workspace=workspace,
                channel_id=channel_id,
                thread_ts=thread_ts or event_ts,
                user_id=user_id,
                session=session,
                text=question,
            )

            llm_connection_id = workspace.default_llm_connection_id
            if not llm_connection_id:
                raise ValueError("No LLM connection configured for Slack workspace")

            resolved_model = await SlackAgentService._resolve_model_for_connection(llm_connection_id, session)
            logger.info(f"[SLACK] Using model for connection {llm_connection_id}: {resolved_model}")

            slack_prompt = await SlackAgentService._build_slack_prompt(
                question=question,
                tenant_id=workspace.tenant_id,
                session=session,
            )

            agent_request = AgentRequest(
                message=slack_prompt,
                notebook_id=conversation.notebook_id,
                llm_connection_id=llm_connection_id,
                create_notebook=conversation.notebook_id is None,
                model=resolved_model,
            )

            raw_response, new_notebook_id, dashboard_generated, query_executed = await SlackAgentService._run_agent(
                request=agent_request,
                session=session,
                tenant_id=workspace.tenant_id,
                user_id=None,
            )

            if new_notebook_id and conversation.notebook_id is None:
                conversation.notebook_id = new_notebook_id
                await session.commit()

            await SlackAgentService._post_agent_response(
                workspace=workspace,
                slack_client=slack_client,
                conversation=conversation,
                channel_id=channel_id,
                thread_ts=thread_ts or event_ts,
                raw_response=raw_response,
                dashboard_generated=dashboard_generated,
                query_executed=query_executed,
                llm_connection_id=llm_connection_id,
                resolved_model=resolved_model,
                append_mention_hint=True,
                session=session,
            )

            conversation.bot_owned = True
            await session.commit()

            event_log.processing_status = "completed"
            await session.commit()

            logger.info(
                f"Successfully processed Slack mention in channel {channel_id}",
                extra={"posthog_context": {"workspace_id": str(workspace.id)}},
            )

        except Exception as e:
            logger.error(f"Error processing Slack mention: {e}", exc_info=True)

            event_log.processing_status = "failed"
            event_log.error_message = str(e)
            await session.commit()

            try:
                await slack_client.post_message(
                    channel=channel_id,
                    text="I encountered an error processing your request. Please try again or contact your administrator.",
                    thread_ts=thread_ts or event_ts,
                )
            except Exception:
                pass

            raise

    @staticmethod
    async def _get_bot_token(workspace: SlackWorkspace, session: AsyncSession) -> str:
        """Decrypt and return the bot token."""
        decrypted = await CryptoService.decrypt_config(workspace.bot_token_encrypted, session)
        return decrypted.get("bot_token", decrypted) if isinstance(decrypted, dict) else decrypted

    @staticmethod
    async def _get_signing_secret(workspace: SlackWorkspace, session: AsyncSession) -> str:
        """Decrypt and return the signing secret."""
        decrypted = await CryptoService.decrypt_config(workspace.signing_secret_encrypted, session)
        return decrypted.get("signing_secret", decrypted) if isinstance(decrypted, dict) else decrypted

    @staticmethod
    def _extract_question(text: str, bot_user_id: str | None) -> str:
        """Remove bot mention from message text."""
        if bot_user_id:
            pattern = f"<@{bot_user_id}>"
            text = re.sub(pattern, "", text)
        text = re.sub(r"<@\w+>", "", text)
        return text.strip()

    @staticmethod
    def _derive_thread_title(text: str | None) -> str | None:
        """Whitespace-collapse the inbound text into a short thread title."""
        if not text:
            return None
        collapsed = " ".join(text.split())
        if not collapsed:
            return None
        return collapsed[:120]

    @staticmethod
    async def _get_or_create_conversation(
        workspace: SlackWorkspace,
        channel_id: str,
        thread_ts: str | None,
        user_id: str,
        session: AsyncSession,
        text: str | None = None,
    ) -> SlackConversation:
        """Get existing conversation or create a new one."""
        from sqlalchemy import select

        query = (
            select(SlackConversation)
            .where(SlackConversation.slack_workspace_id == workspace.id)
            .where(SlackConversation.slack_channel_id == channel_id)
        )
        if thread_ts:
            query = query.where(SlackConversation.slack_thread_ts == thread_ts)
        else:
            query = query.where(SlackConversation.slack_thread_ts.is_(None))

        result = await session.execute(query)
        conversation = result.scalar_one_or_none()

        title = SlackAgentService._derive_thread_title(text)

        if not conversation:
            conversation = SlackConversation(
                slack_workspace_id=workspace.id,
                slack_channel_id=channel_id,
                slack_thread_ts=thread_ts,
                slack_user_id=user_id,
                thread_title=title,
            )
            session.add(conversation)
            await session.commit()
            await session.refresh(conversation)
        elif title and not conversation.thread_title:
            conversation.thread_title = title

        conversation.last_activity_at = datetime.now()
        await session.commit()

        return conversation

    @staticmethod
    async def _run_agent(
        request: AgentRequest,
        session: AsyncSession,
        tenant_id: UUID,
        user_id: UUID | None = None,
    ) -> tuple[str, UUID | None, bool, bool]:
        """Run the unified agent and collect the full response.

        ``user_id`` is optional because Slack messages are workspace-scoped and may not map to
        an individual Byaan user. The unified agent will fall back to org-scoped resources
        (skills, GitHub repos/tokens) when ``user_id`` is None.

        Returns:
            Tuple of (response_text, notebook_id if created, dashboard_generated, query_executed)
        """
        response_parts = []
        notebook_id: UUID | None = None
        dashboard_generated = False
        query_executed = False

        async for event in stream_handoff_agent_response(request, session, tenant_id=tenant_id, user_id=user_id):
            if event.startswith("data: "):
                try:
                    data = json.loads(event[6:])
                    if data.get("type") == "content":
                        response_parts.append(data.get("text", ""))
                    elif data.get("type") == "notebook_created":
                        notebook_id = UUID(str(data.get("notebook_id")))
                    elif data.get("type") == "html_edit_complete":
                        dashboard_generated = True
                        logger.info("Dashboard generation detected (html_edit_complete event)")
                    elif data.get("type") == "datasource_selected":
                        query_executed = True
                        logger.info("Query execution detected (datasource_selected event)")
                    elif data.get("type") == "error":
                        return (
                            f"Error: {data.get('text', 'Unknown error occurred')}",
                            notebook_id,
                            dashboard_generated,
                            query_executed,
                        )
                except json.JSONDecodeError:
                    pass

        return (
            "".join(response_parts) or "I processed your request but have no response to share.",
            notebook_id,
            dashboard_generated,
            query_executed,
        )

    @staticmethod
    async def _summarize_for_slack(
        raw_response: str,
        llm_connection_id: UUID,
        tenant_id: UUID,
        session: AsyncSession,
        model: str | None = None,
        append_mention_hint: bool = True,
    ) -> tuple[list[dict], str]:
        """Clean agent response and convert to Slack Block Kit format."""
        try:
            outbound_skills = await CustomSkillRepository(session).get_by_type(tenant_id, "slack_outbound")

            if outbound_skills:
                combined_instructions = "\n\n".join(
                    f"### {skill.name}\n{skill.instructions}" for skill in outbound_skills if skill.is_active
                )
                prompt = f"""Clean and transform the raw AI agent response for a messaging platform.

TRANSFORMATION RULES:
{combined_instructions}

RAW RESPONSE:
{raw_response}

Output only the transformed message in clean Markdown, nothing else."""
            else:
                prompt = f"""Clean the raw AI agent response for a messaging platform.

RULES:
1. Remove all [[TOOL_CALL:...]] markers and "Tool executed successfully" messages
2. Remove SQL queries and technical details unless explicitly requested
3. Present only the final answer/insight
4. Be concise and conversational
5. Keep standard Markdown formatting (headers, bold, links, lists)
6. For markdown tables with numeric data, move units from cell values to column headers (e.g., "Avg Delay (mins)" in header, "18.1" in cell, not "18.1 mins")

RAW RESPONSE:
{raw_response}

Output only the cleaned message in Markdown, nothing else."""

            try:
                result = await CompletionService.complete(
                    prompt=prompt,
                    llm_connection_id=llm_connection_id,
                    session=session,
                    system_prompt="You are a message cleaner. Output only the cleaned message in Markdown.",
                    model=model,
                )
            except CompletionError as e:
                logger.error(f"Slack message cleanup failed [{e.reason}]: {e.message}")
                result = None

            cleaned = result if result else raw_response

            # Fallback: regex cleanup in case LLM didn't remove tool calls
            cleaned = re.sub(
                r"\[\[TOOL_CALL:.*?\]\](?=\[\[TOOL_CALL|[^\[\]]|$)",
                "",
                cleaned,
                flags=re.DOTALL,
            )
            cleaned = re.sub(r"Tool executed successfully", "", cleaned)
            cleaned = cleaned.strip()

            if append_mention_hint:
                cleaned += SlackAgentService.SLACK_MENTION_HINT

            logger.info(
                f"Processing Slack response for tables/charts. Has table: {SlackTableParser.has_markdown_table(cleaned)}"
            )
            logger.info(f"Response preview (first 500 chars): {cleaned[:500]}")

            final_blocks = []
            fallback = cleaned

            if SlackTableParser.has_markdown_table(cleaned):
                table_blocks, remaining_text = SlackTableParser.extract_and_convert_tables(cleaned)
                text_blocks, fallback = markdown_to_slack_blocks(remaining_text)

                final_blocks.extend(text_blocks)
                final_blocks.extend(table_blocks)

                return final_blocks, fallback
            else:
                text_blocks, fallback = markdown_to_slack_blocks(cleaned)
                final_blocks.extend(text_blocks)
                return final_blocks, fallback

        except Exception as e:
            logger.error(f"Error summarizing for Slack: {e}", exc_info=True)
            text = raw_response + (SlackAgentService.SLACK_MENTION_HINT if append_mention_hint else "")
            return markdown_to_slack_blocks(text)

    @staticmethod
    async def _build_slack_prompt(
        question: str,
        tenant_id: UUID,
        session: AsyncSession,
    ) -> str:
        """Build prompt with Slack context instructions and inbound skills."""
        inbound_skills = await CustomSkillRepository(session).get_by_type(tenant_id, "slack_inbound")

        skill_instructions = ""
        if inbound_skills:
            combined = "\n\n".join(
                f"### {skill.name}\n{skill.instructions}" for skill in inbound_skills if skill.is_active
            )
            skill_instructions = f"""
SLACK INBOUND SKILLS (follow these instructions when processing this request):
{combined}
"""

        return f"""This message came from Slack.
{skill_instructions}
When processing this request:
- Format your response appropriately for Slack (use Slack markdown, keep it concise)
- IMPORTANT: Always format query results as markdown tables using pipe (|) delimiters, even for small result sets
- When creating tables with numeric data, include units in column headers (e.g., "Revenue (USD)", "Duration (hours)") rather than in cell values (e.g., "1500" not "$1500")
- Example table format:
  | Column1 | Column2 | Column3 |
  |---------|---------|---------|
  | Value1  | Value2  | Value3  |

User's question:
{question}"""

    @staticmethod
    async def _upload_dashboard_screenshot(
        slack_client: SlackService,
        channel_id: str,
        thread_ts: str,
        notebook_id: UUID,
        session: AsyncSession,
    ) -> None:
        """
        Upload interactive HTML file and screenshot to Slack thread.

        Generates and uploads both:
        1. Compiled HTML file (downloadable, interactive dashboard)
        2. PNG screenshot (quick preview in Slack)

        Args:
            slack_client: Slack API client
            channel_id: Slack channel ID
            thread_ts: Thread timestamp to post files to
            notebook_id: Notebook/dashboard ID to export
            session: Database session
        """
        try:
            logger.info(f"Generating compiled HTML for dashboard {notebook_id}")

            compiled_html = await CompiledHtmlExportService.generate_compiled_html(
                session=session, notebook_id=str(notebook_id), version=None, disable_animations=False
            )

            logger.info(f"Compiled HTML generated ({len(compiled_html)} bytes), uploading to Slack")

            await slack_client.upload_file(
                channel=channel_id,
                file_bytes=compiled_html.encode("utf-8"),
                filename=f"dashboard_{str(notebook_id)[:8]}.html",
                thread_ts=thread_ts,
                initial_comment="📊 Interactive Dashboard (download and open in browser)",
            )

            logger.info(
                f"HTML file uploaded successfully to channel {channel_id}",
                extra={"posthog_context": {"notebook_id": str(notebook_id)}},
            )

        except Exception as e:
            logger.error(
                f"Failed to upload HTML dashboard: {e}",
                exc_info=True,
                extra={"posthog_context": {"notebook_id": str(notebook_id)}},
            )

        from server.utils.deployment import is_feature_enabled

        if not is_feature_enabled("worker_features_enabled"):
            logger.info(
                f"Skipping dashboard PNG preview for notebook {notebook_id}: worker features disabled. "
                "HTML dashboard file already posted."
            )
            return

        try:
            logger.info(f"Starting screenshot generation for notebook {notebook_id}")

            png_bytes = await ScreenshotService.capture(session=session, dashboard_id=notebook_id, version=None)

            logger.info(f"Screenshot generated ({len(png_bytes)} bytes), uploading to Slack")

            await slack_client.upload_file(
                channel=channel_id,
                file_bytes=png_bytes,
                filename=f"dashboard_{str(notebook_id)[:8]}.png",
                thread_ts=thread_ts,
                initial_comment="📸 Dashboard Preview",
            )

            logger.info(f"Screenshot uploaded successfully to channel {channel_id}")

        except ScreenshotServiceError as e:
            logger.warning(
                f"Failed to generate dashboard screenshot: {e}. HTML dashboard file already posted.",
                extra={"posthog_context": {"notebook_id": str(notebook_id)}},
            )
        except Exception as e:
            logger.error(
                f"Unexpected error uploading dashboard screenshot: {e}. HTML dashboard file already posted.",
                exc_info=True,
                extra={"posthog_context": {"notebook_id": str(notebook_id)}},
            )

    @staticmethod
    async def _post_agent_response(
        workspace: SlackWorkspace,
        slack_client: SlackService,
        conversation: SlackConversation,
        channel_id: str,
        thread_ts: str,
        raw_response: str,
        dashboard_generated: bool,
        query_executed: bool,
        llm_connection_id: UUID,
        resolved_model: str | None,
        append_mention_hint: bool,
        session: AsyncSession,
    ) -> None:
        """Shared post-agent rendering: summarize, post message, viz buttons, dashboard upload."""
        blocks, fallback_text = await SlackAgentService._summarize_for_slack(
            raw_response=raw_response,
            llm_connection_id=llm_connection_id,
            tenant_id=workspace.tenant_id,
            session=session,
            model=resolved_model,
            append_mention_hint=append_mention_hint,
        )

        response_msg = await slack_client.post_message(
            channel=channel_id,
            text=fallback_text,
            thread_ts=thread_ts,
            blocks=blocks,
        )

        has_table = SlackTableParser.has_markdown_table(raw_response)
        if has_table and query_executed and conversation.notebook_id:
            try:
                all_tables = SlackChartDetector._extract_all_tables(raw_response)
                valid_tables = [t for t in all_tables if len(t) >= 2 and len(t) <= 21]

                if valid_tables:
                    table_data = {
                        "tables": valid_tables,
                        "thread_ts": thread_ts,
                        "channel_id": channel_id,
                        "notebook_id": str(conversation.notebook_id) if conversation.notebook_id else None,
                        "tenant_id": str(workspace.tenant_id),
                        "created_by": str(workspace.installed_by) if workspace.installed_by else None,
                    }

                    value_str = json.dumps(table_data)

                    dashboard_button = SlackBlockBuilder.button(
                        text="📊 Full Dashboard",
                        action_id="generate_dashboard",
                        value=json.dumps(
                            {
                                "notebook_id": str(conversation.notebook_id),
                                "thread_ts": thread_ts,
                                "channel_id": channel_id,
                            }
                        ),
                    )

                    if len(value_str) > 2000:
                        logger.warning(
                            f"Table data too large for button value ({len(value_str)} chars), showing only dashboard button"
                        )
                        visualization_blocks = [
                            SlackBlockBuilder.card(title="📊 Visualization"),
                            SlackBlockBuilder.actions([dashboard_button]),
                        ]
                    else:
                        auto_button = SlackBlockBuilder.button(
                            text="🤖 Auto Generate",
                            action_id="auto_generate_chart",
                            value=value_str,
                        )
                        customize_button = SlackBlockBuilder.button(
                            text="⚙️ Customize",
                            action_id="customize_chart_show_options",
                            value=value_str,
                        )
                        action_buttons = [auto_button, customize_button, dashboard_button]

                        download_value = json.dumps(
                            {
                                "tables": valid_tables,
                                "thread_ts": thread_ts,
                                "channel_id": channel_id,
                            }
                        )
                        if len(download_value) <= 2000:
                            download_excel_button = SlackBlockBuilder.button(
                                text="📥 Download Excel",
                                action_id="download_excel",
                                value=download_value,
                            )
                            action_buttons.append(download_excel_button)
                        else:
                            logger.warning(
                                f"Table data too large for download_excel button value ({len(download_value)} chars), skipping button"
                            )

                        visualization_blocks = [
                            SlackBlockBuilder.card(title="📊 Data and Visualization"),
                            SlackBlockBuilder.actions(action_buttons),
                        ]

                    await slack_client.post_message(
                        channel=channel_id,
                        text="Visualization options available",
                        thread_ts=thread_ts,
                        blocks=visualization_blocks,
                    )
            except Exception as e:
                logger.error(f"Error posting visualization options: {e}", exc_info=True)

        if dashboard_generated and conversation.notebook_id:
            response_ts = response_msg.get("ts")
            await SlackAgentService._upload_dashboard_screenshot(
                slack_client=slack_client,
                channel_id=channel_id,
                thread_ts=response_ts,
                notebook_id=conversation.notebook_id,
                session=session,
            )

    @staticmethod
    async def _build_history_from_slack(
        slack_client: SlackService,
        channel_id: str,
        thread_ts: str,
        bot_user_id: str | None,
        limit: int = 12,
    ) -> list[dict]:
        replies = await slack_client.fetch_thread_replies(channel_id, thread_ts, limit=limit)
        history: list[dict] = []
        for msg in replies:
            text = strip_bot_mentions(msg.get("text", "") or "", bot_user_id)
            if not text:
                continue
            is_bot = bool(msg.get("bot_id")) or (bot_user_id and msg.get("user") == bot_user_id)
            history.append({"author": "byaan" if is_bot else "user", "text": text})
        return history

    @staticmethod
    async def process_thread_followup(
        workspace: SlackWorkspace,
        channel_id: str,
        thread_ts: str,
        user_id: str,
        text: str,
        event_ts: str,
        event_id: str,
        session: AsyncSession,
    ) -> None:
        """Process a non-mention message in a Byaan-owned thread.

        Runs cheap guards first, then classifier, then serialized agent invocation.
        Silent skip on any gate failure to avoid noisy channels.
        """
        event_log = SlackEventLog(
            slack_workspace_id=workspace.id,
            event_type="message_followup",
            event_id=event_id,
            slack_channel_id=channel_id,
            slack_user_id=user_id,
            processing_status="processing",
        )
        session.add(event_log)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            logger.info(f"Duplicate Slack event: {event_id}")
            return

        bot_token = await SlackAgentService._get_bot_token(workspace, session)
        slack_client = SlackService(bot_token)

        try:
            conversation = await get_conversation(workspace.id, channel_id, thread_ts, session)

            if check_resume_keyword(text):
                if conversation and conversation.auto_follow_muted:
                    conversation.auto_follow_muted = False
                    await session.commit()
                    await slack_client.post_message(
                        channel=channel_id,
                        thread_ts=thread_ts,
                        text="👋 Byaan is back. I'll follow up on this thread again.",
                    )
                event_log.processing_status = "completed"
                await session.commit()
                return

            if check_mute_keyword(text):
                if conversation:
                    conversation.auto_follow_muted = True
                    await session.commit()
                    await slack_client.post_message(
                        channel=channel_id,
                        thread_ts=thread_ts,
                        text="🤫 Muted for this thread. Mention @Byaan or say 'resume byaan' to re-enable.",
                    )
                event_log.processing_status = "completed"
                await session.commit()
                return

            skip, reason = layer1_should_skip(text, workspace.bot_user_id, conversation)
            if skip:
                logger.info(f"Slack followup layer1 skip: reason={reason} thread={thread_ts}")
                event_log.processing_status = "skipped_layer1"
                event_log.error_message = reason
                await session.commit()
                return

            history = await SlackAgentService._build_history_from_slack(
                slack_client=slack_client,
                channel_id=channel_id,
                thread_ts=thread_ts,
                bot_user_id=workspace.bot_user_id,
            )

            llm_connection_id = workspace.default_llm_connection_id
            if not llm_connection_id:
                logger.warning(f"Slack workspace {workspace.id} has no default LLM connection; skipping followup")
                event_log.processing_status = "skipped_no_llm"
                await session.commit()
                return

            resolved_model = await SlackAgentService._resolve_model_for_connection(llm_connection_id, session)
            classifier_model = await SlackAgentService._resolve_classifier_model(
                llm_connection_id=llm_connection_id,
                session=session,
                fallback_model=resolved_model,
            )
            logger.info(
                f"[SLACK] Classifier model for connection {llm_connection_id}: {classifier_model} "
                f"(agent model: {resolved_model})"
            )
            cleaned_text = strip_bot_mentions(text, workspace.bot_user_id)

            should_respond, decision_source = await classify_intent(
                text=cleaned_text,
                history=history,
                llm_connection_id=llm_connection_id,
                session=session,
                model=classifier_model,
            )

            logger.info(
                f"Slack followup classifier decision: respond={should_respond} "
                f"source={decision_source} model={classifier_model} thread={thread_ts}"
            )

            if not should_respond:
                event_log.processing_status = "skipped_classifier"
                event_log.error_message = decision_source
                await session.commit()
                return

            await slack_client.post_message(
                channel=channel_id,
                text=SlackAgentService.CONFIRMATION_MESSAGE,
                thread_ts=thread_ts,
            )

            async with acquire_thread_lock(workspace.slack_team_id, channel_id, thread_ts):
                conversation = await SlackAgentService._get_or_create_conversation(
                    workspace=workspace,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    user_id=user_id,
                    session=session,
                    text=cleaned_text,
                )

                slack_prompt = await SlackAgentService._build_followup_prompt(
                    question=cleaned_text,
                    tenant_id=workspace.tenant_id,
                    session=session,
                )

                agent_request = AgentRequest(
                    message=slack_prompt,
                    notebook_id=conversation.notebook_id,
                    llm_connection_id=llm_connection_id,
                    create_notebook=conversation.notebook_id is None,
                    model=resolved_model,
                )

                raw_response, new_notebook_id, dashboard_generated, query_executed = await SlackAgentService._run_agent(
                    request=agent_request,
                    session=session,
                    tenant_id=workspace.tenant_id,
                    user_id=None,
                )

                if new_notebook_id and conversation.notebook_id is None:
                    conversation.notebook_id = new_notebook_id
                    await session.commit()

                await SlackAgentService._post_agent_response(
                    workspace=workspace,
                    slack_client=slack_client,
                    conversation=conversation,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    raw_response=raw_response,
                    dashboard_generated=dashboard_generated,
                    query_executed=query_executed,
                    llm_connection_id=llm_connection_id,
                    resolved_model=resolved_model,
                    append_mention_hint=True,
                    session=session,
                )

            event_log.processing_status = "completed"
            await session.commit()

        except Exception as e:
            logger.error(f"Error processing Slack followup: {e}", exc_info=True)
            event_log.processing_status = "failed"
            event_log.error_message = str(e)
            await session.commit()

    @staticmethod
    async def _build_followup_prompt(
        question: str,
        tenant_id: UUID,
        session: AsyncSession,
    ) -> str:
        """Build agent prompt for a thread follow-up (no explicit mention).

        Mirrors the mention path: relies on the thread's notebook memory for
        prior context so we do not double-inject Slack history the agent
        already remembers.
        """
        inbound_skills = await CustomSkillRepository(session).get_by_type(tenant_id, "slack_inbound")

        skill_instructions = ""
        if inbound_skills:
            combined = "\n\n".join(
                f"### {skill.name}\n{skill.instructions}" for skill in inbound_skills if skill.is_active
            )
            skill_instructions = f"""
SLACK INBOUND SKILLS (follow these instructions when processing this request):
{combined}
"""

        return f"""This message came from Slack as a follow-up in an existing thread.
{skill_instructions}
When processing this request:
- The user did NOT re-mention Byaan; treat this as a continuation of the thread's conversation
- Use your notebook memory for prior context (queries, replies, data seen so far)
- Format your response for Slack (use Slack markdown, keep it concise)
- IMPORTANT: Always format query results as markdown tables using pipe (|) delimiters
- Include units in column headers rather than cell values

User's newest message:
{question}"""

    @staticmethod
    async def process_generate_dashboard_request(
        workspace: SlackWorkspace,
        notebook_id: str,
        channel_id: str,
        thread_ts: str,
        user_id: str,
        session: AsyncSession,
    ):
        """
        Process a generate dashboard button click from Slack.

        Args:
            workspace: Slack workspace configuration
            notebook_id: Notebook ID to generate dashboard for
            channel_id: Slack channel ID
            thread_ts: Thread timestamp
            user_id: Slack user ID who clicked the button
            session: Database session
        """
        try:
            bot_token = await SlackAgentService._get_bot_token(workspace, session)
            slack_client = SlackService(bot_token)

            await slack_client.post_message(
                channel=channel_id,
                text="Hey, I'm working on your request. I'll let you know once it's ready.",
                thread_ts=thread_ts,
            )

            notebook_uuid = UUID(notebook_id)

            request = AgentRequest(
                message="Create a dashboard with appropriate chart(s) based on the query results. Save the query(s) and start generating dashboard.",
                notebook_id=notebook_id,
                llm_connection_id=str(workspace.default_llm_connection_id)
                if workspace.default_llm_connection_id
                else None,
            )

            raw_response, _, dashboard_generated, _ = await SlackAgentService._run_agent(
                request=request,
                tenant_id=workspace.tenant_id,
                session=session,
                user_id=None,
            )

            if dashboard_generated:
                await SlackAgentService._upload_dashboard_screenshot(
                    slack_client=slack_client,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    notebook_id=notebook_uuid,
                    session=session,
                )
            else:
                await slack_client.post_message(
                    channel=channel_id,
                    text="I couldn't generate a dashboard from the available data. Please try asking for specific visualizations.",
                    thread_ts=thread_ts,
                )

            logger.info(
                f"Successfully processed generate dashboard request for notebook {notebook_id}",
                extra={"posthog_context": {"notebook_id": notebook_id}},
            )

        except Exception as e:
            logger.error(f"Error processing generate dashboard request: {e}", exc_info=True)

            try:
                if "slack_client" in locals():
                    await slack_client.post_message(
                        channel=channel_id,
                        text="I encountered an error generating the dashboard. Please try again or contact your administrator.",
                        thread_ts=thread_ts,
                    )
            except Exception as post_error:
                logger.error(f"Failed to post error message: {post_error}", exc_info=True)
