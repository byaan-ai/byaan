from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import case, desc, func, or_, select

from server.auth.tenant_context import set_tenant_id
from server.db.session import AsyncSessionFactory
from server.models.conversation_evaluation import ConversationEvaluation
from server.models.custom_skill import CustomSkill
from server.models.llm_connections import LLMConnection
from server.models.messages import Message
from server.models.notebooks import Notebook
from server.models.queries import Query
from server.models.skill_loop_settings import SkillLoopSettings
from server.models.skill_suggestion import SkillSuggestion
from server.models.slack_conversation import SlackConversation
from server.models.slack_workspace import SlackWorkspace
from server.models.tenant import Tenant
from server.models.threads import Thread
from server.models.user import User
from server.prompts.skill_loop_prompts import (
    build_proposer_refuter_prompt,
    build_verifier_prompt,
    parse_last_json_block,
)
from server.repositories.custom_skill import CustomSkillRepository
from server.repositories.skill_loop_settings import SkillLoopSettingsRepository
from server.repositories.skill_suggestion import SkillSuggestionRepository
from server.schemas.agent import AgentRequest
from server.services.message_service import MessageService
from server.services.skill_suggestion_service import SkillSuggestionService
from server.utils.config_loader import get_email_config, get_skill_loop_config
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

STALE_MINUTES = 30
CONVERSATION_TURN_LIMIT = 30
STARTUP_DELAY_SECONDS = 90
VALID_VERDICTS = ("confirmed", "mistake", "ambiguous")


class ConversationEvaluationService:
    """Background loop that verifies finished conversations and proposes skill improvements."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running: bool = False
        self._interval: int = 1800
        self._max_evals_per_day: int = 20
        self._digest_hour: int = 17
        self._last_digest_date = None
        self._last_digest_by_tenant: dict[UUID, object] = {}

    async def start(self) -> None:
        if self._running:
            logger.warning("Skill loop service already running")
            return

        cfg = get_skill_loop_config()
        self._interval = int(cfg["interval_seconds"])
        self._max_evals_per_day = int(cfg["max_evals_per_day"])
        self._digest_hour = int(cfg["digest_hour"])
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            f"Skill loop service started (interval: {self._interval}s, "
            f"max/day: {self._max_evals_per_day}, digest hour: {self._digest_hour})"
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Skill loop service stopped")

    async def _loop(self) -> None:
        await asyncio.sleep(STARTUP_DELAY_SECONDS)

        while self._running:
            try:
                await self._tick()
                await self._maybe_send_digests()
            except Exception as e:
                logger.error(f"Skill loop error: {e}", exc_info=True)

            await asyncio.sleep(self._interval)

    async def _tick(self) -> None:
        async with AsyncSessionFactory() as session:
            used = await self._evaluations_today(session)
            remaining = self._max_evals_per_day - used
            if remaining <= 0:
                logger.info("Skill loop daily budget exhausted; skipping tick")
                return
            candidates = await self.find_candidate_notebooks(session, remaining)

        if not candidates:
            return

        logger.info(f"Skill loop evaluating {len(candidates)} candidate notebook(s)")
        for candidate in candidates:
            try:
                await self.evaluate_now(candidate["notebook_id"], candidate["tenant_id"], trigger="scheduled")
            except Exception as e:
                logger.error(f"Failed to evaluate notebook {candidate['notebook_id']}: {e}", exc_info=True)

    async def find_candidate_notebooks(self, session, limit: int) -> list[dict]:
        """Notebooks whose latest message is stale and newer than their last evaluation.

        Slack-originated notebooks are prioritized. Returns dicts with notebook_id and tenant_id.
        """
        now = datetime.now(UTC).replace(tzinfo=None)
        cutoff = now - timedelta(minutes=STALE_MINUTES)

        last_msg = (
            select(
                Thread.notebook_id.label("notebook_id"),
                func.max(Message.created_at).label("last_msg"),
            )
            .join(Message, Message.thread_id == Thread.id)
            .group_by(Thread.notebook_id)
            .subquery()
        )
        last_eval = (
            select(
                ConversationEvaluation.notebook_id.label("notebook_id"),
                func.max(ConversationEvaluation.evaluated_at).label("last_eval"),
            )
            .group_by(ConversationEvaluation.notebook_id)
            .subquery()
        )
        slack_notebooks = (
            select(SlackConversation.notebook_id)
            .where(SlackConversation.notebook_id.is_not(None))
            .distinct()
            .subquery()
        )
        is_slack = case((Notebook.id.in_(select(slack_notebooks.c.notebook_id)), 1), else_=0).label("is_slack")

        disabled_tenants = select(SkillLoopSettings.tenant_id).where(SkillLoopSettings.enabled.is_(False)).subquery()

        query = (
            select(Notebook.id, Notebook.tenant_id, last_msg.c.last_msg, is_slack)
            .join(last_msg, last_msg.c.notebook_id == Notebook.id)
            .outerjoin(last_eval, last_eval.c.notebook_id == Notebook.id)
            .where(last_msg.c.last_msg < cutoff)
            .where(Notebook.tenant_id.not_in(select(disabled_tenants.c.tenant_id)))
            .where(or_(last_eval.c.last_eval.is_(None), last_msg.c.last_msg > last_eval.c.last_eval))
            .order_by(desc("is_slack"), last_msg.c.last_msg.asc())
            .limit(limit)
        )
        result = await session.execute(query)
        return [{"notebook_id": row[0], "tenant_id": row[1], "is_slack": bool(row[3])} for row in result.all()]

    async def evaluate_now(self, notebook_id: UUID, tenant_id: UUID, trigger: str = "event") -> str | None:
        async with AsyncSessionFactory() as session:
            set_tenant_id(tenant_id)
            notebook = await session.get(Notebook, notebook_id)
            if not notebook:
                logger.warning(f"Notebook {notebook_id} not found for evaluation")
                return None
            return await self._evaluate_notebook(session, notebook, trigger)

    async def _evaluate_notebook(self, session, notebook: Notebook, trigger: str) -> str:
        notebook_id = notebook.id
        tenant_id = notebook.tenant_id

        history = await MessageService.get_notebook_conversation_history(
            session, str(notebook_id), limit=CONVERSATION_TURN_LIMIT
        )

        gate_reason = self._gate(history)
        if gate_reason:
            await self._record_evaluation(session, tenant_id, notebook_id, trigger, "skipped", {"note": gate_reason})
            return "skipped"

        saved_queries = await self._load_saved_queries(session, notebook_id)

        verifier_prompt = build_verifier_prompt(history, saved_queries)
        verify_text = await self._run_agent(session, notebook, verifier_prompt, tenant_id)
        findings = parse_last_json_block(verify_text)

        if not findings or findings.get("verdict") not in VALID_VERDICTS:
            await self._record_evaluation(
                session,
                tenant_id,
                notebook_id,
                trigger,
                "ambiguous",
                {"parse_error": True, "raw": (verify_text or "")[:5000]},
            )
            return "ambiguous"

        verdict = findings["verdict"]
        source = await self._build_source(session, notebook_id)

        suggestion = None
        if verdict == "mistake":
            suggestion = await self._propose(session, tenant_id, notebook, findings, source)
        elif verdict == "ambiguous" and (findings.get("correction") or "").strip():
            suggestion = await self._create_clarification(session, tenant_id, findings, source)

        await self._record_evaluation(session, tenant_id, notebook_id, trigger, verdict, findings)

        if suggestion is not None:
            await self._notify(session, suggestion)

        return verdict

    def _gate(self, history: list[dict[str, str]]) -> str | None:
        assistant = [m for m in history if m.get("role") == "assistant" and (m.get("content") or "").strip()]
        user = [m for m in history if m.get("role") == "user" and (m.get("content") or "").strip()]
        if not assistant:
            return "no assistant answer in conversation"
        if len(user) < 1:
            return "no user question in conversation"
        return None

    async def _load_saved_queries(self, session, notebook_id: UUID) -> list[dict[str, str]]:
        result = await session.execute(select(Query).where(Query.notebook_id == notebook_id))
        return [{"name": q.name, "query": q.query} for q in result.scalars().all()]

    async def _propose(self, session, tenant_id, notebook, findings, source) -> SkillSuggestion | None:
        skills = await CustomSkillRepository(session).list_org_accessible(tenant_id)
        skills_payload = [{"id": str(s.id), "name": s.name, "instructions": s.instructions} for s in skills]

        prompt = build_proposer_refuter_prompt(findings, skills_payload)
        text = await self._run_agent(session, notebook, prompt, tenant_id)
        data = parse_last_json_block(text)

        if not data or not data.get("survives"):
            return None

        suggestion_type = data.get("suggestion_type") or "edit"
        skill_id = self._match_skill_id(data, skills, suggestion_type)
        patch = None
        if suggestion_type == "edit":
            patch = {
                "section": data.get("section"),
                "before": data.get("before"),
                "after": data.get("after"),
            }

        evidence = {
            "summary": findings.get("summary"),
            "evidence": findings.get("evidence"),
            "correction": findings.get("correction"),
        }

        service = SkillSuggestionService(session)
        return await service.create_suggestion(
            tenant_id=tenant_id,
            suggestion_type=suggestion_type,
            title=(data.get("title") or "Skill improvement")[:300],
            rationale=data.get("rationale") or "",
            confidence=data.get("confidence") or "low",
            skill_id=skill_id,
            evidence=evidence,
            patch=patch,
            proposed_instructions=data.get("proposed_instructions"),
            source=source,
            slack_channel_id=source.get("slack_channel_id"),
            slack_message_ts=source.get("slack_thread_ts"),
        )

    async def _create_clarification(self, session, tenant_id, findings, source) -> SkillSuggestion:
        question = findings["correction"].strip()
        service = SkillSuggestionService(session)
        return await service.create_suggestion(
            tenant_id=tenant_id,
            suggestion_type="clarification",
            title=question[:300],
            rationale=findings.get("summary") or "Clarification needed to resolve an ambiguous answer.",
            confidence="low",
            evidence={"summary": findings.get("summary"), "evidence": findings.get("evidence")},
            source=source,
            slack_channel_id=source.get("slack_channel_id"),
            slack_message_ts=source.get("slack_thread_ts"),
        )

    def _match_skill_id(self, data: dict, skills: list[CustomSkill], suggestion_type: str) -> UUID | None:
        if suggestion_type != "edit":
            return None
        raw_id = data.get("skill_id")
        name = data.get("skill_name")
        for skill in skills:
            if raw_id and str(skill.id) == str(raw_id):
                return skill.id
        for skill in skills:
            if name and skill.name == name:
                return skill.id
        return None

    async def _build_source(self, session, notebook_id: UUID) -> dict:
        result = await session.execute(
            select(SlackConversation).where(SlackConversation.notebook_id == notebook_id).limit(1)
        )
        conversation = result.scalar_one_or_none()
        if conversation:
            return {
                "origin": "slack",
                "notebook_id": str(notebook_id),
                "slack_channel_id": conversation.slack_channel_id,
                "slack_thread_ts": conversation.slack_thread_ts,
            }
        return {
            "origin": "app",
            "notebook_id": str(notebook_id),
            "slack_channel_id": None,
            "slack_thread_ts": None,
        }

    async def _run_agent(self, session, notebook: Notebook, instruction: str, tenant_id: UUID) -> str:
        from server.services.unified_agent import stream_handoff_agent_response

        llm_connection_id = await self._resolve_llm_connection(session, tenant_id, notebook)
        if not llm_connection_id:
            logger.warning(f"No LLM connection for tenant {tenant_id}; cannot run skill-loop agent")
            return ""

        agent_request = AgentRequest(
            message=instruction,
            notebook_id=notebook.id,
            llm_connection_id=llm_connection_id,
        )

        parts: list[str] = []
        async for event in stream_handoff_agent_response(agent_request, session, tenant_id=tenant_id):
            if not event.startswith("data: "):
                continue
            try:
                data = json.loads(event[6:])
            except json.JSONDecodeError:
                continue
            if data.get("type") == "content":
                parts.append(data.get("text", ""))
        return "".join(parts)

    async def _resolve_llm_connection(self, session, tenant_id: UUID, notebook: Notebook) -> UUID | None:
        result = await session.execute(
            select(SlackWorkspace).where(SlackWorkspace.tenant_id == tenant_id).where(SlackWorkspace.is_active == True)  # noqa: E712
        )
        workspace = result.scalar_one_or_none()
        if workspace and workspace.default_llm_connection_id:
            return workspace.default_llm_connection_id

        if notebook.last_used_provider:
            conn_result = await session.execute(
                select(LLMConnection)
                .where(LLMConnection.tenant_id == tenant_id)
                .where(LLMConnection.type == notebook.last_used_provider)
                .limit(1)
            )
            connection = conn_result.scalar_one_or_none()
            if connection:
                return connection.id

        conn_result = await session.execute(select(LLMConnection).where(LLMConnection.tenant_id == tenant_id).limit(1))
        connection = conn_result.scalar_one_or_none()
        return connection.id if connection else None

    async def _record_evaluation(self, session, tenant_id, notebook_id, trigger, verdict, findings) -> None:
        evaluation = ConversationEvaluation(
            tenant_id=tenant_id,
            notebook_id=notebook_id,
            trigger=trigger,
            verdict=verdict,
            findings=findings,
        )
        session.add(evaluation)
        await session.commit()

    async def _notify(self, session, suggestion: SkillSuggestion) -> None:
        try:
            from server.services.slack_suggestion_service import notify_suggestion_created
        except ImportError:
            logger.info("Slack suggestion notifier not available; skipping notification")
            return
        try:
            await notify_suggestion_created(session, suggestion)
        except Exception as e:
            logger.warning(f"Failed to notify suggestion {suggestion.id}: {e}")

    async def _evaluations_today(self, session) -> int:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        count = await session.scalar(
            select(func.count()).select_from(ConversationEvaluation).where(ConversationEvaluation.evaluated_at >= start)
        )
        return int(count or 0)

    async def _maybe_send_digests(self) -> None:
        now = datetime.now()
        async with AsyncSessionFactory() as session:
            due = await self._tenants_due_for_digest(session, now)

        for tenant_id in due:
            try:
                async with AsyncSessionFactory() as session:
                    await self.send_tenant_digest(session, tenant_id)
                self._last_digest_by_tenant[tenant_id] = now.date()
            except Exception as e:
                logger.error(f"Failed to send skill digest for tenant {tenant_id}: {e}", exc_info=True)

    async def _tenants_due_for_digest(self, session, now: datetime) -> list[UUID]:
        """Tenants that have activity today and whose per-tenant digest is enabled and past its hour."""
        settings_repo = SkillLoopSettingsRepository(session)
        due: list[UUID] = []
        for tenant_id in await self._tenants_needing_digest(session):
            settings = await settings_repo.get_or_defaults(tenant_id)
            if not settings.enabled or not settings.digest_enabled:
                continue
            if now.hour < settings.digest_hour:
                continue
            if self._last_digest_by_tenant.get(tenant_id) == now.date():
                continue
            due.append(tenant_id)
        return due

    async def _tenants_needing_digest(self, session) -> list[UUID]:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        tenants: set[UUID] = set()

        pending = await session.execute(
            select(SkillSuggestion.tenant_id).where(SkillSuggestion.status == "pending").distinct()
        )
        tenants.update(row[0] for row in pending.all())

        evaluated = await session.execute(
            select(ConversationEvaluation.tenant_id).where(ConversationEvaluation.evaluated_at >= start).distinct()
        )
        tenants.update(row[0] for row in evaluated.all())
        return list(tenants)

    async def send_tenant_digest(self, session, tenant_id: UUID) -> bool:
        tenant = await session.get(Tenant, tenant_id)
        if not tenant:
            return False

        stats = await self._digest_stats(session, tenant_id)
        pending = await SkillSuggestionRepository(session).list_by_tenant(tenant_id, status="pending")
        suggestions = await self._digest_suggestions(session, pending)
        recipients = await self._digest_recipients(session, tenant)

        if not recipients:
            logger.info(f"No owner/admin recipients for tenant {tenant_id}; skipping digest")
            return False

        from server.services.tenant_service import _get_email_service

        email_service = _get_email_service()
        if not email_service:
            logger.info("No email service configured; skipping skill digest")
            return False

        frontend_url = get_email_config()["frontend_url"]
        sent = False
        for email in recipients:
            try:
                await email_service.send_skill_digest_email(
                    to_email=email,
                    tenant_name=tenant.name,
                    stats=stats,
                    suggestions=suggestions,
                    frontend_url=frontend_url,
                )
                sent = True
            except Exception as e:
                logger.warning(f"Failed to send skill digest to {email}: {e}")
        return sent

    async def _digest_stats(self, session, tenant_id: UUID) -> dict:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        rows = await session.execute(
            select(ConversationEvaluation.verdict, func.count())
            .where(ConversationEvaluation.tenant_id == tenant_id)
            .where(ConversationEvaluation.evaluated_at >= start)
            .group_by(ConversationEvaluation.verdict)
        )
        counts = dict(rows.all())
        return {
            "evaluated": sum(counts.values()),
            "confirmed": counts.get("confirmed", 0),
            "mistake": counts.get("mistake", 0),
            "questions": counts.get("ambiguous", 0),
        }

    async def _digest_suggestions(self, session, pending: list[SkillSuggestion]) -> list[dict]:
        skill_ids = [s.skill_id for s in pending if s.skill_id]
        names: dict[UUID, str] = {}
        if skill_ids:
            rows = await session.execute(select(CustomSkill.id, CustomSkill.name).where(CustomSkill.id.in_(skill_ids)))
            names = {row[0]: row[1] for row in rows.all()}

        result = []
        for suggestion in pending:
            skill_name = names.get(suggestion.skill_id)
            if not skill_name:
                skill_name = "New skill" if suggestion.suggestion_type == "new_skill" else "General"
            result.append({"title": suggestion.title, "skill_name": skill_name})
        return result

    async def _digest_recipients(self, session, tenant: Tenant) -> list[str]:
        from server.services.tenant_service import TenantService

        members = await TenantService.list_members_with_users(tenant.id, session)
        emails: set[str] = set()
        for member in members:
            if member.role in ("owner", "admin") and member.user and member.user.email:
                emails.add(member.user.email)

        owner = await session.get(User, tenant.owner_id)
        if owner and owner.email:
            emails.add(owner.email)
        return list(emails)


skill_loop_service = ConversationEvaluationService()
