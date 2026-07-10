from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytz
from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.custom_skill import CustomSkill
from server.models.llm_connections import LLMConnection
from server.models.schedules import Schedule
from server.models.slack_workspace import SlackWorkspace
from server.repositories.custom_skill import CustomSkillRepository
from server.repositories.messages import MessageRepository
from server.repositories.queries import QueryRepository
from server.repositories.schedules import ScheduleRunRepository
from server.repositories.threads import ThreadRepository
from server.schemas.agent import AgentRequest
from server.services.completion_service import CompletionError, CompletionService
from server.services.crypto_service import CryptoService
from server.services.query_service import QueryService
from server.services.screenshot_service import ScreenshotService, ScreenshotServiceError
from server.services.slack_service import SlackService
from server.services.unified_agent import stream_handoff_agent_response
from server.utils.custom_logger import get_logger
from server.utils.slack_formatter import markdown_to_slack_blocks
from server.utils.slack_table_parser import SlackTableParser

logger = get_logger(__name__)

executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="schedule_")

_TOOL_CALL_RE = re.compile(r"\[\[TOOL_CALL:.*?\]\](?=\[\[TOOL_CALL|[^\[\]]|$)", re.DOTALL)


def _clean_tool_markers(text: str) -> str:
    """Remove tool call markers and execution messages from agent response."""
    text = _TOOL_CALL_RE.sub("", text)
    text = text.replace("\n\nTool executed successfully\n\n", "")
    text = text.replace("Tool executed successfully", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class ScheduleService:
    @staticmethod
    def calculate_next_run(cron_expression: str, tz: str) -> datetime:
        try:
            tz_obj = pytz.timezone(tz)
        except pytz.UnknownTimeZoneError:
            tz_obj = pytz.UTC

        now = datetime.now(tz_obj)
        cron = croniter(cron_expression, now)
        next_dt = cron.get_next(datetime)
        return next_dt.astimezone(pytz.UTC).replace(tzinfo=None)

    @staticmethod
    def validate_cron_expression(cron_expression: str) -> bool:
        try:
            croniter(cron_expression)
            return True
        except (ValueError, KeyError):
            return False

    @staticmethod
    async def execute_schedule(session: AsyncSession, schedule: Schedule) -> dict:
        start_time = datetime.now(UTC)

        if schedule.instruction:
            return await ScheduleService._execute_with_agent(session, schedule, start_time)

        query_repo = QueryRepository(session)
        queries = await query_repo.get_by_notebook_id(str(schedule.notebook_id))

        if not queries:
            return {"status": "failed", "error": "No queries in notebook"}

        query_ids = [str(q[0]) for q in queries]
        query_result = await QueryService.execute_batch_saved_queries(session, query_ids=query_ids, max_parallel=5)

        notebook_name = schedule.notebook.notebook_name if schedule.notebook else "Unknown"

        workspace_result = await session.execute(
            select(SlackWorkspace)
            .where(SlackWorkspace.tenant_id == schedule.tenant_id)
            .where(SlackWorkspace.is_active == True)  # noqa: E712
        )
        workspace = workspace_result.scalar_one_or_none()
        llm_connection_id = workspace.default_llm_connection_id if workspace else None

        if llm_connection_id:
            summary = await ScheduleService._summarize_query_results(
                session,
                query_result,
                notebook_name,
                llm_connection_id,
            )
        else:
            summary = ScheduleService._format_summary(schedule.name, notebook_name, query_result)

        message = await ScheduleService._write_to_notebook(session, schedule, summary, start_time)

        slack_error = None
        if schedule.slack_channel_id:
            _, slack_error = await ScheduleService._send_to_slack(session, schedule, summary)
            if slack_error:
                logger.warning(f"Slack delivery failed for schedule {schedule.id}: {slack_error}")

        webhook_error = None
        if schedule.webhook_url:
            _, webhook_error = await ScheduleService._send_webhook(
                schedule.webhook_url,
                schedule.name,
                summary,
            )

        duration_ms = int((datetime.now(UTC) - start_time).total_seconds() * 1000)
        has_errors = webhook_error or slack_error
        error_messages = [e for e in [webhook_error, slack_error] if e]
        run_repo = ScheduleRunRepository(session)
        await run_repo.create_run(
            {
                "schedule_id": schedule.id,
                "status": "success" if not has_errors else "partial",
                "started_at": start_time.replace(tzinfo=None),
                "completed_at": datetime.now(UTC).replace(tzinfo=None),
                "duration_ms": duration_ms,
                "queries_total": query_result.get("total_queries", 0),
                "queries_succeeded": query_result.get("successful_queries", 0),
                "queries_failed": query_result.get("failed_queries", 0),
                "message_id": message.id if message else None,
                "error_message": "; ".join(error_messages) if error_messages else None,
            }
        )

        return {"status": "success", "summary": summary, "message_id": message.id if message else None}

    @staticmethod
    async def _execute_with_agent(session: AsyncSession, schedule: Schedule, start_time: datetime) -> dict:
        result = await session.execute(
            select(SlackWorkspace)
            .where(SlackWorkspace.tenant_id == schedule.tenant_id)
            .where(SlackWorkspace.is_active == True)  # noqa: E712
        )
        workspace = result.scalar_one_or_none()
        llm_connection_id = workspace.default_llm_connection_id if workspace else None

        if not llm_connection_id and schedule.notebook:
            provider = schedule.notebook.last_used_provider
            model = schedule.notebook.last_used_model
            if provider:
                conn_result = await session.execute(
                    select(LLMConnection)
                    .where(LLMConnection.tenant_id == schedule.tenant_id)
                    .where(LLMConnection.type == provider)
                )
                connections = conn_result.scalars().all()

                for conn in connections:
                    if conn.config and model:
                        try:
                            cfg = await CryptoService.decrypt_config(conn.config, session)
                            conn_model = cfg.get("model") or cfg.get("models")
                            if isinstance(conn_model, list):
                                conn_model = conn_model[0] if conn_model else None
                            if conn_model == model:
                                llm_connection_id = conn.id
                                break
                        except Exception:
                            pass

                if not llm_connection_id and connections:
                    llm_connection_id = connections[0].id

        if not llm_connection_id:
            conn_result = await session.execute(
                select(LLMConnection).where(LLMConnection.tenant_id == schedule.tenant_id).limit(1)
            )
            connection = conn_result.scalar_one_or_none()
            if connection:
                llm_connection_id = connection.id

        if not llm_connection_id:
            return {"status": "failed", "error": "No LLM connection configured"}

        current_datetime = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

        scheduled_run_prompt = f"""## SCHEDULED RUN MODE

This is an automated scheduled report execution.

**Current execution time:** {current_datetime}

**Your task:**

1. **Always run fresh queries** - Execute new queries to get the latest data. Do not rely on cached or previous results. If any integrations are configured (e.g., PostHog, analytics tools via skills), run fresh queries there too.

2. **Date Handling (CRITICAL):**
   Interpret ALL relative time references based on current time ({current_datetime}):
   - "past week" / "last week" = 7 days ago to today
   - "this week" = start of current week to today
   - "last month" = previous calendar month
   - "past 30 days" = 30 days ago to today

3. **Compare with previous report** - Review the notebook conversation history for the last scheduled report and highlight what changed (new trends, increases/decreases in key metrics, anomalies).

4. **Output Formatting:**
   - Start with "This is your scheduled report"
   - Use proper markdown formatting for text: **bold** for emphasis, ## headings for sections, and clear structure
   - IMPORTANT: Always format query results as markdown tables using pipe (|) delimiters, even for small result sets
   - When creating tables with numeric data, include units in column headers (e.g., "Revenue (USD)", "Duration (hours)") rather than in cell values (e.g., "1500" not "$1500")
   - Example table format:
     | Column1 | Column2 | Column3 |
     |---------|---------|---------|
     | Value1  | Value2  | Value3  |
   - Do NOT include any [[TOOL_CALL:...]] markers or "Tool executed successfully" messages in your final output - only include the cleaned summary

---

**User's scheduled instruction:**
{schedule.instruction}
"""

        agent_request = AgentRequest(
            message=scheduled_run_prompt,
            notebook_id=schedule.notebook_id,
            llm_connection_id=llm_connection_id,
        )

        response_parts = []
        try:
            async for event in stream_handoff_agent_response(agent_request, session, tenant_id=schedule.tenant_id):
                if event.startswith("data: "):
                    try:
                        data = json.loads(event[6:])
                        if data.get("type") == "content":
                            response_parts.append(data.get("text", ""))
                        elif data.get("type") == "html_edit_complete":
                            logger.info(f"Dashboard generation detected for schedule {schedule.id}")
                        elif data.get("type") == "error":
                            return {"status": "failed", "error": data.get("text", "Unknown error")}
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            logger.error(f"Error running agent for schedule {schedule.id}: {e}", exc_info=True)
            return {"status": "failed", "error": str(e)}

        agent_response = "".join(response_parts)
        agent_response = _clean_tool_markers(agent_response)

        message = await ScheduleService._write_to_notebook(session, schedule, agent_response, start_time)

        slack_error = None
        if schedule.slack_channel_id and agent_response.strip():
            _, slack_error = await ScheduleService._send_to_slack(session, schedule, agent_response)
            if slack_error:
                logger.warning(f"Slack delivery failed for schedule {schedule.id}: {slack_error}")

        webhook_error = None
        if schedule.webhook_url:
            _, webhook_error = await ScheduleService._send_webhook(
                schedule.webhook_url,
                schedule.name,
                agent_response,
            )

        duration_ms = int((datetime.now(UTC) - start_time).total_seconds() * 1000)
        has_errors = webhook_error or slack_error
        error_messages = [e for e in [webhook_error, slack_error] if e]
        run_repo = ScheduleRunRepository(session)
        await run_repo.create_run(
            {
                "schedule_id": schedule.id,
                "status": "success" if not has_errors else "partial",
                "started_at": start_time.replace(tzinfo=None),
                "completed_at": datetime.now(UTC).replace(tzinfo=None),
                "duration_ms": duration_ms,
                "queries_total": 0,
                "queries_succeeded": 0,
                "queries_failed": 0,
                "message_id": message.id if message else None,
                "error_message": "; ".join(error_messages) if error_messages else None,
            }
        )

        return {"status": "success", "summary": agent_response, "message_id": message.id if message else None}

    @staticmethod
    async def test_schedule(session: AsyncSession, schedule: Schedule) -> dict:
        query_repo = QueryRepository(session)
        queries = await query_repo.get_by_notebook_id(str(schedule.notebook_id))

        if not queries:
            return {
                "success": False,
                "error": "No queries in notebook",
                "summary": None,
                "queries_total": 0,
                "queries_succeeded": 0,
                "queries_failed": 0,
            }

        query_ids = [str(q[0]) for q in queries]
        query_result = await QueryService.execute_batch_saved_queries(session, query_ids=query_ids, max_parallel=5)

        summary = ScheduleService._format_summary(
            schedule.name,
            schedule.notebook.notebook_name if schedule.notebook else "Unknown",
            query_result,
        )

        return {
            "success": query_result.get("success", False),
            "summary": summary,
            "queries_total": query_result.get("total_queries", 0),
            "queries_succeeded": query_result.get("successful_queries", 0),
            "queries_failed": query_result.get("failed_queries", 0),
        }

    @staticmethod
    async def preview_notebook_schedule(session: AsyncSession, notebook_id: str, notebook_name: str) -> dict:
        query_repo = QueryRepository(session)
        queries = await query_repo.get_by_notebook_id(notebook_id)

        if not queries:
            return {
                "success": False,
                "error": "No queries in notebook",
                "summary": None,
                "queries_total": 0,
                "queries_succeeded": 0,
                "queries_failed": 0,
            }

        query_ids = [str(q[0]) for q in queries]
        query_result = await QueryService.execute_batch_saved_queries(session, query_ids=query_ids, max_parallel=5)

        summary = ScheduleService._format_summary(
            "Schedule Preview",
            notebook_name,
            query_result,
        )

        return {
            "success": query_result.get("success", False),
            "summary": summary,
            "queries_total": query_result.get("total_queries", 0),
            "queries_succeeded": query_result.get("successful_queries", 0),
            "queries_failed": query_result.get("failed_queries", 0),
        }

    @staticmethod
    def _format_summary(schedule_name: str, notebook_name: str, result: dict) -> str:
        total = result.get("total_queries", 0)
        succeeded = result.get("successful_queries", 0)
        failed = result.get("failed_queries", 0)
        exec_time = result.get("total_execution_time_ms", 0)

        status_icon = "✅" if failed == 0 else "⚠️"

        summary_lines = [
            f"**{notebook_name}**",
            "",
            f"{status_icon} Queries executed: {succeeded}/{total}",
            f"⏱️ Execution time: {exec_time:.0f}ms",
        ]

        if failed > 0:
            summary_lines.append(f"❌ Failed queries: {failed}")

            data = result.get("data", [])
            for item in data:
                if hasattr(item, "success") and not item.success:
                    query_name = getattr(item, "query_name", "Unknown")
                    error = getattr(item, "error", "Unknown error")
                    summary_lines.append(f"  - {query_name}: {error}")

        return "\n".join(summary_lines)

    @staticmethod
    async def _summarize_query_results(
        session: AsyncSession,
        result: dict,
        notebook_name: str,
        llm_connection_id: UUID,
    ) -> str:
        """Use LLM to summarize query results into a readable report."""
        data = result.get("data", [])
        if not data:
            return f"**{notebook_name}**\n\nNo query results to summarize."

        query_summaries = []
        for item in data:
            if not hasattr(item, "success") or not item.success:
                continue

            query_name = getattr(item, "query_name", "Unknown")
            query_result = getattr(item, "result", None)

            if query_result:
                if isinstance(query_result, list):
                    row_count = len(query_result)
                    sample = query_result[:5]
                    query_summaries.append(
                        {
                            "name": query_name,
                            "row_count": row_count,
                            "sample_data": sample,
                        }
                    )
                else:
                    query_summaries.append(
                        {
                            "name": query_name,
                            "data": query_result,
                        }
                    )

        if not query_summaries:
            return f"**{notebook_name}**\n\nAll queries failed or returned no data."

        prompt = f"""Summarize the following query results from a scheduled report for "{notebook_name}".

Create a clear, concise summary that highlights:
- Key metrics and numbers
- Important trends or insights
- Any notable data points

Query Results:
{json.dumps(query_summaries, indent=2, default=str)}

Write a brief summary (2-4 paragraphs max) in markdown format. Start with the notebook name as a header.
Focus on what the data shows, not technical details about queries."""

        try:
            summary = await CompletionService.complete(
                prompt=prompt,
                llm_connection_id=llm_connection_id,
                session=session,
                system_prompt="You are a helpful assistant that summarizes data.",
            )
            return summary if summary else ScheduleService._format_summary("", notebook_name, result)
        except Exception as e:
            logger.error(f"Error summarizing query results: {e}", exc_info=True)
            return ScheduleService._format_summary("", notebook_name, result)

    @staticmethod
    async def _write_to_notebook(session: AsyncSession, schedule: Schedule, summary: str, timestamp: datetime):
        formatted_time = timestamp.strftime("%b %d, %Y at %I:%M %p UTC")

        content = f"""📊 **Scheduled Report: {schedule.name}**
*{formatted_time}*

{summary}

---
*This report was generated automatically.*"""

        thread_repo = ThreadRepository(session)
        notebook_id = schedule.notebook_id

        thread = await thread_repo.get(notebook_id)
        thread_id = notebook_id

        if not thread:
            existing_threads = await thread_repo.list(filters={"notebook_id": notebook_id})
            if existing_threads:
                thread_id = existing_threads[0].id
            else:
                await thread_repo.create(
                    {
                        "id": notebook_id,
                        "notebook_id": notebook_id,
                        "thread_title": None,
                    }
                )

        message_repo = MessageRepository(session)
        return await message_repo.create(
            {
                "thread_id": thread_id,
                "role": "assistant",
                "content": content,
            }
        )

    @staticmethod
    async def _send_webhook(url: str, name: str, summary: str) -> tuple[bool, str | None]:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    url,
                    json={
                        "schedule_name": name,
                        "summary": summary,
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                )
                if response.status_code >= 400:
                    return False, f"HTTP {response.status_code}"
                return True, None
        except httpx.TimeoutException:
            return False, "Request timed out"
        except Exception as e:
            return False, str(e)

    @staticmethod
    async def _send_to_slack(
        session: AsyncSession,
        schedule: Schedule,
        summary: str,
    ) -> tuple[bool, str | None]:
        """Post schedule output to Slack with outbound skill transformation and optional screenshot."""
        result = await session.execute(
            select(SlackWorkspace)
            .where(SlackWorkspace.tenant_id == schedule.tenant_id)
            .where(SlackWorkspace.is_active == True)  # noqa: E712
        )
        workspace = result.scalar_one_or_none()

        if not workspace:
            return False, "No active Slack workspace for tenant"

        if not workspace.default_llm_connection_id:
            return False, "No LLM connection configured for Slack"

        outbound_skills = await CustomSkillRepository(session).get_by_type(schedule.tenant_id, "slack_outbound")

        final_message = summary
        if outbound_skills:
            final_message = await ScheduleService._apply_outbound_skill(
                summary, outbound_skills, workspace.default_llm_connection_id, session
            )

        decrypted = await CryptoService.decrypt_config(workspace.bot_token_encrypted, session)
        bot_token = decrypted.get("bot_token", decrypted) if isinstance(decrypted, dict) else decrypted

        if not schedule.slack_channel_id:
            return False, "No Slack channel ID configured"

        slack_client = SlackService(bot_token)
        try:
            has_dashboard = False
            if schedule.notebook and hasattr(schedule.notebook, "dashboards"):
                has_dashboard = len(schedule.notebook.dashboards) > 0

            if has_dashboard and schedule.notebook_id:
                await ScheduleService._upload_dashboard_screenshot_to_slack(
                    slack_client=slack_client,
                    channel_id=schedule.slack_channel_id,
                    notebook_id=schedule.notebook_id,
                    text_summary=final_message,
                    session=session,
                )
            else:
                if SlackTableParser.has_markdown_table(final_message):
                    table_blocks, remaining_text = SlackTableParser.extract_and_convert_tables(final_message)
                    text_blocks, fallback = markdown_to_slack_blocks(remaining_text)

                    all_blocks = text_blocks + table_blocks
                else:
                    message_blocks, fallback = markdown_to_slack_blocks(final_message)
                    all_blocks = message_blocks

                await slack_client.post_message(
                    channel=schedule.slack_channel_id,
                    text=fallback,
                    blocks=all_blocks,
                )
            return True, None
        except Exception as e:
            logger.error(f"Error posting to Slack: {e}", exc_info=True)
            return False, f"Slack error: {str(e)}"

    @staticmethod
    async def _apply_outbound_skill(
        response: str,
        skills: list[CustomSkill],
        llm_connection_id: UUID,
        session: AsyncSession,
    ) -> str:
        """Apply outbound skills to transform the response using CompletionService."""
        combined_instructions = "\n\n---\n\n".join([s.instructions for s in skills if s.is_active])

        prompt = f"""Apply the following transformation rules to the text below.
Return ONLY the transformed text, nothing else.

TRANSFORMATION RULES:
{combined_instructions}

TEXT TO TRANSFORM:
{response}

TRANSFORMED TEXT:"""

        try:
            result = await CompletionService.complete(
                prompt=prompt,
                llm_connection_id=llm_connection_id,
                session=session,
            )
        except CompletionError as e:
            logger.error(f"Response transformation failed [{e.reason}]: {e.message}")
            return response
        return result if result else response

    @staticmethod
    async def _upload_dashboard_screenshot_to_slack(
        slack_client: SlackService,
        channel_id: str,
        notebook_id: UUID,
        text_summary: str,
        session: AsyncSession,
    ) -> None:
        """
        Generate and upload a dashboard screenshot to Slack for scheduled reports.

        This method implements graceful degradation - if screenshot generation fails,
        it falls back to posting the text summary only.

        Args:
            slack_client: Slack API client
            channel_id: Slack channel ID
            notebook_id: Notebook/dashboard ID to screenshot
            text_summary: Text summary to post as initial_comment
            session: Database session
        """
        from server.utils.deployment import is_feature_enabled

        if not is_feature_enabled("worker_features_enabled"):
            logger.info(
                f"Skipping scheduled report screenshot for notebook {notebook_id}: worker features disabled. "
                "Posting text summary only."
            )
            blocks, fallback = markdown_to_slack_blocks(text_summary)
            await slack_client.post_message(channel=channel_id, text=fallback, blocks=blocks)
            return

        try:
            logger.info(f"Generating screenshot for scheduled report (notebook {notebook_id})")

            png_bytes = await ScreenshotService.capture(session=session, dashboard_id=notebook_id, version=None)

            logger.info(f"Screenshot generated ({len(png_bytes)} bytes), uploading to Slack channel {channel_id}")

            await slack_client.upload_file(
                channel=channel_id,
                file_bytes=png_bytes,
                filename="scheduled_dashboard.png",
                initial_comment="📊Scheduled Report",
            )

            if SlackTableParser.has_markdown_table(text_summary):
                table_blocks, remaining_text = SlackTableParser.extract_and_convert_tables(text_summary)
                text_blocks, fallback = markdown_to_slack_blocks(remaining_text)
                all_blocks = text_blocks + table_blocks
            else:
                all_blocks, fallback = markdown_to_slack_blocks(text_summary)

            await slack_client.post_message(
                channel=channel_id,
                text=fallback,
                blocks=all_blocks,
            )

            logger.info(
                f"Dashboard screenshot posted successfully for scheduled report to channel {channel_id}",
                extra={"posthog_context": {"notebook_id": str(notebook_id)}},
            )

        except ScreenshotServiceError as e:
            logger.warning(f"Failed to generate dashboard screenshot: {e}. Will post text summary only.")
            blocks, fallback = markdown_to_slack_blocks(text_summary)
            await slack_client.post_message(channel=channel_id, text=fallback, blocks=blocks)

        except Exception as e:
            logger.error(f"Unexpected error uploading dashboard screenshot: {e}. Will post text summary only.")
            blocks, fallback = markdown_to_slack_blocks(text_summary)
            await slack_client.post_message(channel=channel_id, text=fallback, blocks=blocks)
