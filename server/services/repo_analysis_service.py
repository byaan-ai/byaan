from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from server.db.session import AsyncSessionFactory
from server.prompts.repo_fact_prompts import (
    FACT_EXTRACTION_SYSTEM,
    FACT_FAMILIES,
    build_fact_extraction_prompt,
    parse_last_json_array,
)
from server.repositories.custom_skill import CustomSkillRepository
from server.repositories.github_repository import GitHubRepoRepository
from server.repositories.skill_citation import SkillCitationRepository
from server.services import github_service
from server.services.completion_service import CompletionError, CompletionService
from server.services.unified_agent import is_using_claude_code_auth
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)


@dataclass
class AnalysisProgress:
    message: str = ""
    step: int = 0
    total_steps: int = 5
    files_analyzed: int = 0
    total_files: int = 0


_analysis_progress: dict[str, AnalysisProgress] = {}


def _update_progress(
    repo_id: str,
    message: str,
    step: int,
    files_analyzed: int = 0,
    total_files: int = 0,
) -> None:
    _analysis_progress[repo_id] = AnalysisProgress(
        message=message,
        step=step,
        total_steps=5,
        files_analyzed=files_analyzed,
        total_files=total_files,
    )


def _clear_progress(repo_id: str) -> None:
    _analysis_progress.pop(repo_id, None)


def get_analysis_progress(repo_id: str) -> AnalysisProgress | None:
    return _analysis_progress.get(repo_id)


SKIP_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp3",
    ".mp4",
    ".wav",
    ".avi",
    ".mov",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".7z",
    ".pyc",
    ".pyo",
    ".class",
    ".o",
    ".so",
    ".dll",
    ".exe",
    ".bin",
    ".dat",
    ".lock",
    ".min.js",
    ".min.css",
    ".map",
}

SKIP_DIRS = {"node_modules/", "vendor/", ".git/", "dist/", "build/", "__pycache__/", ".venv/", "venv/"}

MAX_FILES_PER_SKILL = 50
MAX_CHARS_PER_SKILL = 100_000

PRIORITY_FILES = [
    "README.md",
    "readme.md",
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
]

PRIORITY_PATTERNS = [
    "main.py",
    "index.ts",
    "index.js",
    "app.py",
    "manage.py",
    "src/main.",
    "src/app.",
    "src/index.",
]

CONFIG_PATTERNS = [
    "tsconfig.json",
    "ruff.toml",
    ".eslintrc",
    "webpack.config.",
    "vite.config.",
    "jest.config.",
    "pytest.ini",
    "setup.cfg",
    "setup.py",
]

DATA_PATTERNS = ["model", "schema", "migration", "prisma/schema.prisma", "alembic.ini"]

CI_PATTERNS = [".github/workflows/"]

TEST_PATTERNS = ["test", "spec", "__tests__"]


def _should_skip(path: str) -> bool:
    for d in SKIP_DIRS:
        if d in path:
            return True
    for ext in SKIP_EXTENSIONS:
        if path.endswith(ext):
            return True
    return False


def _score_file(path: str, focus_boost: list[str] | None = None) -> int:
    lower = path.lower()
    basename = path.rsplit("/", 1)[-1] if "/" in path else path

    if focus_boost:
        for pattern in focus_boost:
            if pattern.lower() in lower:
                return 95

    if basename in PRIORITY_FILES:
        return 100
    for p in PRIORITY_PATTERNS:
        if p in lower:
            return 90
    for p in CI_PATTERNS:
        if p in lower:
            return 80
    for p in CONFIG_PATTERNS:
        if p.lower() in lower:
            return 70
    for p in DATA_PATTERNS:
        if p in lower:
            return 60
    for p in TEST_PATTERNS:
        if p in lower:
            return 40
    return 10


def _get_blob_paths(tree: list[dict]) -> list[str]:
    return [item["path"] for item in tree if item.get("type") == "blob" and not _should_skip(item["path"])]


def _select_key_files(blob_paths: list[str], focus_boost: list[str] | None = None) -> list[str]:
    scored = sorted(blob_paths, key=lambda p: (-_score_file(p, focus_boost), p))
    return scored[:MAX_FILES_PER_SKILL]


SKILL_PROMPTS = {
    "codebase": {
        "name": "Codebase Overview",
        "system": (
            "You are writing a skill document that an AI agent will consume to answer user questions. "
            "Write structured markdown optimized for machine parsing, not human reading.\n\n"
            "Target length: 200-400 lines. Be dense and precise — no filler, no introductions, no conclusions.\n\n"
            "Start with:\n"
            "## When To Use This Skill\n"
            "List trigger terms: architecture, tech stack, project structure, conventions, "
            "build system, testing setup, entry points, directory layout, dependencies.\n\n"
            "Then cover:\n"
            "1. **Project Overview** — What it does, who it's for, core value prop (3-5 bullets)\n"
            "2. **Tech Stack** — Languages, frameworks, key deps with versions (bullet list)\n"
            "3. **Architecture** — Directory structure, how components connect (cite exact paths)\n"
            "4. **Key Entry Points** — Main files, startup flow, request lifecycle (cite paths)\n"
            "5. **Coding Conventions** — Naming, formatting, import style, error handling (with examples)\n"
            "6. **Testing** — Framework, structure, how to run tests (cite config files)\n"
            "7. **Build & Deploy** — Build tools, CI/CD, deployment config (cite paths)\n\n"
            "Rules:\n"
            "- Use bullet points, not paragraphs\n"
            "- Always cite exact file paths (e.g., `src/main.ts`)\n"
            "- No speculation — if information is missing, say so\n"
            "- No generic advice — only facts from the codebase"
        ),
        "user_prompt": (
            "Analyze the provided codebase files and produce a Codebase Overview skill document. "
            "Cite exact file paths for every claim. Use bullets, not paragraphs. "
            "Acknowledge gaps instead of speculating. Target 200-400 lines."
        ),
        "focus": ["README", "config", "entry", "main", "app", "index", "src/"],
    },
    "data_layer": {
        "name": "Data Layer",
        "system": (
            "You are writing a skill document that an AI agent will consume to answer questions about "
            "database models, schemas, migrations, and data access patterns. "
            "Write structured markdown optimized for machine parsing.\n\n"
            "Target length: 200-400 lines. Be dense and precise — no filler.\n\n"
            "Start with:\n"
            "## When To Use This Skill\n"
            "List trigger terms: database models, schemas, migrations, relationships, "
            "data access, ORM config, foreign keys, indexes, repositories, data flow.\n\n"
            "Then cover:\n"
            "1. **Database Models** — Use compact format per model:\n"
            "   `### ModelName (path/to/file.py)` → `Table: table_name` → `Fields:` bullet list\n"
            "2. **Relationships** — FK references, join tables, one-to-many / many-to-many (cite models)\n"
            "3. **Schemas & Serialization** — Request/response schemas, validation rules (cite files)\n"
            "4. **Data Access Layer** — Repository/DAO patterns, query patterns (cite files)\n"
            "5. **Migrations** — Strategy, tools, how schema changes are managed (cite config)\n"
            "6. **ORM Configuration** — Setup, session management, connection pooling (cite files)\n"
            "7. **Indexes & Constraints** — Indexes, unique constraints, check constraints\n\n"
            "Rules:\n"
            "- Use bullet points, not paragraphs\n"
            "- Always cite exact file paths\n"
            "- No speculation — if information is missing, say so\n"
            "- No generic advice — only facts from the codebase"
        ),
        "user_prompt": (
            "Analyze the provided codebase files and produce a Data Layer skill document. "
            "Cite exact file paths for every claim. Use the compact model format specified. "
            "Acknowledge gaps instead of speculating. Target 200-400 lines."
        ),
        "focus": ["model", "schema", "migration", "orm", "repository", "entity", "table", "database", "prisma"],
    },
}

DATA_TRUTHS_FOCUS = [
    "model",
    "models",
    "migration",
    "schema",
    "enum",
    "constant",
    "config",
    "settings",
    "entity",
    "prisma/schema.prisma",
]

_FAMILY_TITLES = {
    "enum": "Enums & Value Sets",
    "scope": "Tenancy & Scoping",
    "config": "Config-Driven Definitions",
    "launch": "Launch & Go-Live Constants",
    "semantics": "Field Semantics",
    "join": "Joins & Relationships",
    "provenance": "Data Provenance & Gotchas",
}

_MAX_SNIPPET_CHARS = 500


def _normalize_ws(text: str) -> str:
    return " ".join(text.split())


def _normalize_with_line_map(content: str) -> tuple[str, list[int]]:
    """Whitespace-normalized content plus a per-char map back to the 1-based source line."""
    norm: list[str] = []
    line_map: list[int] = []
    line = 1
    prev_ws = False
    for ch in content:
        if ch.isspace():
            if not prev_ws:
                norm.append(" ")
                line_map.append(line)
            prev_ws = True
            if ch == "\n":
                line += 1
        else:
            norm.append(ch)
            line_map.append(line)
            prev_ws = False
    return "".join(norm), line_map


def _ground_snippet(content: str, snippet: str) -> tuple[int, int] | None:
    """Verify snippet appears verbatim (whitespace-normalized) in content.

    Returns the recomputed (start_line, end_line) from the actual match position, or None when
    the snippet is not found — the anti-hallucination guard drops such claims.
    """
    norm_snippet = _normalize_ws(snippet)
    if not norm_snippet:
        return None
    norm_content, line_map = _normalize_with_line_map(content)
    idx = norm_content.find(norm_snippet)
    if idx == -1:
        return None
    end_idx = idx + len(norm_snippet) - 1
    return line_map[idx], line_map[end_idx]


def _render_data_truths_markdown(claims: list[dict]) -> str:
    lines = ["# Data Truths & Conventions", ""]
    for family, title in _FAMILY_TITLES.items():
        family_claims = [c for c in claims if c["family"] == family]
        if not family_claims:
            continue
        lines.append(f"## {title}")
        for c in family_claims:
            cite = f"{c['path']}:{c['start_line']}-{c['end_line']}"
            lines.append(f"- {c['claim']} — Query rule: {c['query_rule']} (cited: {cite})")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _ground_claims(claims: list[dict], file_contents: dict[str, str]) -> tuple[list[dict], int]:
    grounded: list[dict] = []
    dropped = 0
    for claim in claims:
        if not isinstance(claim, dict):
            dropped += 1
            continue
        family = claim.get("family")
        path = claim.get("path")
        snippet = claim.get("snippet")
        claim_text = claim.get("claim")
        query_rule = claim.get("query_rule")
        claim_key = claim.get("claim_key")
        if family not in FACT_FAMILIES or not path or not snippet or not claim_text or not query_rule:
            dropped += 1
            continue
        content = file_contents.get(path)
        if not content:
            dropped += 1
            continue
        lines = _ground_snippet(content, snippet)
        if lines is None:
            dropped += 1
            continue
        start_line, end_line = lines
        grounded.append(
            {
                "claim_key": (claim_key or "").strip() or None,
                "family": family,
                "claim": str(claim_text).strip(),
                "query_rule": str(query_rule).strip(),
                "path": path,
                "start_line": start_line,
                "end_line": end_line,
                "snippet": str(snippet)[:_MAX_SNIPPET_CHARS],
            }
        )
    return grounded, dropped


async def _run_data_truths_pass(
    repo_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
    llm_connection_id: str,
    use_claude_sdk: bool,
    repo_full_name: str,
    data_truths_paths: list[str],
    file_contents: dict[str, str],
    commit_sha: str,
) -> None:
    """Compile grounded code facts into a deterministic 'data_truths' skill plus citations.

    Failure-isolated: any problem logs and returns without raising, so the other passes and the
    overall analysis are unaffected. Keyed per repo, so it is multi-repo safe.
    """
    files = [(path, file_contents[path]) for path in data_truths_paths if path in file_contents]
    if not files:
        logger.info(f"[ANALYSIS] data_truths for {repo_full_name}: no file content, skipping")
        return

    prompt = build_fact_extraction_prompt(files)

    async with AsyncSessionFactory() as session:
        try:
            raw = await CompletionService.complete(
                prompt=prompt,
                llm_connection_id=llm_connection_id,
                session=session,
                system_prompt=FACT_EXTRACTION_SYSTEM,
                use_claude_sdk=use_claude_sdk,
            )
        except CompletionError as err:
            logger.error(f"[ANALYSIS] data_truths completion failed for {repo_full_name}: {err.reason} — {err.message}")
            return

        claims = parse_last_json_array(raw or "")
        if claims is None:
            logger.warning(f"[ANALYSIS] data_truths for {repo_full_name}: could not parse json array, skipping pass")
            return

        grounded, dropped = _ground_claims(claims, file_contents)
        if dropped:
            logger.info(f"[ANALYSIS] data_truths for {repo_full_name}: dropped {dropped} ungrounded/invalid claims")
        if not grounded:
            logger.info(f"[ANALYSIS] data_truths for {repo_full_name}: no grounded claims, skipping")
            return

        markdown = _render_data_truths_markdown(grounded)
        description = (
            f"Use for {repo_full_name} data truths & conventions: enum value sets, tenancy scoping, "
            f"config-driven definitions, launch constants, field semantics, join keys, data provenance."
        )[:500]

        skill_repo = CustomSkillRepository(session)
        skill = await skill_repo.upsert_github_skill(
            tenant_id=tenant_id,
            created_by=user_id,
            github_repo_id=repo_id,
            github_analysis_type="data_truths",
            name="Data Truths & Conventions",
            description=description,
            instructions=markdown,
        )

        citations = [
            {
                "repo_id": repo_id,
                "path": claim["path"],
                "start_line": claim["start_line"],
                "end_line": claim["end_line"],
                "blob_sha": None,
                "commit_sha": commit_sha,
                "snippet_hash": hashlib.sha256(_normalize_ws(claim["snippet"]).encode("utf-8")).hexdigest(),
                "snippet": claim["snippet"],
                "claim_key": claim["claim_key"],
                "status": "valid",
            }
            for claim in grounded
        ]
        await SkillCitationRepository(session).bulk_replace_for_skill(skill.id, citations)
        logger.info(f"[ANALYSIS] data_truths for {repo_full_name}: {len(grounded)} claims, {len(citations)} citations")


async def analyze_repository(
    repo_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
    llm_connection_id: str,
    github_token: str,
    repo_full_name: str,
    default_branch: str,
) -> None:
    async with AsyncSessionFactory() as session:
        repo_repo = GitHubRepoRepository(session)

        rid = str(repo_id)
        try:
            await repo_repo.update_analysis_status(repo_id, "analyzing")
            _update_progress(rid, "Fetching repository metadata...", 1)

            owner, repo_name = repo_full_name.split("/", 1)

            languages, sha, tree = await asyncio.gather(
                github_service.get_repo_languages(github_token, owner, repo_name),
                github_service.get_latest_commit_sha(github_token, owner, repo_name, default_branch),
                github_service.get_repo_tree(github_token, owner, repo_name, default_branch),
            )

            blob_paths = _get_blob_paths(tree)
            _update_progress(rid, f"Scanning {len(blob_paths)} files...", 2, total_files=len(blob_paths))

            skill_file_lists: dict[str, list[str]] = {}
            for skill_type, config in SKILL_PROMPTS.items():
                skill_file_lists[skill_type] = _select_key_files(blob_paths, focus_boost=config.get("focus"))

            data_truths_paths = _select_key_files(blob_paths, focus_boost=DATA_TRUTHS_FOCUS)

            all_paths: list[str] = list(
                dict.fromkeys(path for files in [*skill_file_lists.values(), data_truths_paths] for path in files)
            )

            file_contents: dict[str, str] = {}
            total_chars = 0
            for i, path in enumerate(all_paths):
                if total_chars >= MAX_CHARS_PER_SKILL * (len(SKILL_PROMPTS) + 1):
                    break
                content = await github_service.get_file_content(github_token, owner, repo_name, path, ref=sha)
                if content:
                    file_contents[path] = content
                    total_chars += len(content)
                if (i + 1) % 5 == 0 or i + 1 == len(all_paths):
                    _update_progress(
                        rid, f"Reading file contents ({i + 1}/{len(all_paths)})...", 3, i + 1, len(all_paths)
                    )

            file_tree_str = "\n".join(blob_paths)

            use_claude_sdk = await is_using_claude_code_auth(str(llm_connection_id), session)

            _update_progress(rid, "Generating skills with AI...", 4, len(all_paths), len(all_paths))

            tasks = []
            for skill_type, config in SKILL_PROMPTS.items():
                skill_contents = _build_skill_contents(skill_file_lists[skill_type], file_contents)
                context_block = _build_context_block(skill_contents, file_tree_str, languages)
                prompt = f"{context_block}\n\n{config['user_prompt']}"

                tasks.append(
                    _generate_skill(
                        skill_type=skill_type,
                        skill_name=config["name"],
                        system_prompt=config["system"],
                        prompt=prompt,
                        llm_connection_id=llm_connection_id,
                        repo_id=repo_id,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        use_claude_sdk=use_claude_sdk,
                        repo_full_name=repo_full_name,
                        languages=languages,
                    )
                )

            results = await asyncio.gather(*tasks, return_exceptions=True)

            _update_progress(rid, "Finalizing analysis...", 5)

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    skill_type = list(SKILL_PROMPTS.keys())[i]
                    logger.error(f"[ANALYSIS] Skill {skill_type} failed: {result}")

            try:
                await _run_data_truths_pass(
                    repo_id=repo_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    llm_connection_id=llm_connection_id,
                    use_claude_sdk=use_claude_sdk,
                    repo_full_name=repo_full_name,
                    data_truths_paths=data_truths_paths,
                    file_contents=file_contents,
                    commit_sha=sha,
                )
            except Exception as e:
                logger.error(f"[ANALYSIS] data_truths pass failed for {repo_full_name}: {e}", exc_info=True)

            await repo_repo.update_analysis_status(repo_id, "completed", sha=sha, language_breakdown=languages)
            _clear_progress(rid)
            logger.info(f"[ANALYSIS] Completed for {repo_full_name}")

        except asyncio.CancelledError:
            logger.info(f"[ANALYSIS] Cancelled for {repo_full_name}")
            await repo_repo.update_analysis_status(repo_id, "cancelled")
            _clear_progress(rid)
        except Exception as e:
            logger.error(f"[ANALYSIS] Failed for {repo_full_name}: {e}", exc_info=True)
            await repo_repo.update_analysis_status(repo_id, "failed", error=str(e))
            _clear_progress(rid)


async def _generate_skill(
    skill_type: str,
    skill_name: str,
    system_prompt: str,
    prompt: str,
    llm_connection_id: str,
    repo_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
    use_claude_sdk: bool,
    repo_full_name: str,
    languages: dict,
) -> None:
    async with AsyncSessionFactory() as skill_session:
        skill_repo = CustomSkillRepository(skill_session)
        try:
            content = await CompletionService.complete(
                prompt=prompt,
                llm_connection_id=llm_connection_id,
                session=skill_session,
                system_prompt=system_prompt,
                use_claude_sdk=use_claude_sdk,
            )
        except CompletionError as err:
            raise RuntimeError(
                f"Skill '{skill_type}' for {repo_full_name} failed: {err.reason} — {err.message}"
            ) from err

        if skill_type == "codebase":
            description = (
                f"Use when asked about {repo_full_name} architecture, tech stack, "
                f"project structure, conventions, build, testing, or entry points."
            )
        elif skill_type == "data_layer":
            description = (
                f"Use when asked about {repo_full_name} database models, schemas, "
                f"migrations, relationships, data access, or ORM config."
            )
        else:
            lang_list = ", ".join(list(languages.keys())[:3]) if languages else ""
            description = f"{skill_name} for {repo_full_name}"
            if lang_list:
                description += f" ({lang_list})"
        description = description[:500]

        await skill_repo.upsert_github_skill(
            tenant_id=tenant_id,
            created_by=user_id,
            github_repo_id=repo_id,
            github_analysis_type=skill_type,
            name=skill_name,
            description=description,
            instructions=content,
        )


def _build_skill_contents(skill_paths: list[str], all_contents: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    total = 0
    for path in skill_paths:
        if path not in all_contents:
            continue
        content = all_contents[path]
        if total + len(content) > MAX_CHARS_PER_SKILL:
            break
        result[path] = content
        total += len(content)
    return result


def _build_context_block(file_contents: dict[str, str], file_tree: str, languages: dict) -> str:
    parts = [f"## Languages\n{json.dumps(languages, indent=2)}", f"## File Tree\n```\n{file_tree}\n```"]
    for path, content in file_contents.items():
        parts.append(f"## {path}\n```\n{content}\n```")
    return "\n\n".join(parts)


async def analyze_local_repository(
    repo_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
    llm_connection_id: str,
    local_path: str,
    repo_name: str,
) -> None:
    from server.services.local_repo_service import detect_local_languages, get_local_file_content, get_local_file_tree

    rid = str(repo_id)
    async with AsyncSessionFactory() as session:
        repo_repo = GitHubRepoRepository(session)

        try:
            await repo_repo.update_analysis_status(repo_id, "analyzing")
            _update_progress(rid, "Scanning local file tree...", 1)

            tree = await get_local_file_tree(local_path)
            languages = detect_local_languages(tree)
            blob_paths = _get_blob_paths(tree)
            _update_progress(
                rid,
                f"Found {len(blob_paths)} files across {len(languages)} languages...",
                2,
                total_files=len(blob_paths),
            )

            skill_file_lists: dict[str, list[str]] = {}
            for skill_type, config in SKILL_PROMPTS.items():
                skill_file_lists[skill_type] = _select_key_files(blob_paths, focus_boost=config.get("focus"))

            all_paths: list[str] = list(dict.fromkeys(path for files in skill_file_lists.values() for path in files))

            file_contents: dict[str, str] = {}
            total_chars = 0
            for i, path in enumerate(all_paths):
                if total_chars >= MAX_CHARS_PER_SKILL * len(SKILL_PROMPTS):
                    break
                content = await get_local_file_content(local_path, path)
                if content:
                    file_contents[path] = content
                    total_chars += len(content)
                if (i + 1) % 5 == 0 or i + 1 == len(all_paths):
                    _update_progress(
                        rid, f"Reading file contents ({i + 1}/{len(all_paths)})...", 3, i + 1, len(all_paths)
                    )

            file_tree_str = "\n".join(blob_paths)

            use_claude_sdk = await is_using_claude_code_auth(str(llm_connection_id), session)

            _update_progress(rid, "Generating skills with AI...", 4, len(all_paths), len(all_paths))

            tasks = []
            for skill_type, config in SKILL_PROMPTS.items():
                skill_contents = _build_skill_contents(skill_file_lists[skill_type], file_contents)
                context_block = _build_context_block(skill_contents, file_tree_str, languages)
                prompt = f"{context_block}\n\n{config['user_prompt']}"

                tasks.append(
                    _generate_skill(
                        skill_type=skill_type,
                        skill_name=config["name"],
                        system_prompt=config["system"],
                        prompt=prompt,
                        llm_connection_id=llm_connection_id,
                        repo_id=repo_id,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        use_claude_sdk=use_claude_sdk,
                        repo_full_name=repo_name,
                        languages=languages,
                    )
                )

            results = await asyncio.gather(*tasks, return_exceptions=True)

            _update_progress(rid, "Finalizing analysis...", 5)

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    skill_type = list(SKILL_PROMPTS.keys())[i]
                    logger.error(f"[ANALYSIS] Local skill {skill_type} failed: {result}")

            await repo_repo.update_analysis_status(repo_id, "completed", language_breakdown=languages)
            _clear_progress(rid)
            logger.info(f"[ANALYSIS] Completed for local repo {repo_name}")

        except asyncio.CancelledError:
            logger.info(f"[ANALYSIS] Cancelled for local repo {repo_name}")
            await repo_repo.update_analysis_status(repo_id, "cancelled")
            _clear_progress(rid)
        except Exception as e:
            logger.error(f"[ANALYSIS] Failed for local repo {repo_name}: {e}", exc_info=True)
            await repo_repo.update_analysis_status(repo_id, "failed", error=str(e))
            _clear_progress(rid)
