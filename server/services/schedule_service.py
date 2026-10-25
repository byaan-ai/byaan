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
        llm_connection_id, pinned_model = await ScheduleService._resolve_run_model(session, schedule)
        if not llm_connection_id:
            await ScheduleService._handle_run_failure(session, schedule, "No LLM connection configured")
            return {"status": "failed", "error": "No LLM connection configured"}

        current_datetime = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

        last_findings_block = await ScheduleService._format_last_findings(session, schedule)

        scheduled_run_prompt = f"""## SCHEDULED MONITOR RUN

Current execution time: {current_datetime}

You are running a recurring monitor. The user set this up ahead of time; nobody is
watching this run live. Follow their instruction, run fresh queries each time (data
can change between runs — do not reuse cached results), and summarize what you
found.

**User's monitor instruction:**
{schedule.instruction or "(no instruction provided)"}

{last_findings_block}

Date handling:
- "past week" / "last week" = last 7 days
- "this week" = start of current week to today
- "last month" = previous calendar month
- "past 30 days" = last 30 days

When you are done, call ``record_monitor_findings`` exactly once:
- ``outcome='alert'`` when something in the instruction's alert criteria matches.
- ``outcome='no_change'`` when the data looks the same as the previous run.
- ``outcome='ok'`` otherwise.
Include a short ``headline`` and (for alerts) a ``findings`` list with
``entity``, ``check``, ``value``, ``previous_value``, and ``why``.

Then produce the human-readable summary. Use markdown tables for query results
and put units in headers (e.g. "Revenue (USD)"). Do not emit tool-call markers
in the final text.
"""

        agent_request = AgentRequest(
            message=scheduled_run_prompt,
            notebook_id=schedule.notebook_id,
            llm_connection_id=llm_connection_id,
            model=pinned_model,
            scheduled_run=True,
            scheduled_run_schedule_id=str(schedule.id),
        )

        from server.tools.monitors import MANUAL_RUN_PENDING, RUN_FINDINGS

        schedule_key = str(schedule.id)
        RUN_FINDINGS.pop(schedule_key, None)
        trigger_label = "manual" if schedule_key in MANUAL_RUN_PENDING else "scheduled"
        MANUAL_RUN_PENDING.discard(schedule_key)

        response_parts: list[str] = []
        try:
            async for event in stream_handoff_agent_response(
                agent_request,
                session,
                tenant_id=schedule.tenant_id,
                user_id=schedule.created_by,
            ):
                if event.startswith("data: "):
                    try:
                        data = json.loads(event[6:])
                        etype = data.get("type")
                        if etype == "content":
                            response_parts.append(data.get("text", ""))
                        elif etype == "html_edit_complete":
                            logger.info(f"Dashboard generation detected for schedule {schedule.id}")
                        elif etype == "error":
                            await ScheduleService._handle_run_failure(session, schedule, data.get("text", ""))
                            return {"status": "failed", "error": data.get("text", "Unknown error")}
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            logger.error(f"Error running agent for schedule {schedule.id}: {e}", exc_info=True)
            await ScheduleService._handle_run_failure(session, schedule, str(e))
            return {"status": "failed", "error": str(e)}

        monitor_findings = RUN_FINDINGS.pop(schedule_key, None)

        agent_response = _clean_tool_markers("".join(response_parts))
        outcome = (monitor_findings or {}).get("outcome") or "ok"
        headline = (monitor_findings or {}).get("headline") or ""
        findings = (monitor_findings or {}).get("findings") or []

        message = await ScheduleService._write_to_notebook(session, schedule, agent_response, start_time)

        deliveries: list[dict[str, Any]] = []

        should_notify = ScheduleService._should_notify(schedule.notify_policy, outcome, agent_response)

        slack_error = None
        if should_notify and schedule.slack_channel_id and agent_response.strip():
            override_broadcast = outcome == "alert"
            _, slack_error = await ScheduleService._send_to_slack(
                session, schedule, agent_response, force_broadcast=override_broadcast
            )
            deliveries.append({"target": "slack", "ok": slack_error is None, "error": slack_error})

        webhook_error = None
        if should_notify and schedule.webhook_url:
            _, webhook_error = await ScheduleService._send_webhook(
                schedule.webhook_url,
                schedule.name,
                agent_response,
            )
            deliveries.append({"target": "webhook", "ok": webhook_error is None, "error": webhook_error})

        duration_ms = int((datetime.now(UTC) - start_time).total_seconds() * 1000)
        has_errors = bool(webhook_error or slack_error)
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
                "outcome": outcome,
                "headline": headline[:500] if headline else None,
                "findings": findings,
                "deliveries": deliveries,
                "trigger": trigger_label,
            }
        )

        from server.repositories.schedules import ScheduleRepository as _SchedRepo

        await _SchedRepo(session).reset_failure(schedule.id)

        return {"status": "success", "summary": agent_response, "message_id": message.id if message else None}

    @staticmethod
    async def _resolve_run_model(session: AsyncSession, schedule: Schedule) -> tuple[Any, str | None]:
        """Return (llm_connection_id, model) using pinned values first, then fallbacks."""
        if schedule.llm_connection_id:
            return schedule.llm_connection_id, schedule.model

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
                                return conn.id, model
                        except Exception:
                            pass
                if connections:
                    return connections[0].id, model

        if not llm_connection_id:
            conn_result = await session.execute(
                select(LLMConnection).where(LLMConnection.tenant_id == schedule.tenant_id).limit(1)
            )
            connection = conn_result.scalar_one_or_none()
            if connection:
                return connection.id, None

        return llm_connection_id, None

    @staticmethod
    async def _format_last_findings(session: AsyncSession, schedule: Schedule) -> str:
        """Pull the most recent run's findings so the agent can compare against them."""
        from server.repositories.schedules import ScheduleRunRepository as _RunRepo

        runs = await _RunRepo(session).get_recent_runs(str(schedule.id), limit=1)
        if not runs:
            return ""
        run = runs[0]
        if not (run.findings or run.headline):
            return ""
        parts = ["**Previous run:**"]
        if run.headline:
            parts.append(f"- {run.headline}")
        if run.findings:
            for f in run.findings[:10]:
                entity = f.get("entity") or ""
                check = f.get("check") or ""
                value = f.get("value")
                prev = f.get("previous_value")
                why = f.get("why") or ""
                parts.append(f"  - {entity} · {check}: {value} (was {prev}) — {why}")
        return "\n".join(parts)

    @staticmethod
    def _should_notify(policy: str, outcome: str, agent_response: str) -> bool:
        if not agent_response.strip():
            return False
        if policy == "always":
            return True
        if policy == "on_alert":
            return outcome == "alert"
        if policy == "on_change":
            return outcome != "no_change"
        return True

    @staticmethod
    async def _handle_run_failure(session: AsyncSession, schedule: Schedule, error: str) -> None:
        """Record failure, auto-pause after 3 consecutive."""
        from server.repositories.schedules import ScheduleRepository as _SchedRepo

        repo = _SchedRepo(session)
        count = await repo.increment_failure(schedule.id)
        if count >= 3:
            await repo.set_status(schedule.id, "auto_paused")
            logger.warning(f"Monitor {schedule.id} auto-paused after {count} consecutive failures: {error}")

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
        force_broadcast: bool = False,
    ) -> tuple[bool, str | None]:
        """Post schedule output to Slack with outbound skill transformation and optional screenshot.

        ``force_broadcast=True`` enables ``reply_broadcast`` for alert outcomes
        even when the schedule normally posts quietly in-thread.
        """
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
        thread_ts = schedule.slack_thread_ts
        reply_broadcast = (schedule.broadcast_to_channel or force_broadcast) if thread_ts else False
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
                    thread_ts=thread_ts,
                    reply_broadcast=reply_broadcast,
                )
            else:
                if SlackTableParser.has_markdown_table(final_message):
                    table_blocks, remaining_text = SlackTableParser.extract_and_convert_tables(final_message)
                    text_blocks, fallback = markdown_to_slack_blocks(remaining_text)

                    all_blocks = text_blocks + table_blocks
                else:
                    message_blocks, fallback = markdown_to_slack_blocks(final_message)
                    all_blocks = message_blocks

                await ScheduleService._post_with_thread_fallback(
                    slack_client=slack_client,
                    schedule=schedule,
                    session=session,
                    text=fallback,
                    blocks=all_blocks,
                    thread_ts=thread_ts,
                    reply_broadcast=reply_broadcast,
                )
            return True, None
        except Exception as e:
            logger.error(f"Error posting to Slack: {e}", exc_info=True)
            return False, f"Slack error: {str(e)}"

    @staticmethod
    async def _post_with_thread_fallback(
        slack_client: SlackService,
        schedule: Schedule,
        session: AsyncSession,
        text: str,
        blocks: list,
        thread_ts: str | None,
        reply_broadcast: bool,
    ) -> None:
        """Post to thread; on thread_not_found fall back to channel root and clear the stored thread."""
        from slack_sdk.errors import SlackApiError

        from server.repositories.schedules import ScheduleRepository

        try:
            await slack_client.post_message(
                channel=schedule.slack_channel_id,
                text=text,
                blocks=blocks,
                thread_ts=thread_ts,
                reply_broadcast=reply_broadcast if thread_ts else False,
            )
        except SlackApiError as e:
            err = (e.response or {}).get("error", "")
            if thread_ts and err in {"thread_not_found", "not_in_channel", "channel_not_found"}:
                logger.warning(f"Thread post failed for schedule {schedule.id} ({err}); falling back to channel root")
                await ScheduleRepository(session).clear_lost_thread(schedule.id)
                await slack_client.post_message(
                    channel=schedule.slack_channel_id,
                    text=text,
                    blocks=blocks,
                )
            else:
                raise

    @staticmethod
    def preview_fire_times(cron_expression: str, tz: str, count: int = 3) -> list[datetime]:
        """Return the next `count` fire times for a cron expression in the given tz."""
        try:
            tz_obj = pytz.timezone(tz)
        except pytz.UnknownTimeZoneError:
            tz_obj = pytz.UTC
        now = datetime.now(tz_obj)
        cron = croniter(cron_expression, now)
        return [cron.get_next(datetime) for _ in range(count)]

    @staticmethod
    async def auto_create_notebook_for_cron(session: AsyncSession, tenant_id: UUID, name: str) -> UUID:
        """Create an empty notebook so pure-prompt crons have a conversation-memory container."""
        from server.models.notebooks import Notebook

        notebook = Notebook(
            tenant_id=tenant_id,
            notebook_name=name[:255] if name else "Scheduled report",
        )
        session.add(notebook)
        await session.commit()
        await session.refresh(notebook)
        return notebook.id

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
        thread_ts: str | None = None,
        reply_broadcast: bool = False,
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
            await slack_client.post_message(
                channel=channel_id,
                text=fallback,
                blocks=blocks,
                thread_ts=thread_ts,
                reply_broadcast=reply_broadcast if thread_ts else False,
            )
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
                thread_ts=thread_ts,
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
            await slack_client.post_message(
                channel=channel_id,
                text=fallback,
                blocks=blocks,
                thread_ts=thread_ts,
                reply_broadcast=reply_broadcast if thread_ts else False,
            )

        except Exception as e:
            logger.error(f"Unexpected error uploading dashboard screenshot: {e}. Will post text summary only.")
            blocks, fallback = markdown_to_slack_blocks(text_summary)
            await slack_client.post_message(
                channel=channel_id,
                text=fallback,
                blocks=blocks,
                thread_ts=thread_ts,
                reply_broadcast=reply_broadcast if thread_ts else False,
            )
