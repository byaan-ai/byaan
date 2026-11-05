from __future__ import annotations

import asyncio
import os
import re
import shutil
import socket
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.tenant_context import set_tenant_id
from server.config.storage import handoffs_root
from server.db.session import AsyncSessionFactory
from server.models.custom_skill import CustomSkill
from server.models.github_repository import GitHubRepository
from server.models.handoff import WORKING_STATUSES, Handoff
from server.repositories.handoffs import HandoffEventRepository, HandoffMessageRepository, HandoffRepository, utcnow
from server.schemas.handoff import HandoffRepoConfig
from server.services import handoff_git
from server.services.github_app_service import HandoffGitHubAuth, can_push, create_signed_commit, resolve_auth
from server.services.handoff_agent import AgentRunSpec, AgentUnavailable, ClaudeCodeAdapter
from server.services.handoff_checks import disallowed_paths, scan_added_lines
from server.services.handoff_github import GitHubError, HandoffGitHub, summarize_checks
from server.services.handoff_service import activity_summary, move, record
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

LOOP_SECONDS = 5
HEARTBEAT_SECONDS = 15
STALE_HEARTBEAT = timedelta(seconds=120)
CI_POLL_INTERVAL = timedelta(seconds=30)
REVIEW_POLL_INTERVAL = timedelta(minutes=5)
REVIEW_WINDOW = timedelta(days=14)
NO_CHECKS_GRACE = timedelta(minutes=10)
CI_PENDING_LIMIT = timedelta(minutes=60)
WORKSPACE_TTL = timedelta(hours=72)
INDICATOR_REFRESH_SECONDS = 60
ORPHAN_GRACE_SECONDS = 6 * 3600
CLEANUP_EVERY_SECONDS = 600
MAX_CI_ROUNDS = 3
ANSWER_TIMEOUT_SECONDS = 600
MIN_FREE_DISK_BYTES = 2 * 1024**3
FAILURE_LOG_BUDGET = 30_000
BOT_EMAIL = os.getenv("HANDOFF_BOT_EMAIL", "byaan-bot@users.noreply.byaan.ai")


@dataclass
class RunPlan:
    mode: str
    failure_context: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class PreflightError(Exception):
    pass


def max_concurrent() -> int:
    try:
        return max(1, int(os.getenv("HANDOFF_MAX_CONCURRENT", "3")))
    except ValueError:
        return 3


_SLUG_FILLER = {"a", "an", "the", "to", "for", "of", "by", "with", "and", "in", "on", "from", "that"}


def _slug(text: str, limit: int = 32) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _SLUG_FILLER]
    slug = ""
    for word in words:
        candidate = f"{slug}-{word}" if slug else word
        if len(candidate) > limit:
            break
        slug = candidate
    return slug or (words[0][:limit] if words else "fix")


def pr_title(handoff: Handoff) -> str:
    title = f"[Byaan] {handoff.title}"
    return f"UNTESTED - {title}" if handoff.untested else title


async def sync_pr_title(session: AsyncSession, handoff: Handoff) -> None:
    auth = await _auth_for(session, handoff)
    if auth and handoff.pr_number:
        try:
            await HandoffGitHub(auth.token).update_pull(
                handoff.repo_full_name, handoff.pr_number, title=pr_title(handoff)
            )
        except GitHubError as exc:
            logger.warning(f"[HANDOFF] PR title update failed for {handoff.id}: {exc}")


async def _auth_for(session: AsyncSession, handoff: Handoff) -> HandoffGitHubAuth | None:
    return await resolve_auth(
        session,
        tenant_id=handoff.tenant_id,
        repo_full_name=handoff.repo_full_name,
        requester_id=handoff.requested_by,
        origin=handoff.origin,
    )


class _Hooks:
    def __init__(self, handoff_id: UUID, work_dir: Path, in_prompt: frozenset[UUID] = frozenset()):
        self.handoff_id = handoff_id
        self.work_dir = work_dir
        self.in_prompt = in_prompt
        self.result: dict[str, str] | None = None
        self._waiting_total = 0.0
        self._waiting_since: float | None = None

    async def on_tool(self, name: str, target: str | None) -> None:
        if target:
            target = target.replace(f"{self.work_dir}/", "").replace(str(self.work_dir), ".")
        async with AsyncSessionFactory() as session:
            await record(
                session,
                self.handoff_id,
                "executor",
                "tool",
                f"{name} {target or ''}".strip(),
                {"tool": name, "target": target},
                notify=False,
            )

    async def report_progress(self, step: str, note: str, estimate: float | None) -> str:
        async with AsyncSessionFactory() as session:
            message = f"{step}: {note}" if step and note else (note or step)
            data = {"step": step, "estimate": max(0.0, min(1.0, estimate)) if estimate is not None else None}
            await record(session, self.handoff_id, "agent", "progress", message, data)
        return "Progress recorded."

    async def ask_human(self, question: str, options: list[str]) -> str:
        self._waiting_since = time.monotonic()
        try:
            async with AsyncSessionFactory() as session:
                previous = await HandoffRepository(session).status_of(self.handoff_id)
                moved = previous in ("running", "fixing") and await move(
                    session, self.handoff_id, "waiting_on_human", from_statuses=(previous,)
                )
                if not moved:
                    return "The handoff is no longer running; stop working."
                event = await record(session, self.handoff_id, "agent", "question", question, {"options": options})
            deadline = time.monotonic() + ANSWER_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                await asyncio.sleep(3)
                async with AsyncSessionFactory() as session:
                    status = await HandoffRepository(session).status_of(self.handoff_id)
                    if status != "waiting_on_human":
                        return "The handoff was stopped; finish now without further changes."
                    answers = await HandoffMessageRepository(session).pending(self.handoff_id, "answer")
                    answer = next((a for a in answers if a.question_seq in (None, event.seq)), None)
                    if answer:
                        await HandoffMessageRepository(session).mark_delivered([a.id for a in answers])
                        await move(
                            session,
                            self.handoff_id,
                            previous,
                            from_statuses=("waiting_on_human",),
                            data={"resumed": True},
                        )
                        who = answer.author_label or "A teammate"
                        return f"{who} answered: {answer.body}"
            async with AsyncSessionFactory() as session:
                await move(
                    session, self.handoff_id, previous, from_statuses=("waiting_on_human",), data={"resumed": True}
                )
                await record(session, self.handoff_id, "system", "answer", "No answer within 10 minutes", notify=False)
            return "No answer within 10 minutes. Proceed on your stated assumption and list it under risks in submit_result."
        finally:
            self._waiting_total += time.monotonic() - self._waiting_since
            self._waiting_since = None

    async def submit_result(self, title: str, summary: str, tests: str, risks: str, steps: list[str]) -> str:
        self.result = {
            "title": title.strip()[:120],
            "summary": summary.strip(),
            "tests": tests.strip(),
            "risks": risks.strip(),
            "steps": [step.strip()[:300] for step in steps if step.strip()],
        }
        async with AsyncSessionFactory() as session:
            await record(session, self.handoff_id, "agent", "result", self.result["title"], self.result, notify=False)
        return "Result recorded. Stop now; Byaan will commit, push and open the pull request."

    async def pending_followups(self) -> list[tuple[Any, str]]:
        async with AsyncSessionFactory() as session:
            messages = await HandoffMessageRepository(session).pending(self.handoff_id, "followup")
        return [
            (
                m.id,
                f"Follow-up from {m.author_label or 'a teammate'}: {m.body}\n\n"
                "Apply it, then call submit_result again so the summary covers the whole change.",
            )
            for m in messages
            if m.id not in self.in_prompt
        ]

    async def mark_delivered(self, message_ids: list[Any]) -> None:
        self.result = None
        async with AsyncSessionFactory() as session:
            await HandoffMessageRepository(session).mark_delivered(message_ids)
            await record(session, self.handoff_id, "system", "status", "Follow-up delivered to Claude", notify=False)

    async def stop_requested(self) -> bool:
        async with AsyncSessionFactory() as session:
            return await HandoffRepository(session).status_of(self.handoff_id) in ("cancelled", "taken_over")

    def waiting_seconds(self) -> float:
        current = time.monotonic() - self._waiting_since if self._waiting_since else 0.0
        return self._waiting_total + current

    def is_waiting(self) -> bool:
        return self._waiting_since is not None


class HandoffRunner:
    def __init__(self) -> None:
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"[:64]
        self._loop_tasks: list[asyncio.Task] = []
        self._runs: dict[UUID, asyncio.Task] = {}
        self._running = False
        self._last_cleanup = 0.0
        self._last_indicator_refresh = 0.0
        self.adapter = ClaudeCodeAdapter()

    @property
    def free_slots(self) -> int:
        return max_concurrent() - len(self._runs)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._loop_tasks = [
            asyncio.create_task(self._loop((self._sweep, self._claim))),
            asyncio.create_task(
                self._loop((self._poll_ci, self._poll_reviews, self._refresh_indicators, self._cleanup))
            ),
        ]
        logger.info(f"[HANDOFF] Runner started as {self.worker_id} (max {max_concurrent()} concurrent)")

    async def stop(self) -> None:
        self._running = False
        for task in self._loop_tasks:
            task.cancel()
        await asyncio.gather(*self._loop_tasks, return_exceptions=True)
        for task in list(self._runs.values()):
            task.cancel()
        await asyncio.gather(*self._runs.values(), return_exceptions=True)
        async with AsyncSessionFactory() as session:
            for handoff in await HandoffRepository(session).owned_by(self.worker_id):
                await move(
                    session, handoff.id, "failed", message="Byaan shut down during the run", error="Byaan shut down"
                )

    async def _loop(self, steps: tuple) -> None:
        """Claiming new work and watching GitHub run in separate loops so slow GitHub polling never delays a start."""
        await asyncio.sleep(10)
        while self._running:
            for step in steps:
                try:
                    await step()
                except Exception as exc:
                    logger.error(f"[HANDOFF] Runner step {step.__name__} failed: {exc}", exc_info=True)
            await asyncio.sleep(LOOP_SECONDS)

    async def _sweep(self) -> None:
        async with AsyncSessionFactory() as session:
            for handoff in await HandoffRepository(session).stale_working(utcnow() - STALE_HEARTBEAT):
                if handoff.id in self._runs:
                    continue
                set_tenant_id(handoff.tenant_id)
                await move(
                    session,
                    handoff.id,
                    "failed",
                    from_statuses=(handoff.status,),
                    message="Interrupted: Byaan restarted or lost the worker. Retry to start again.",
                    error="Interrupted",
                )

    async def _claim(self) -> None:
        if not self._running or self.free_slots <= 0:
            return
        async with AsyncSessionFactory() as session:
            repo = HandoffRepository(session)
            candidate_ids = [h.id for h in await repo.queued_candidates()]
            for handoff_id in candidate_ids:
                if self.free_slots <= 0:
                    return
                claimed = await repo.transition(
                    handoff_id,
                    "preparing",
                    from_statuses=("queued",),
                    worker_id=self.worker_id,
                    heartbeat_at=utcnow(),
                    started_at=utcnow(),
                    finished_at=None,
                    error=None,
                )
                if claimed:
                    await HandoffEventRepository(session).append(
                        handoff_id, "executor", "status", "Preparing workspace", {"status": "preparing"}
                    )
                    self._spawn(handoff_id, RunPlan(mode="work"))

    def _spawn(self, handoff_id: UUID, plan: RunPlan) -> None:
        task = asyncio.create_task(self._run(handoff_id, plan))
        self._runs[handoff_id] = task
        task.add_done_callback(lambda _t: self._runs.pop(handoff_id, None))

    async def _heartbeat(self, handoff_id: UUID) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            async with AsyncSessionFactory() as session:
                await HandoffRepository(session).heartbeat(handoff_id, self.worker_id)

    async def _run(self, handoff_id: UUID, plan: RunPlan) -> None:
        heartbeat = asyncio.create_task(self._heartbeat(handoff_id))
        try:
            async with AsyncSessionFactory() as session:
                await self._execute(session, handoff_id, plan)
        except asyncio.CancelledError:
            raise
        except PreflightError as exc:
            await self._fail(handoff_id, plan, str(exc))
        except Exception as exc:
            logger.error(f"[HANDOFF] Run {handoff_id} crashed: {exc}", exc_info=True)
            await self._fail(handoff_id, plan, f"Something went wrong: {str(exc)[:300]}")
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    @staticmethod
    async def _fail(handoff_id: UUID, plan: RunPlan, message: str) -> None:
        """A failed CI-fix round leaves the PR open, so it ends as ci_failed and review polling continues."""
        async with AsyncSessionFactory() as session:
            for target in ("ci_failed", "failed") if plan.mode == "fix" else ("failed",):
                if await move(
                    session, handoff_id, target, from_statuses=WORKING_STATUSES, message=message, error=message
                ):
                    return

    async def _execute(self, session: AsyncSession, handoff_id: UUID, plan: RunPlan) -> None:
        repo_store = HandoffRepository(session)
        handoff = await repo_store.get_unscoped(handoff_id)
        set_tenant_id(handoff.tenant_id)
        repo = (
            await session.get(GitHubRepository, handoff.github_repository_id) if handoff.github_repository_id else None
        )
        if not repo or not repo.handoff_enabled:
            raise PreflightError("Handoffs are turned off for this repository, or it was disconnected.")
        config = HandoffRepoConfig.from_db(repo.handoff_config)
        auth, oauth_token = await self._preflight(session, handoff)
        github = HandoffGitHub(auth.token)

        is_fix = plan.mode == "fix"
        workspace = handoff_git.Workspace.for_handoff(handoff.id)
        reuse = False
        if handoff.head_sha:
            reuse = workspace.exists and await handoff_git.head_sha(workspace.repo) == handoff.head_sha
            if not reuse:
                workspace = await handoff_git.create_workspace(
                    handoff.id, handoff.repo_full_name, handoff.branch, auth.token
                )
                await repo_store.update_fields(handoff.id, workspace_path=str(workspace.root), claude_session_id=None)
                handoff.claude_session_id = None
        elif handoff.branch and handoff.base_sha and workspace.exists:
            reuse = await handoff_git.head_sha(workspace.repo) == handoff.base_sha
        if not handoff.head_sha and not reuse:
            branch = f"byaan/h-{str(handoff.id)[:8]}-{_slug(handoff.title)}"
            if await handoff_git.remote_branch_exists(handoff.repo_full_name, branch, auth.token):
                branch = f"{branch}-{int(time.time()) % 100000}"
            workspace = await handoff_git.create_workspace(
                handoff.id, handoff.repo_full_name, handoff.base_branch, auth.token, new_branch=branch
            )
            base_sha = await handoff_git.head_sha(workspace.repo)
            has_ci = await github.has_ci(handoff.repo_full_name, base_sha)
            await repo_store.update_fields(
                handoff.id,
                branch=branch,
                base_sha=base_sha,
                workspace_path=str(workspace.root),
                untested=not has_ci,
                claude_session_id=None,
            )
            handoff = await repo_store.refresh(handoff)

        from_status = "fixing" if is_fix else "preparing"
        if not is_fix:
            first_start = not handoff.pr_number
            if not await move(
                session,
                handoff.id,
                "running",
                from_statuses=(from_status,),
                source="executor",
                data={"first_start": first_start},
                message="Claude Code started",
            ):
                return

        inbox = HandoffMessageRepository(session)
        followups = await inbox.pending(handoff.id, "followup")
        requests = "\n".join(f"- {m.author_label or 'A teammate'}: {m.body}" for m in followups)
        prompt = self._user_prompt(handoff, plan, requests)
        if not is_fix and handoff.pr_number and handoff.head_sha:
            ci_context = await self._current_ci_failure(github, handoff)
            if ci_context:
                prompt = f"{prompt}\n\nCI is currently failing on this pull request:\n\n{ci_context}"
        spec = AgentRunSpec(
            cwd=workspace.work,
            home=workspace.home,
            prompt=prompt,
            system_prompt=await self._system_prompt(session, handoff, repo, workspace, config),
            oauth_token=oauth_token,
            paths=config.paths,
            minutes=config.minutes,
            resume_session_id=handoff.claude_session_id if reuse else None,
            data_tools=self._data_tools(),
            data_context=self._data_context(handoff),
        )
        hooks = _Hooks(handoff.id, workspace.work, frozenset(m.id for m in followups))
        result = await self.adapter.run(spec, hooks)

        handoff = await repo_store.refresh(handoff)
        await repo_store.update_fields(
            handoff.id,
            cost_usd=(handoff.cost_usd or 0.0) + result.cost_usd,
            claude_session_id=result.session_id or handoff.claude_session_id,
        )
        handoff = await repo_store.refresh(handoff)
        if handoff.status in ("cancelled", "taken_over"):
            return
        if result.outcome != "success":
            message = {
                "rate_limited": "Claude usage limit reached. Retry once the limit resets.",
            }.get(result.outcome, result.error or "Claude stopped with an error")
            target = "ci_failed" if is_fix and handoff.pr_number else "failed"
            await move(session, handoff.id, target, from_statuses=WORKING_STATUSES, message=message, error=message)
            return

        if followups:
            await inbox.mark_delivered([m.id for m in followups])
        await self._push(
            session,
            handoff,
            workspace,
            auth,
            github,
            hooks.result,
            result.final_text,
            is_fix,
            plan.extra.get("failing", []),
        )

    async def _preflight(self, session: AsyncSession, handoff: Handoff) -> tuple[HandoffGitHubAuth, str]:
        from server.services.claude_oauth_service import get_active_token_with_refresh
        from server.services.handoff_agent import resolve_cli

        if not shutil.which("git"):
            raise PreflightError("git is not installed on this Byaan host.")
        try:
            resolve_cli()
        except AgentUnavailable as exc:
            raise PreflightError(str(exc)) from exc
        token = await get_active_token_with_refresh()
        if not token:
            raise PreflightError(
                "Claude Code is not connected or its login expired. Reconnect it in Settings → AI Models → Claude Code."
            )
        auth = await _auth_for(session, handoff)
        if not auth:
            raise PreflightError("No GitHub credentials available. Connect GitHub in Settings → GitHub.")
        try:
            if not await can_push(auth, HandoffGitHub(auth.token), handoff.repo_full_name):
                raise PreflightError(f"The connected GitHub account can't push to {handoff.repo_full_name}.")
        except GitHubError as exc:
            raise PreflightError(f"GitHub rejected access to {handoff.repo_full_name}: {exc}") from exc
        if shutil.disk_usage(handoffs_root()).free < MIN_FREE_DISK_BYTES:
            raise PreflightError("Less than 2 GB of free disk space on the Byaan host.")
        return auth, token

    def _data_context(self, handoff: Handoff) -> dict[str, Any]:
        return {
            "tenant_id": str(handoff.tenant_id),
            "notebook_id": str(handoff.notebook_id) if handoff.notebook_id else None,
            "user_id": str(handoff.requested_by) if handoff.requested_by else None,
        }

    def _data_tools(self) -> list[Any]:
        from server.tools.agentic import get_database_schema, get_dataset_schema_by_id, search_datasets
        from server.tools.dataframe import get_duckdb_tools
        from server.tools.mongo import get_mongo_tools
        from server.tools.sql import get_sql_tools

        return [
            search_datasets,
            get_dataset_schema_by_id,
            get_database_schema,
            *get_sql_tools(),
            *get_mongo_tools(),
            *get_duckdb_tools(),
        ]

    async def _system_prompt(
        self,
        session: AsyncSession,
        handoff: Handoff,
        repo: GitHubRepository,
        workspace: handoff_git.Workspace,
        config: HandoffRepoConfig,
    ) -> str:
        allow = ", ".join(config.paths.allow) or "the whole repository"
        deny = ", ".join(config.paths.effective_deny())
        skills = (
            await session.execute(
                select(CustomSkill.name, CustomSkill.instructions).where(
                    CustomSkill.github_repo_id == repo.id, CustomSkill.is_active.is_(True)
                )
            )
        ).all()
        skill_text = "\n\n".join(f"### {name}\n{text[:6000]}" for name, text in skills)[:15000]
        repo_rules = handoff_git.read_repo_instructions(workspace.repo)
        sections = [
            "This session is a Byaan handoff: fix one specific problem in this repository and nothing else.",
            "Rules:\n"
            f"- You may only change files matching: {allow}. Never change: {deny}.\n"
            "- Never edit CI configuration or weaken tests to make checks pass.\n"
            "- Add or update a test that fails before your fix and passes after it.\n"
            "- Do not commit, push or create branches; Byaan handles git and the pull request.\n"
            "- Do not install dependencies or set up environments (no pip/npm/apt installs, no virtualenvs); "
            "the repository's CI runs the tests. Only run checks that work with what is already installed.\n"
            "- Never put secrets, credentials or row-level data (names, records, identifiers) in code, tests or text.\n"
            "- Call report_progress after meaningful milestones, ask_human only when truly blocked, "
            "and submit_result when you are done.",
            "Before calling submit_result, carefully review every change you made:\n"
            "- Read your full diff as a strict code reviewer would.\n"
            "- Make sure none of your changes break existing behaviour: check every caller, import and usage of "
            "anything you modified, renamed or removed.\n"
            "- Make sure your new code is correct, handles edge cases and matches the repository's existing style.\n"
            "- Remove debugging leftovers and any edit that is not needed for this task.\n"
            "- Fix any problem you find before calling submit_result, and mention remaining risks under risks. "
            "Don't mention missing local dependencies or tests not being run locally anywhere in submit_result; "
            "CI runs the tests.",
            f"Repository: {handoff.repo_full_name}, base branch {handoff.base_branch}.",
        ]
        if repo_rules:
            sections.append(f"Repository instructions:\n{repo_rules}")
        if skill_text:
            sections.append(f"Byaan's analysis of this repository:\n{skill_text}")
        return "\n\n".join(sections)

    def _user_prompt(self, handoff: Handoff, plan: RunPlan, requests: str = "") -> str:
        brief = handoff.brief or {}
        if plan.mode == "fix":
            return (
                "CI failed on your pull request. Fix every failure below without weakening tests or editing CI config, "
                "then call submit_result again. If you re-run a check locally, run it exactly as CI does with the "
                "repository's own configuration (no --isolated flags or narrower rule selections).\n\n"
                f"{plan.failure_context}"
            )
        if handoff.pr_number and requests:
            return (
                f'Your pull request #{handoff.pr_number} for "{handoff.title}" is open on this branch. '
                "Apply the requested changes below, then call submit_result.\n\n"
                f"Original task:\n{brief.get('problem', '')}\n\nRequested changes:\n{requests}"
            )
        if handoff.pr_number:
            return (
                f'Your pull request #{handoff.pr_number} for "{handoff.title}" is open on this branch and the task '
                "was re-started without new instructions. Check that the branch fully covers the task below and "
                "change only what is missing or broken. If nothing is missing, make no changes and call "
                "submit_result explaining that the pull request already covers it.\n\n"
                f"Task:\n{brief.get('problem', '')}"
                + (f"\n\nDone when: {brief['acceptance_check']}" if brief.get("acceptance_check") else "")
            )
        parts = [f"Task: {handoff.title}", f"Problem:\n{brief.get('problem', '')}"]
        if brief.get("evidence"):
            parts.append("Evidence:\n" + "\n".join(f"- {e}" for e in brief["evidence"]))
        if brief.get("suspected_files"):
            parts.append("Start by looking at:\n" + "\n".join(f"- {f}" for f in brief["suspected_files"]))
        if brief.get("acceptance_check"):
            parts.append(f"Done when: {brief['acceptance_check']}")
        if requests:
            parts.append(f"Requested changes:\n{requests}")
        return "\n\n".join(parts)

    async def _push(
        self,
        session: AsyncSession,
        handoff: Handoff,
        workspace: handoff_git.Workspace,
        auth: HandoffGitHubAuth,
        github: HandoffGitHub,
        result: dict[str, str] | None,
        final_text: str | None,
        is_fix: bool,
        failing: list[str] | None = None,
    ) -> None:
        new_pr = not handoff.pr_number
        source_status = "fixing" if is_fix else "running"
        if not await move(
            session,
            handoff.id,
            "pushing",
            from_statuses=(source_status,),
            source="executor",
            message="Checking changes and pushing",
        ):
            return
        repo = await session.get(GitHubRepository, handoff.github_repository_id)
        config = HandoffRepoConfig.from_db(repo.handoff_config if repo else None)
        fail_status = "ci_failed" if is_fix else "failed"

        await handoff_git.sync_work_into_repo(workspace)
        changes = await handoff_git.staged_changes(workspace.repo)
        if not changes:
            explanation = ((result or {}).get("summary") or final_text or "").strip()[:800]
            if handoff.pr_number and not is_fix:
                message = f"No new changes were needed; PR #{handoff.pr_number} already covers this request."
                await move(
                    session,
                    handoff.id,
                    "done",
                    from_statuses=("pushing",),
                    source="executor",
                    message=f"{message} {explanation}".strip(),
                    next_check_at=utcnow() + REVIEW_POLL_INTERVAL,
                )
                return
            message = "Claude couldn't produce a CI fix." if is_fix else "Claude made no changes."
            await move(session, handoff.id, fail_status, from_statuses=("pushing",), message=message, error=message)
            return
        blocked = disallowed_paths([c.path for c in changes], config.paths)
        if blocked:
            message = "Changes outside the allowed paths were blocked: " + ", ".join(blocked[:10])
            await move(session, handoff.id, fail_status, from_statuses=("pushing",), message=message, error=message)
            return
        findings = scan_added_lines(await handoff_git.staged_added_lines(workspace.repo))
        if findings:
            where = ", ".join(f"{f.path}:{f.line} ({f.kind})" for f in findings[:5])
            message = f"Possible secrets found, nothing was pushed: {where}"
            await move(session, handoff.id, fail_status, from_statuses=("pushing",), message=message, error=message)
            return

        title = (result or {}).get("title") or handoff.title
        if is_fix:
            title = (
                f"Fix CI: {', '.join(failing or []) or 'failing checks'} (round {handoff.ci_fix_round}/{MAX_CI_ROUNDS})"
            )
        commit_message = f"{title}\n\nByaan handoff {handoff.id}"
        parent_sha = await handoff_git.head_sha(workspace.repo)
        branch_exists = bool(handoff.pr_number) or await handoff_git.remote_branch_exists(
            handoff.repo_full_name, handoff.branch, auth.token
        )
        new_sha = None
        if auth.kind == "app":
            new_sha = await create_signed_commit(
                token=auth.token,
                repo_full_name=handoff.repo_full_name,
                branch=handoff.branch,
                parent_sha=parent_sha,
                worktree=workspace.repo,
                changes=changes,
                message=commit_message,
                branch_exists=branch_exists,
            )
            if new_sha:
                await handoff_git.reset_to_remote(workspace.repo, handoff.repo_full_name, handoff.branch, auth.token)
        if not new_sha:
            author_name = auth.app.author_name if auth.app else "Byaan Bot"
            author_email = auth.app.author_email if auth.app else BOT_EMAIL
            new_sha = await handoff_git.commit(workspace.repo, commit_message, author_name, author_email)
            await handoff_git.push(workspace.repo, handoff.repo_full_name, handoff.branch, auth.token)

        now = utcnow()
        await HandoffRepository(session).update_fields(handoff.id, head_sha=new_sha, pushed_at=now)
        stats = {
            "files": len(changes),
            "added": sum(c.added for c in changes),
            "removed": sum(c.removed for c in changes),
        }
        fields: dict[str, Any] = {"error": None}

        if not handoff.pr_number:
            activity = activity_summary(await HandoffEventRepository(session).recent(handoff.id, 2000))
            body = self._pr_body(handoff, result, final_text, stats, await self._attribution(session, handoff))
            if activity["lines"]:
                body += (
                    "\n\n<details><summary>Activity recorded by Byaan</summary>\n\n"
                    + "\n".join(f"- {line}" for line in activity["lines"])
                    + "\n</details>"
                )
            pull = await github.find_open_pull(handoff.repo_full_name, handoff.branch)
            if not pull:
                pull = await github.create_pull(
                    handoff.repo_full_name,
                    head=handoff.branch,
                    base=handoff.base_branch,
                    title=pr_title(handoff),
                    body=body,
                )
            fields.update(pr_number=pull["number"], pr_url=pull["html_url"])
            labels = ["byaan", "untested"] if handoff.untested else ["byaan"]
            try:
                await github.add_labels(handoff.repo_full_name, pull["number"], labels)
            except GitHubError as exc:
                logger.warning(f"[HANDOFF] Could not label PR for {handoff.id}: {exc}")

        await HandoffRepository(session).update_fields(handoff.id, **fields)
        data = {
            "stats": stats,
            "pr_opened": new_pr,
            "pr_updated": not new_pr and not is_fix,
            **{k: v for k, v in (result or {}).items() if k in ("summary", "risks", "tests")},
        }
        if is_fix:
            pushed = "Fix pushed"
        elif new_pr:
            pushed = "Pull request opened"
        else:
            pushed = f"Pushed the requested changes to PR #{handoff.pr_number}"
        if handoff.untested:
            await move(
                session,
                handoff.id,
                "done",
                from_statuses=("pushing",),
                source="executor",
                message=f"{pushed} (UNTESTED: no CI on this repository)",
                data=data,
                next_check_at=now + REVIEW_POLL_INTERVAL,
            )
        else:
            await move(
                session,
                handoff.id,
                "ci_running",
                from_statuses=("pushing",),
                source="executor",
                message=f"{pushed}, CI running" if not is_fix else "Fix pushed, CI running again",
                data=data,
                next_check_at=now,
            )

    async def _attribution(self, session: AsyncSession, handoff: Handoff) -> str:
        from server.models.schedules import Schedule
        from server.models.user import User

        parts = []
        if handoff.requested_by:
            user = await session.get(User, handoff.requested_by)
            if user:
                parts.append(f"Requested by {user.full_name or user.email}")
        parts.append("from a Slack thread" if handoff.origin == "slack" else "from a Byaan notebook")
        if handoff.schedule_id:
            schedule = await session.get(Schedule, handoff.schedule_id)
            if schedule:
                parts.append(f'proposed by Monitor "{schedule.name}"')
        return ", ".join(parts)

    def _pr_body(
        self,
        handoff: Handoff,
        result: dict[str, str] | None,
        final_text: str | None,
        stats: dict[str, int],
        attribution: str,
    ) -> str:
        lines = []
        if handoff.untested:
            lines.append("> **UNTESTED**: this repository has no CI. Please test these changes before merging.\n")
        if result and result.get("summary"):
            lines.append(f"## Summary\n{result['summary']}")
            if result.get("tests"):
                lines.append(f"## Tests\n{result['tests']}")
            if result.get("risks"):
                lines.append(f"## Risks and assumptions\n{result['risks']}")
            if result.get("steps"):
                lines.append(
                    "## Steps Claude took\n" + "\n".join(f"{i}. {step}" for i, step in enumerate(result["steps"], 1))
                )
        else:
            lines.append(
                "## Summary (written by Byaan; Claude did not submit a summary)\n"
                f"{(final_text or handoff.brief.get('problem', ''))[:3000]}"
            )
        lines.append(f"## Task\n{handoff.brief.get('problem', '')[:3000]}")
        lines.append(
            f"---\n{attribution}.\n\nOpened by Byaan handoff `{handoff.id}` · {stats['files']} files, +{stats['added']} −{stats['removed']}. "
            "Review carefully; Byaan never merges."
        )
        return "\n\n".join(lines)

    @staticmethod
    async def _stop_watching_ci(session: AsyncSession, handoff: Handoff, reason: str) -> None:
        message = f"{reason}, so Byaan stopped watching CI"
        await move(
            session,
            handoff.id,
            "ci_failed",
            from_statuses=("ci_running",),
            message=message,
            error=message,
            next_check_at=None,
        )

    async def _poll_ci(self) -> None:
        async with AsyncSessionFactory() as session:
            claimed = await HandoffRepository(session).claim_poll(("ci_running",), CI_POLL_INTERVAL)
        for handoff_id in claimed:
            try:
                async with AsyncSessionFactory() as session:
                    await self._check_ci(session, handoff_id)
            except Exception as exc:
                logger.error(f"[HANDOFF] CI check failed for {handoff_id}: {exc}", exc_info=True)

    async def _check_ci(self, session: AsyncSession, handoff_id: UUID) -> None:
        repo_store = HandoffRepository(session)
        handoff = await repo_store.get_unscoped(handoff_id)
        if not handoff or handoff.status != "ci_running":
            return
        set_tenant_id(handoff.tenant_id)
        overdue = utcnow() - (handoff.pushed_at or utcnow()) > CI_PENDING_LIMIT
        auth = await _auth_for(session, handoff)
        if not auth:
            if overdue:
                await self._stop_watching_ci(session, handoff, "No GitHub credentials are available anymore")
            return
        github = HandoffGitHub(auth.token)
        try:
            pull = await github.get_pull(handoff.repo_full_name, handoff.pr_number)
        except GitHubError as exc:
            if overdue:
                await self._stop_watching_ci(
                    session, handoff, f"GitHub keeps rejecting the pull request lookup ({exc})"
                )
                return
            raise
        if pull.get("state") != "open":
            verb = "merged" if pull.get("merged") else "closed"
            await move(
                session, handoff.id, "done", from_statuses=("ci_running",), message=f"PR was {verb}", next_check_at=None
            )
            return
        pr_head = pull.get("head", {}).get("sha")
        if pr_head != handoff.head_sha:
            if await github.compare_status(handoff.repo_full_name, handoff.head_sha, pr_head) == "behind":
                return
            await move(
                session,
                handoff.id,
                "done",
                from_statuses=("ci_running",),
                message="Someone else pushed to the branch, so Byaan stopped fixing CI",
                next_check_at=utcnow() + REVIEW_POLL_INTERVAL,
            )
            return

        summary = summarize_checks(
            await github.check_runs(handoff.repo_full_name, handoff.head_sha),
            await github.combined_status(handoff.repo_full_name, handoff.head_sha),
        )
        since_push = utcnow() - (handoff.pushed_at or utcnow())
        pending_followups = await HandoffMessageRepository(session).pending(handoff.id, "followup")

        if summary["state"] == "none":
            if since_push < NO_CHECKS_GRACE:
                return
            await repo_store.update_fields(handoff.id, untested=True)
            handoff = await repo_store.refresh(handoff)
            await sync_pr_title(session, handoff)
            try:
                await github.add_labels(handoff.repo_full_name, handoff.pr_number, ["untested"])
            except GitHubError:
                pass
            await self._finish_ci(
                session, handoff, "No CI checks ran on the pull request, so it's marked UNTESTED", pending_followups
            )
            return
        if summary["state"] == "pending":
            if since_push > CI_PENDING_LIMIT:
                await move(
                    session,
                    handoff.id,
                    "ci_failed",
                    from_statuses=("ci_running",),
                    message="CI didn't finish within 60 minutes",
                    next_check_at=utcnow() + REVIEW_POLL_INTERVAL,
                )
            return
        await record(
            session,
            handoff.id,
            "github",
            "ci",
            f"CI {summary['state']}",
            {k: summary[k] for k in ("state", "pending", "passing")}
            | {"failing": [f["name"] for f in summary["failing"]]},
            notify=False,
        )
        if summary["state"] == "success":
            await self._finish_ci(session, handoff, "CI passed", pending_followups)
            return

        failing = summary["failing"]
        names = ", ".join(f["name"] for f in failing[:5])
        if all(f.get("conclusion") == "cancelled" for f in failing):
            await move(
                session,
                handoff.id,
                "ci_failed",
                from_statuses=("ci_running",),
                message=f"CI was cancelled ({names}). Re-run it on GitHub, then ask Byaan for changes if needed.",
                next_check_at=utcnow() + REVIEW_POLL_INTERVAL,
            )
            return
        if handoff.ci_fix_round >= MAX_CI_ROUNDS:
            await move(
                session,
                handoff.id,
                "ci_failed",
                from_statuses=("ci_running",),
                message=f"CI still failing after {MAX_CI_ROUNDS} attempts ({names}). The PR stays open for a person.",
                next_check_at=utcnow() + REVIEW_POLL_INTERVAL,
            )
            return
        if self.free_slots <= 0:
            await repo_store.update_fields(handoff.id, next_check_at=utcnow() + timedelta(seconds=15))
            return
        context = await self._failure_context(github, handoff, failing)
        moved = await move(
            session,
            handoff.id,
            "fixing",
            from_statuses=("ci_running",),
            source="github",
            message=f"CI failed ({names}). Sending it back to Claude, round {handoff.ci_fix_round + 1}/{MAX_CI_ROUNDS}",
            data={"failing": [f["name"] for f in failing], "log_excerpt": context[-3000:]},
            ci_fix_round=handoff.ci_fix_round + 1,
            worker_id=self.worker_id,
            heartbeat_at=utcnow(),
        )
        if moved:
            self._spawn(
                handoff.id,
                RunPlan(mode="fix", failure_context=context, extra={"failing": [f["name"] for f in failing]}),
            )
        else:
            await repo_store.update_fields(handoff.id, next_check_at=utcnow() + timedelta(seconds=15))

    async def _finish_ci(self, session: AsyncSession, handoff: Handoff, message: str, pending_followups: list) -> None:
        if pending_followups:
            await move(
                session,
                handoff.id,
                "done",
                from_statuses=("ci_running",),
                message=message,
                notify=False,
            )
            await move(
                session,
                handoff.id,
                "queued",
                from_statuses=("done",),
                message=f"{message}. Applying the follow-up requests next",
                ci_fix_round=0,
                next_check_at=None,
                finished_at=None,
            )
            return
        await move(
            session,
            handoff.id,
            "done",
            from_statuses=("ci_running",),
            message=message,
            next_check_at=utcnow() + REVIEW_POLL_INTERVAL,
        )

    async def _current_ci_failure(self, github: HandoffGitHub, handoff: Handoff) -> str:
        try:
            summary = summarize_checks(
                await github.check_runs(handoff.repo_full_name, handoff.head_sha),
                await github.combined_status(handoff.repo_full_name, handoff.head_sha),
            )
        except GitHubError as exc:
            logger.warning(f"[HANDOFF] Could not read CI for {handoff.id}: {exc}")
            return ""
        if summary["state"] != "failure":
            return ""
        return await self._failure_context(github, handoff, summary["failing"])

    async def _failure_context(self, github: HandoffGitHub, handoff: Handoff, failing: list[dict[str, Any]]) -> str:
        chunks: list[str] = []
        budget = FAILURE_LOG_BUDGET
        for check in failing:
            text = None
            if check.get("is_actions") and check.get("id"):
                try:
                    job_id = int(check["id"])
                    text = await github.job_log_failure(handoff.repo_full_name, job_id)
                except (GitHubError, ValueError):
                    text = None
            if not text:
                output = check.get("output") or {}
                text = "\n".join(str(output.get(k) or "") for k in ("title", "summary", "text")).strip()
            chunk = f"### {check['name']}\n{text or 'No log available'}"
            if len(chunk) > budget:
                chunk = chunk[-budget:]
            chunks.append(chunk)
            budget -= len(chunk)
            if budget <= 0:
                break
        return "\n\n".join(chunks)

    async def _poll_reviews(self) -> None:
        async with AsyncSessionFactory() as session:
            claimed = await HandoffRepository(session).claim_poll(("done", "ci_failed"), REVIEW_POLL_INTERVAL)
        for handoff_id in claimed:
            try:
                async with AsyncSessionFactory() as session:
                    await self._check_reviews(session, handoff_id)
            except Exception as exc:
                logger.error(f"[HANDOFF] Review check failed for {handoff_id}: {exc}", exc_info=True)

    async def _check_reviews(self, session: AsyncSession, handoff_id: UUID) -> None:
        repo_store = HandoffRepository(session)
        handoff = await repo_store.get_unscoped(handoff_id)
        if not handoff or not handoff.pr_number or handoff.status not in ("done", "ci_failed"):
            return
        if handoff.pushed_at and utcnow() - handoff.pushed_at > REVIEW_WINDOW:
            await repo_store.update_fields(handoff.id, next_check_at=None)
            return
        set_tenant_id(handoff.tenant_id)
        auth = await _auth_for(session, handoff)
        if not auth:
            return
        github = HandoffGitHub(auth.token)
        pull = await github.get_pull(handoff.repo_full_name, handoff.pr_number)
        if pull.get("state") != "open":
            await repo_store.update_fields(handoff.id, next_check_at=None)
            verb = "merged" if pull.get("merged") else "closed"
            await record(session, handoff.id, "github", "status", f"PR was {verb}", {"pr_state": verb}, notify=False)
            return
        feedback = [
            f for f in await github.review_feedback(handoff.repo_full_name, handoff.pr_number) if not f["is_bot"]
        ]
        previous = await HandoffEventRepository(session).latest_of_kind(handoff.id, "review")
        seen = set((previous.data or {}).get("seen_ids", [])) if previous else set()
        new = [f for f in feedback if f["id"] not in seen]
        if not new:
            return
        await record(
            session,
            handoff.id,
            "github",
            "review",
            "\n\n".join(f"{f['author']}: {f['body'][:1000]}" for f in new)[:4000],
            {
                "seen_ids": sorted(seen | {f["id"] for f in new}),
                "new_ids": [f["id"] for f in new],
                "comments": new[:20],
            },
        )

    async def _refresh_indicators(self) -> None:
        """Slack drops a thread status after about two minutes, so re-assert it while handoffs work."""
        if time.monotonic() - self._last_indicator_refresh < INDICATOR_REFRESH_SECONDS:
            return
        self._last_indicator_refresh = time.monotonic()
        from server.services.handoff_notifier import refresh_thread_indicators

        async with AsyncSessionFactory() as session:
            await refresh_thread_indicators(session)

    async def _cleanup(self) -> None:
        if time.monotonic() - self._last_cleanup < CLEANUP_EVERY_SECONDS:
            return
        self._last_cleanup = time.monotonic()
        async with AsyncSessionFactory() as session:
            repo_store = HandoffRepository(session)
            for handoff in await repo_store.expired_workspaces(utcnow() - WORKSPACE_TTL):
                if await self._remove(handoff_git.Workspace.for_handoff(handoff.id).root):
                    await repo_store.update_fields(handoff.id, workspace_path=None, claude_session_id=None)
            referenced = await repo_store.referenced_workspace_paths()
            active = {str(h) for h in self._runs}
        cutoff = time.time() - ORPHAN_GRACE_SECONDS
        for entry in handoffs_root().iterdir():
            if not entry.is_dir() or entry.name.startswith(".") or entry.name in active or str(entry) in referenced:
                continue
            try:
                UUID(entry.name)
            except ValueError:
                continue
            if entry.stat().st_mtime < cutoff:
                await self._remove(entry)

    @staticmethod
    async def _remove(root: Path) -> bool:
        try:
            await asyncio.to_thread(handoff_git.remove_workspace, root)
            return True
        except Exception as exc:
            logger.error(f"[HANDOFF] Could not remove workspace {root}: {exc}")
            return False


handoff_runner = HandoffRunner()


async def requirements(session: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    from server.services.claude_oauth_service import get_auth_method
    from server.services.github_app_service import app_status
    from server.services.handoff_agent import resolve_cli

    try:
        _, isolated = resolve_cli()
        cli_ok, cli_error = True, None
    except AgentUnavailable as exc:
        isolated, cli_ok, cli_error = False, False, str(exc)
    return {
        "git": bool(shutil.which("git")),
        "claude_cli": cli_ok,
        "claude_cli_error": cli_error,
        "claude_auth": get_auth_method() is not None,
        "os_isolation": isolated,
        "github_app": await app_status(tenant_id, session),
        "max_concurrent": max_concurrent(),
    }


async def test_setup(session: AsyncSession, repo: GitHubRepository, requester_id: UUID) -> dict[str, Any]:
    """Dry run for a repository: credentials, clone, CI detection and branch rules, without running Claude."""
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("git installed", bool(shutil.which("git")))
    auth = await resolve_auth(
        session,
        tenant_id=repo.tenant_id,
        repo_full_name=repo.repo_full_name,
        requester_id=requester_id,
        origin="notebook",
    )
    if not auth:
        add("GitHub credentials", False, "Connect GitHub first")
        return {"ok": False, "checks": checks}
    add("GitHub credentials", True, "GitHub App" if auth.kind == "app" else f"{auth.kind} token")
    github = HandoffGitHub(auth.token)
    try:
        add("Push access", await can_push(auth, github, repo.repo_full_name))
    except GitHubError as exc:
        add("Push access", False, str(exc))
        return {"ok": False, "checks": checks}

    probe_id = uuid4()
    try:
        workspace = await handoff_git.create_workspace(probe_id, repo.repo_full_name, repo.effective_branch, auth.token)
        sha = await handoff_git.head_sha(workspace.repo)
        add("Clone", True, f"{repo.effective_branch} @ {sha[:8]}")
        features = handoff_git.detect_repo_features(workspace.repo)
        if features["lfs"]:
            add("Git LFS", False, "LFS files are not downloaded; Claude can't edit them")
        if features["submodules"]:
            add("Submodules", False, "Submodules are not cloned")
        has_ci = await github.has_ci(repo.repo_full_name, sha)
        add("CI detected", has_ci, "" if has_ci else "PRs will be marked UNTESTED")
        signed = await github.requires_signed_commits(repo.repo_full_name, repo.effective_branch)
        if signed and auth.kind != "app":
            add("Signed commits", False, "Branch requires signed commits; connect the Byaan GitHub App")
    except handoff_git.GitError as exc:
        add("Clone", False, str(exc))
    finally:
        await asyncio.to_thread(handoff_git.remove_workspace, handoff_git.Workspace.for_handoff(probe_id).root)
    blocking = {"git installed", "GitHub credentials", "Push access", "Clone"}
    return {"ok": all(c["ok"] for c in checks if c["name"] in blocking), "checks": checks}
