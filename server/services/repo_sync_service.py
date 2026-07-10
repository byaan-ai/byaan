from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.tenant_context import set_tenant_id
from server.models.custom_skill import CustomSkill
from server.models.github_repository import GitHubRepository
from server.models.notebooks import Notebook
from server.models.skill_citation import SkillCitation
from server.prompts.skill_loop_prompts import (
    build_code_newfact_prompt,
    build_code_reverify_prompt,
    parse_last_json_block,
)
from server.repositories.skill_citation import SkillCitationRepository
from server.repositories.skill_loop_settings import SkillLoopSettingsRepository
from server.services import github_service
from server.services.conversation_evaluation_service import SYSTEM_NOTEBOOK_NAME, skill_loop_service
from server.services.skill_suggestion_service import SkillSuggestionService
from server.utils.config_loader import get_skill_loop_config
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

IGNORED_DIRS = (
    "node_modules/",
    "vendor/",
    "dist/",
    "build/",
    ".git/",
    "__pycache__/",
    ".venv/",
    "venv/",
)
IGNORED_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".webp",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp3",
    ".mp4",
    ".wav",
    ".mov",
    ".zip",
    ".tar",
    ".gz",
    ".pdf",
    ".lock",
    ".map",
)
IGNORED_BASENAMES = (
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "cargo.lock",
    "uv.lock",
    "composer.lock",
)
HIGH_VALUE_MARKERS = ("models/", "migrations/", "schemas/", "config", "constants")
MAX_REPOS_PER_TICK = 25


def _is_ignorable_path(path: str) -> bool:
    if not path:
        return True
    lower = path.lower()
    if any(d in lower for d in IGNORED_DIRS):
        return True
    if lower.endswith(IGNORED_EXTENSIONS):
        return True
    basename = lower.rsplit("/", 1)[-1]
    if basename in IGNORED_BASENAMES:
        return True
    if ".min." in basename:
        return True
    return False


def _is_high_value_path(path: str) -> bool:
    lower = path.lower()
    return any(marker in lower for marker in HIGH_VALUE_MARKERS)


def _normalize(text: str) -> str:
    return " ".join(text.split())


class RepoSyncService:
    """Background loop that syncs skill citations against new commits and proposes drift fixes."""

    def __init__(self, evaluator=skill_loop_service) -> None:
        self._evaluator = evaluator
        self._session_date: date | None = None
        self._sessions_used: int = 0

    def _take_session_budget(self, per_day: int) -> bool:
        today = datetime.now().date()
        if self._session_date != today:
            self._session_date = today
            self._sessions_used = 0
        if self._sessions_used >= per_day:
            return False
        self._sessions_used += 1
        return True

    async def _get_system_notebook(self, session: AsyncSession, tenant_id: UUID) -> Notebook:
        result = await session.execute(
            select(Notebook).where(Notebook.tenant_id == tenant_id, Notebook.notebook_name == SYSTEM_NOTEBOOK_NAME)
        )
        notebook = result.scalars().first()
        if notebook:
            return notebook
        notebook = Notebook(tenant_id=tenant_id, notebook_name=SYSTEM_NOTEBOOK_NAME)
        session.add(notebook)
        await session.commit()
        await session.refresh(notebook)
        return notebook

    async def _run_agent(self, session: AsyncSession, tenant_id: UUID, instruction: str) -> str:
        notebook = await self._get_system_notebook(session, tenant_id)
        return await self._evaluator._run_agent(session, notebook, instruction, tenant_id)

    async def _notify(self, session: AsyncSession, suggestion) -> None:
        await self._evaluator._notify(session, suggestion)

    async def _resolve_token(self, session: AsyncSession, repo: GitHubRepository) -> str | None:
        token = await github_service.get_github_token(repo.tenant_id, repo.user_id, session)
        if token:
            return token
        return await github_service.get_org_github_token(repo.tenant_id, session)

    async def tick(self, session: AsyncSession) -> None:
        cfg = get_skill_loop_config()
        if not cfg["code_sync_enabled"]:
            return

        repos = await self._eligible_repos(session)
        if not repos:
            return

        if len(repos) > MAX_REPOS_PER_TICK:
            logger.info(f"Repo sync: {len(repos)} repos eligible, processing first {MAX_REPOS_PER_TICK} this tick")
            repos = repos[:MAX_REPOS_PER_TICK]

        settings_repo = SkillLoopSettingsRepository(session)
        enabled_by_tenant: dict[UUID, bool] = {}

        logger.info(f"Repo sync evaluating {len(repos)} repo(s)")
        for repo in repos:
            enabled = enabled_by_tenant.get(repo.tenant_id)
            if enabled is None:
                settings = await settings_repo.get_or_defaults(repo.tenant_id)
                enabled = bool(settings.enabled)
                enabled_by_tenant[repo.tenant_id] = enabled
            if not enabled:
                continue
            try:
                await self._sync_repo(session, repo, cfg)
            except Exception as e:
                logger.error(f"Repo sync failed for {repo.repo_full_name}: {e}", exc_info=True)
                await session.rollback()

    async def _eligible_repos(self, session: AsyncSession) -> list[GitHubRepository]:
        query = select(GitHubRepository).where(
            GitHubRepository.source == "github",
            GitHubRepository.analysis_status == "completed",
            GitHubRepository.skill_sync_enabled.is_(True),
            GitHubRepository.last_analyzed_sha.is_not(None),
            GitHubRepository.is_active.is_(True),
        )
        result = await session.execute(query)
        return list(result.scalars().all())

    async def _sync_repo(self, session: AsyncSession, repo: GitHubRepository, cfg: dict) -> None:
        set_tenant_id(repo.tenant_id)
        token = await self._resolve_token(session, repo)
        if not token:
            logger.warning(f"No GitHub token for {repo.repo_full_name}; skipping sync")
            return

        owner, name = repo.repo_full_name.split("/", 1)
        base_sha = repo.last_analyzed_sha
        head = await github_service.get_latest_commit_sha(token, owner, name, repo.effective_branch)
        if head == base_sha:
            return

        compare = await github_service.compare_commits(token, repo.repo_full_name, base_sha, head)
        if compare is None:
            logger.info(f"Compare unavailable for {repo.repo_full_name} {base_sha[:8]}...{head[:8]}; advancing cursor")
            repo.last_analyzed_sha = head
            await session.commit()
            return

        changed = [f for f in compare["files"] if f.get("filename") and not _is_ignorable_path(f["filename"])]
        patch_by_path = {f["filename"]: f for f in changed}
        changed_paths = list(patch_by_path.keys())

        citations = await SkillCitationRepository(session).list_for_repo_paths(repo.id, changed_paths)
        await self._reresolve_citations(session, repo, owner, name, head, token, citations, patch_by_path)

        compare_url = compare.get("html_url", "")
        file_names = changed_paths[:20]

        await self._reverify_unresolved(
            session, repo, cfg, citations, patch_by_path, base_sha, head, compare_url, file_names
        )
        await self._probe_new_fact(session, repo, cfg, changed, citations, base_sha, head, compare_url, file_names)

        repo.last_analyzed_sha = head
        await session.commit()

    async def _reresolve_citations(
        self,
        session: AsyncSession,
        repo: GitHubRepository,
        owner: str,
        name: str,
        head: str,
        token: str,
        citations: list[SkillCitation],
        patch_by_path: dict[str, dict],
    ) -> None:
        content_cache: dict[str, str | None] = {}
        for citation in citations:
            path = citation.path
            file_change = patch_by_path.get(path)
            removed = bool(file_change and file_change.get("status") == "removed")
            if removed:
                content = None
            elif path in content_cache:
                content = content_cache[path]
            else:
                content = await github_service.get_file_content(token, owner, name, path, ref=head)
                content_cache[path] = content

            status = self._reresolve(citation, content)
            citation.status = status
        await session.commit()

    def _reresolve(self, citation: SkillCitation, content: str | None) -> str:
        snippet = citation.snippet or ""
        snippet_lines = snippet.splitlines()
        if content is None or not snippet_lines:
            return "unresolved"

        target = _normalize(snippet)
        lines = content.splitlines()
        span = len(snippet_lines)
        for i in range(0, len(lines) - span + 1):
            window = "\n".join(lines[i : i + span])
            if _normalize(window) == target:
                start = i + 1
                end = i + span
                if citation.start_line == start and citation.end_line == end:
                    return "valid"
                citation.start_line = start
                citation.end_line = end
                return "valid"
        return "unresolved"

    async def _reverify_unresolved(
        self,
        session: AsyncSession,
        repo: GitHubRepository,
        cfg: dict,
        citations: list[SkillCitation],
        patch_by_path: dict[str, dict],
        base_sha: str,
        head: str,
        compare_url: str,
        file_names: list[str],
    ) -> None:
        skill_ids: list[UUID] = []
        for citation in citations:
            if citation.status == "unresolved" and citation.skill_id not in skill_ids:
                skill_ids.append(citation.skill_id)

        processed = 0
        for skill_id in skill_ids:
            if processed >= cfg["code_max_skills_per_tick"]:
                break
            if not self._take_session_budget(cfg["code_sessions_per_day"]):
                break
            processed += 1
            await self._reverify_skill(
                session, repo, skill_id, citations, patch_by_path, base_sha, head, compare_url, file_names
            )

    async def _reverify_skill(
        self,
        session: AsyncSession,
        repo: GitHubRepository,
        skill_id: UUID,
        citations: list[SkillCitation],
        patch_by_path: dict[str, dict],
        base_sha: str,
        head: str,
        compare_url: str,
        file_names: list[str],
    ) -> None:
        skill = await session.get(CustomSkill, skill_id)
        if not skill:
            return

        affected = [c for c in citations if c.skill_id == skill_id]
        citations_payload = [
            {
                "claim_key": c.claim_key,
                "path": c.path,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "snippet": c.snippet,
                "status": c.status,
            }
            for c in affected
        ]
        patches = [patch_by_path[c.path] for c in affected if c.path in patch_by_path]
        patches = list({p["filename"]: p for p in patches}.values())

        prompt = build_code_reverify_prompt(skill.name, skill.instructions, citations_payload, patches)
        text = await self._run_agent(session, repo.tenant_id, prompt)
        data = parse_last_json_block(text)
        if not data:
            return

        edit = data.get("edit")
        if data.get("still_valid") or not edit:
            return

        summary = data.get("summary") or ""
        source = self._build_source(repo, base_sha, head, compare_url, file_names)
        evidence = {
            "citations": [{"claim_key": c.claim_key, "path": c.path, "status": c.status} for c in affected],
            "summary": summary,
        }
        patch = {"section": edit.get("section"), "before": edit.get("before"), "after": edit.get("after")}

        service = SkillSuggestionService(session)
        suggestion = await service.create_suggestion(
            tenant_id=repo.tenant_id,
            suggestion_type="edit",
            title=f"Code drift: {skill.name}"[:300],
            rationale=summary or "A cited code claim changed and no longer matches the skill.",
            confidence=data.get("confidence") or "low",
            skill_id=skill_id,
            evidence=evidence,
            patch=patch,
            proposed_instructions=edit.get("proposed_instructions"),
            source=source,
        )
        await self._notify(session, suggestion)

    async def _probe_new_fact(
        self,
        session: AsyncSession,
        repo: GitHubRepository,
        cfg: dict,
        changed: list[dict],
        citations: list[SkillCitation],
        base_sha: str,
        head: str,
        compare_url: str,
        file_names: list[str],
    ) -> None:
        cited_paths = {c.path for c in citations}
        high_value = [
            f
            for f in changed
            if _is_high_value_path(f["filename"]) and f["filename"] not in cited_paths and f.get("patch")
        ]
        if not high_value:
            return
        if not self._take_session_budget(cfg["code_sessions_per_day"]):
            return

        prompt = build_code_newfact_prompt(high_value)
        text = await self._run_agent(session, repo.tenant_id, prompt)
        data = parse_last_json_block(text)
        if not data or not data.get("worth_new_fact"):
            return

        source = self._build_source(repo, base_sha, head, compare_url, file_names)
        service = SkillSuggestionService(session)
        suggestion = await service.create_suggestion(
            tenant_id=repo.tenant_id,
            suggestion_type="new_skill",
            title=(data.get("title") or "New codebase fact")[:300],
            rationale=data.get("rationale") or "",
            confidence=data.get("confidence") or "low",
            skill_id=None,
            evidence={"summary": data.get("rationale")},
            proposed_instructions=data.get("proposed_instructions"),
            source=source,
        )
        await self._notify(session, suggestion)

    def _build_source(
        self,
        repo: GitHubRepository,
        base_sha: str,
        head: str,
        compare_url: str,
        file_names: list[str],
    ) -> dict:
        return {
            "origin": "codebase",
            "repo_id": str(repo.id),
            "repo_full_name": repo.repo_full_name,
            "base_sha": base_sha,
            "head_sha": head,
            "compare_url": compare_url,
            "files": file_names,
        }


repo_sync_service = RepoSyncService()
