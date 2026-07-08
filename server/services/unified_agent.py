from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from time import perf_counter
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from agents import Agent

from agents import RunConfig
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.tenant_context import set_tenant_id
from server.prompts.prompt_variants import detect_model_family
from server.prompts.prompts import get_unified_agent_prompt_compact
from server.repositories.connections import ConnectionRepository
from server.repositories.custom_skill import CustomSkillRepository
from server.repositories.llm_connections import LLMConnectionRepository
from server.repositories.messages import MessageRepository
from server.repositories.notebooks import NotebookRepository
from server.repositories.settings import SettingRepository
from server.schemas.agent import AgentRequest
from server.schemas.notebooks import NotebookCreate
from server.services.agent_session_factory import create_agent_session
from server.services.claude_mcp_service import stream_claude_with_mcp_tools
from server.services.connections import ConnectionService
from server.services.crypto_service import CryptoService
from server.services.database_operations import DatabaseOperationsService
from server.services.dataset import DatasetService
from server.services.file_operations import DataFrameFileService
from server.services.llm_service import ModelService
from server.services.message_service import MessageService
from server.services.notebook import NotebookService
from server.services.redaction_service import RedactionService
from server.services.skill_registry import SkillRegistry
from server.services.title_generation import generate_notebook_title
from server.tools.agentic import (
    apply_html_patch,
    dashboard_search_replace,
    generate_dashboard_screenshot,
    get_chart_styling,
    get_database_schema,
    get_dataset_schema_by_id,
    get_existing_html,
    get_user_style_guidelines,
    save_query,
    save_skill_query,
    saved_query_schema,
    search_datasets,
    start_html_generation,
)
from server.tools.databricks import get_databricks_tools
from server.tools.dataframe import get_duckdb_tools
from server.tools.dynamodb import get_dynamodb_tools
from server.tools.filters import (
    define_dashboard_filters,
    get_dashboard_filter_config,
    get_filter_options,
    remove_dashboard_filter,
    update_dashboard_filter,
)
from server.tools.instruction import get_instruction_tools
from server.tools.mongo import get_mongo_tools
from server.tools.plan_tools import get_plan_tools
from server.tools.skill_executor import get_skill_executor_tools
from server.tools.sql import get_sql_tools
from server.tools.tool_friendly_descriptions import get_user_friendly_tool_description
from server.utils.cache_control import get_cache_control_injection_points
from server.utils.custom_logger import get_logger
from server.utils.litellm_utils import supports_parallel_tool_calls

logger = get_logger(__name__)

HTML_EDIT_START_TOOLS = {
    "start_html_generation",
    "apply_html_patch",
    "dashboard_search_replace",
}

HTML_EDIT_COMPLETE_TOOLS = {
    "apply_html_patch",
    "dashboard_search_replace",
}

HTML_CONTEXT_FETCH_TOOLS = {"get_existing_html"}


def _truncate_text(text: str | None, limit: int = 1500) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "... [truncated]"


FILLER_WORDS = frozenset(
    {
        "can",
        "you",
        "please",
        "show",
        "me",
        "i",
        "want",
        "to",
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "what",
        "how",
        "do",
        "does",
        "my",
        "of",
        "in",
        "for",
        "and",
        "or",
        "with",
        "this",
        "that",
        "it",
        "be",
        "have",
        "has",
        "had",
        "will",
        "would",
        "could",
        "should",
        "let",
        "help",
        "tell",
        "about",
        "from",
        "on",
        "at",
        "by",
        "get",
        "give",
        "make",
        "just",
        "like",
        "also",
        "some",
        "all",
        "any",
        "hi",
        "hey",
        "hello",
        "thanks",
        "thank",
        "know",
        "need",
        "see",
        "look",
        "find",
        "think",
    }
)


def _extract_search_keywords(message: str) -> str:
    words = message[:300].lower().split()
    meaningful = [w for w in words if w not in FILLER_WORDS and len(w) > 1]
    return " ".join(meaningful[:15])


def _summarize_html_tool_args(tool_name: str, arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        return {}

    if tool_name == "dashboard_search_replace":
        diff_content = arguments.get("diff_content") or ""
        return {
            "type": "search_replace",
            "diff_preview": _truncate_text(diff_content),
            "diff_content": diff_content,
            "block_count": (diff_content.count("<<<<<<< SEARCH") if isinstance(diff_content, str) else 0),
        }
    if tool_name == "apply_html_patch":
        patch_text = arguments.get("patch_text") or ""
        return {
            "type": "patch",
            "patch_preview": _truncate_text(patch_text),
            "patch_text": patch_text,
        }
    if tool_name == "start_html_generation":
        return {"type": "start_signal"}

    return {}


def merge_multimodal_input_with_history(history: list, new_input_list: list) -> list:
    """Merge new multimodal input with conversation history for the Agents SDK."""
    return history + new_input_list


def _build_minimal_skills_hint(enabled_skills: dict[str, dict], custom_skills: dict[str, dict]) -> str:
    """Build a minimal hint about available skills showing scopes (no full documentation)."""
    if not enabled_skills and not custom_skills:
        return ""

    byaan_lines = []
    if enabled_skills:
        skill_info: dict[str, list[str]] = {}
        for key, data in enabled_skills.items():
            name = data.get("skill_name", key)
            skill_info.setdefault(name, []).append(data.get("scope", "user"))

        for name, scopes in skill_info.items():
            scope_labels = []
            if "user" in scopes:
                scope_labels.append("personal key")
            if "org" in scopes:
                scope_labels.append("team shared key")
            byaan_lines.append(f"- {name}: {', '.join(scope_labels)}")

    custom_api_lines = []
    custom_info_lines = []
    if custom_skills:
        for name, data in custom_skills.items():
            creator = data.get("created_by_name", "")
            creator_info = f" (by {creator})" if creator else ""
            line = f"- {name}: {data.get('description', '')[:60]}{creator_info}"
            if data.get("can_execute_api", False):
                custom_api_lines.append(line)
            else:
                custom_info_lines.append(line)

    sections = []

    if byaan_lines:
        sections.append(f"""Byaan Skills (CAN execute APIs via execute_skill_api):
{chr(10).join(byaan_lines)}""")

    if custom_api_lines:
        sections.append(f"""Custom Skills with API (CAN execute APIs via execute_skill_api):
{chr(10).join(custom_api_lines)}""")

    if custom_info_lines:
        sections.append(f"""Custom Skills (INFORMATIONAL ONLY - no API execution):
{chr(10).join(custom_info_lines)}""")

    skills_display = "\n\n".join(sections)

    return f"""
<external_skills>
Available skills:

{skills_display}

USAGE:
1. search_enabled_skills(query) - find relevant skills
2. get_skill_definition(skill_name) - load full documentation
3. execute_skill_api(...) - make API requests (Byaan skills only)
4. save_skill_query(...) - save API call for dashboard use (returns query_id)

Scopes: "user" = personal key, "org" = team shared key

FOR DASHBOARDS (MANDATORY WORKFLOW):
1. save_skill_query() → saves API call, returns query_id
2. saved_query_schema() → get output_schema with exact field names
3. Write dashboard that FETCHES data via batch endpoint (using query_id)
4. Map fields using ONLY the field names from output_schema

ABSOLUTE RULES:
- NEVER hardcode data arrays in dashboard (e.g., const data = [{...}])
- ALL data must come from saved queries via batch endpoint fetch
- Batch endpoint FLATTENS responses: result.data[0].result is a FLAT ARRAY
- NEVER use nested paths like .teams.nodes or .viewer
</external_skills>"""


async def _load_custom_skills(
    tenant_id: UUID,
    user_id: UUID | None,
    session: AsyncSession,
) -> dict[str, dict]:
    """Load custom skills accessible to the user, or org-only if no user."""
    try:
        repo = CustomSkillRepository(session)
        if user_id:
            skills = await repo.list_accessible(tenant_id, user_id)
        else:
            skills = await repo.list_org_accessible(tenant_id)

        custom_skills = {}
        for skill in skills:
            creator_name = ""
            if skill.creator:
                creator_name = skill.creator.full_name or skill.creator.email.split("@")[0]

            entry = {
                "id": str(skill.id),
                "name": skill.name,
                "description": skill.description,
                "instructions": skill.instructions,
                "scope": skill.scope,
                "created_by": str(skill.created_by),
                "created_by_name": creator_name,
                "can_execute_api": skill.can_execute_api,
                "api_base_url": skill.api_base_url,
                "api_type": skill.api_type,
                "api_auth_type": skill.api_auth_type,
                "api_domain": skill.api_domain,
                "domain_active": skill.domain_active,
            }

            if skill.api_credentials_encrypted:
                try:
                    decrypted = await CryptoService.decrypt_config(skill.api_credentials_encrypted, session)
                    entry["credentials"] = decrypted
                except Exception as e:
                    logger.warning(f"Failed to decrypt credentials for custom skill {skill.name}: {e}")

            custom_skills[skill.name] = entry

        return custom_skills
    except Exception as e:
        logger.warning(f"Failed to load custom skills: {e}")
        return {}


async def _load_skills_for_agent(
    tenant_id: UUID | None,
    user_id: UUID | None,
    session: AsyncSession,
    tools: list,
    instructions: str,
) -> tuple[str, dict[str, dict], list[str], dict[str, dict]]:
    """Load enabled skills and custom skills, extend tools, and return context data."""
    if not tenant_id:
        return instructions, {}, [], {}

    try:
        if user_id:
            enabled_skills = await SkillRegistry.get_enabled_skills(tenant_id, user_id, session)
        else:
            enabled_skills = await SkillRegistry.get_org_enabled_skills(tenant_id, session)
        enabled_skill_names = (
            list({data.get("skill_name") for data in enabled_skills.values()}) if enabled_skills else []
        )

        custom_skills = await _load_custom_skills(tenant_id, user_id, session)

        has_any_skills = enabled_skills or custom_skills
        if has_any_skills:
            tools.extend(get_skill_executor_tools())
            instructions = instructions + _build_minimal_skills_hint(enabled_skills, custom_skills)
            all_skill_names = enabled_skill_names + list(custom_skills.keys())
            logger.info(f"Added skill tools for: {all_skill_names}")

        return instructions, enabled_skills, enabled_skill_names, custom_skills
    except Exception as e:
        logger.warning(f"Failed to load skill tools: {e}")
        return instructions, {}, [], {}


def _add_skill_credentials_to_context(
    context: dict, enabled_skills: dict[str, dict], custom_skills: dict[str, dict]
) -> None:
    """Add skill credentials and custom skills to tool context."""
    context["enabled_skills"] = enabled_skills
    context["custom_skills"] = custom_skills
    for key, skill_data in enabled_skills.items():
        context[f"{key}_credentials"] = skill_data.get("credentials", {})


async def _load_github_repos_for_agent(tenant_id: UUID, user_id: UUID | None, session: AsyncSession) -> dict[str, dict]:
    try:
        from server.repositories.custom_skill import CustomSkillRepository
        from server.repositories.github_repository import GitHubRepoRepository
        from server.services import github_service

        repo_repo = GitHubRepoRepository(session)
        custom_skill_repo = CustomSkillRepository(session)

        token = await _get_github_token_for_agent(tenant_id, user_id, session)
        if not token:
            logger.info("[GITHUB AGENT] No GitHub token available, skipping GitHub repos")
            return {}

        if user_id:
            repos = await repo_repo.list_by_user_and_source(tenant_id, user_id, "github")
        else:
            repos = await repo_repo.list_org_accessible(tenant_id, "github")

        logger.info(f"[GITHUB AGENT] Found {len(repos)} GitHub repos for user {user_id}")

        result = {}
        for repo in repos:
            if repo.analysis_status != "completed":
                logger.info(f"[GITHUB AGENT] Skipping repo '{repo.repo_full_name}' — status: {repo.analysis_status}")
                continue
            skills = await custom_skill_repo.list_by_github_repo(repo.id)
            skills_dict = {}
            for s in skills:
                key = s.github_analysis_type or f"custom:{s.name}"
                skills_dict[key] = {
                    "name": s.name,
                    "content": s.instructions,
                }

            owner, repo_name = repo.repo_full_name.split("/", 1)
            file_tree: list[str] = []
            try:
                tree = await github_service.get_repo_tree(token, owner, repo_name, repo.default_branch)
                file_tree = [item["path"] for item in tree if item.get("type") == "blob"]
            except Exception:
                pass

            result[str(repo.id)] = {
                "repo_full_name": repo.repo_full_name,
                "default_branch": repo.default_branch,
                "skills": skills_dict,
                "file_tree": file_tree,
            }

        logger.info(f"[GITHUB AGENT] Loaded {len(result)} analyzed repos into agent context")
        return result
    except Exception as e:
        logger.error(f"[GITHUB AGENT] Failed to load GitHub repos for agent: {e}", exc_info=True)
        return {}


async def _get_github_token_for_agent(tenant_id: UUID, user_id: UUID | None, session: AsyncSession) -> str | None:
    try:
        from server.services import github_service

        if user_id:
            token = await github_service.get_github_token(tenant_id, user_id, session)
            if token:
                return token
        return await github_service.get_org_github_token(tenant_id, session)
    except Exception:
        return None


def _build_github_repos_hint(github_repos: dict[str, dict]) -> str:
    if not github_repos:
        return ""

    skill_type_descriptions = {
        "codebase": "Architecture, tech stack, directory structure, entry points",
        "data_layer": "Database models, migrations, data access patterns, schemas",
        "logging": "Logging framework, error handling, observability setup",
        "code_review": "Coding conventions, test patterns, linter/formatter config",
    }

    repo_lines = []
    for repo_id, data in github_repos.items():
        repo_lines.append(f"\n- **{data['repo_full_name']}** (id: `{repo_id}`)")
        skills = data.get("skills", {})
        if skills:
            for skill_type, skill_data in skills.items():
                desc = skill_type_descriptions.get(skill_type, "Custom analysis")
                repo_lines.append(f"  - `{skill_type}` ({skill_data.get('name', skill_type)}): {desc}")

    return f"""

<github_repos>
You have access to the following connected GitHub repositories with pre-analyzed skills:
{"".join(repo_lines)}

SKILL TYPES:
- `codebase`: Full architecture overview — use this FIRST when answering questions about how the repo works
- `data_layer`: Database models, schemas, migrations — use for data/DB questions
- `logging`: Logging and error handling patterns — use for observability questions
- `code_review`: Coding standards and conventions — use for review/style questions
- `custom`: User-created analysis skills

TOOLS & WORKFLOW:
1. `get_repo_skill(repo_id, skill_type)` — Read a pre-analyzed skill summary. Best for HIGH-LEVEL overview/architecture.
2. `list_repo_skills(repo_id)` — List all available skills for a repo.
3. `search_repo_code(repo_id, query)` — Search file paths in the repo tree (case-insensitive substring match).
4. `get_repo_file(repo_id, path)` — Fetch actual CURRENT file content from GitHub. Use this for any question about specific code.
5. `create_repo_skill(repo_id, skill_name, description)` — Create a custom analysis skill. Describe what to analyze; the tool fetches code, runs LLM analysis, and saves the result.

⚠ IMPORTANT: Skills are SNAPSHOTS captured at analyze time. They may be stale after the user pushes new commits.
For any question about RECENT or SPECIFIC code, always read the live file via get_repo_file — do NOT rely on the skill alone.

WHEN TO USE WHICH:
- Project orientation (architecture, tech stack, structure, conventions) → start with get_repo_skill(skill_type="codebase").
- Data layer questions (models, schemas, migrations, ORM setup) → get_repo_skill(skill_type="data_layer").
- Locating something specific in the code → search_repo_code to find the path, then get_repo_file to read it. Do NOT guess paths.
- Reasoning about current/recent behavior, dependencies, configs, error traces → get_repo_file on the implicated files. Skills can be stale.
- Cross-cutting analysis the existing skills do not cover → create_repo_skill with a clear focus description.
- Combine with database queries when the user asks about both code and data.

BE PROACTIVE — do NOT wait for the user to say "look in the repo" or "check the github". If <github_repos> contains a repo whose name or context matches the question, explore it on the FIRST turn.

PERSISTENCE — save what you discover:
After exploring a repo (reading files, mapping handlers, diagnosing bugs), call add_learning to record reusable findings.
Title MUST start with the exact repo_full_name shown above (e.g. "<repo_full_name> — <insight>") so search_learnings can find it later.
Save WHERE code lives, HOW modules are wired, gotchas — never copy file contents.
Before exploring, call search_learnings with the repo_full_name + topic to recall past discoveries.
This is what stops the "push me again" loop — every exploration should leave behind a learning the next session reuses.

CREATING CUSTOM SKILLS:
- When a user asks for analysis NOT covered by existing skills (security audit, API docs, performance review, etc.)
- Workflow: (1) call `search_repo_code` and `get_repo_file` to gather the source you need, (2) write the markdown analysis yourself, (3) call `create_repo_skill(repo_id, skill_name, description, instructions=<the markdown>)` to persist it.
- The tool just saves — it does not generate content. You are responsible for the analysis quality.
- After saving, the skill is retrievable via `get_repo_skill(repo_id, "custom:<skill_name>")` in future conversations.
</github_repos>"""


async def _load_local_repos_for_agent(tenant_id: UUID, user_id: UUID, session: AsyncSession) -> dict[str, dict]:
    try:
        from server.repositories.custom_skill import CustomSkillRepository
        from server.repositories.github_repository import GitHubRepoRepository
        from server.services.local_repo_service import get_local_file_tree

        repo_repo = GitHubRepoRepository(session)
        custom_skill_repo = CustomSkillRepository(session)
        repos = await repo_repo.list_by_user_and_source(tenant_id, user_id, "local")

        result = {}
        for repo in repos:
            if repo.analysis_status != "completed" or not repo.local_path:
                continue

            skills = await custom_skill_repo.list_by_github_repo(repo.id)
            skills_dict = {}
            for s in skills:
                key = s.github_analysis_type or f"custom:{s.name}"
                skills_dict[key] = {"name": s.name, "content": s.instructions}

            file_tree: list[dict] = []
            try:
                file_tree = await get_local_file_tree(repo.local_path)
            except Exception:
                pass

            result[str(repo.id)] = {
                "repo_full_name": repo.repo_full_name,
                "local_path": repo.local_path,
                "skills": skills_dict,
                "file_tree": file_tree,
            }

        logger.info(f"[LOCAL REPOS] Loaded {len(result)} analyzed local repos into agent context")
        return result
    except Exception as e:
        logger.error(f"[LOCAL REPOS] Failed to load local repos for agent: {e}", exc_info=True)
        return {}


def _build_local_repos_hint(local_repos: dict[str, dict]) -> str:
    if not local_repos:
        return ""

    skill_type_descriptions = {
        "codebase": "Architecture, tech stack, directory structure, entry points",
        "data_layer": "Database models, migrations, data access patterns, schemas",
    }

    repo_lines = []
    for repo_id, data in local_repos.items():
        repo_lines.append(f"\n- **{data['repo_full_name']}** (id: `{repo_id}`)")
        skills = data.get("skills", {})
        if skills:
            for skill_type, skill_data in skills.items():
                desc = skill_type_descriptions.get(skill_type, "Custom analysis")
                repo_lines.append(f"  - `{skill_type}` ({skill_data.get('name', skill_type)}): {desc}")

    return f"""

<local_repos>
You have access to the following connected LOCAL repositories with pre-analyzed skills:
{"".join(repo_lines)}

LOCAL REPO TOOLS:
1. `get_local_repo_skill(repo_id, skill_type)` — Read a skill's full content. Start here.
2. `search_local_repo_files(repo_id, query)` — Search file paths in the repo tree.
3. `read_local_repo_file(repo_id, path)` — Read file content from disk.
4. `grep_local_repo(repo_id, pattern, file_extensions)` — Search file contents with regex.
5. `list_local_repo_directory(repo_id, path)` — List files/dirs in a specific directory.

WHEN TO USE:
- When a user asks about their local codebase, architecture, or code patterns
- Load the relevant skill FIRST for context, then use search/read/grep for details
- Use grep_local_repo for finding specific code patterns across files
</local_repos>"""


async def is_using_claude_code_auth(llm_connection_id: str, session: AsyncSession) -> bool:
    """
    Check if an LLM connection is configured to use Claude Code authentication.

    Args:
        llm_connection_id: The ID of the LLM connection
        session: Database session

    Returns:
        True if using Claude Code auth, False otherwise
    """
    try:
        repo = LLMConnectionRepository(session)
        llm_connection = await repo.get(llm_connection_id)

        if not llm_connection:
            logger.warning(f"[CLAUDE CODE CHECK] No connection found for ID: {llm_connection_id}")
            return False

        # Check if provider type is 'claude_code'
        is_claude_code = llm_connection.type == "claude_code"
        logger.info(f"[CLAUDE CODE CHECK] Connection type: {llm_connection.type}, is_claude_code: {is_claude_code}")
        return is_claude_code

    except Exception as e:
        logger.error(f"Error checking Claude Code auth status: {e}")
        return False


def _build_agent_tools(
    database_schemas: list[dict] | None = None,
    plan_mode: bool = False,
    has_github_repos: bool = False,
    has_local_repos: bool = False,
) -> list:
    """
    Build the unified tool list for the agent.

    This is the SINGLE SOURCE OF TRUTH for tool configuration.
    Used by both OpenAI Agents SDK and Claude SDK paths.
    """
    tools = []

    if database_schemas:
        db_types = {db.get("db_type", "").lower() for db in database_schemas}

        if "mongo" in db_types:
            tools.extend(get_mongo_tools())
        if any(t in db_types for t in ["pg", "postgres", "postgresql", "sql", "mssql", "mysql", "sqlite"]):
            tools.extend(get_sql_tools())
        if any(t in db_types for t in ["duckdb", "csv", "excel", "parquet", "json", "file"]):
            tools.extend(get_duckdb_tools())
        if "dynamodb" in db_types:
            tools.extend(get_dynamodb_tools())
        if "databricks" in db_types:
            tools.extend(get_databricks_tools())
    else:
        tools.extend(get_sql_tools())
        tools.extend(get_mongo_tools())
        tools.extend(get_duckdb_tools())
        tools.extend(get_dynamodb_tools())
        tools.extend(get_databricks_tools())

    tools.extend(
        [
            save_query,
            get_database_schema,
            get_chart_styling,
        ]
    )

    tools.extend(
        [
            start_html_generation,
            get_existing_html,
            apply_html_patch,
            dashboard_search_replace,
            saved_query_schema,
            generate_dashboard_screenshot,
        ]
    )

    tools.extend(
        [
            get_user_style_guidelines,
        ]
    )

    tools.extend(
        [
            get_filter_options,
            define_dashboard_filters,
            update_dashboard_filter,
            remove_dashboard_filter,
            get_dashboard_filter_config,
        ]
    )

    tools.extend(
        [
            search_datasets,
            get_dataset_schema_by_id,
            save_skill_query,
        ]
    )

    tools.extend(get_instruction_tools())

    from server.tools.learnings import get_learning_tools

    tools.extend(get_learning_tools())

    from server.tools.skill_executor import get_skill_management_tools

    tools.extend(get_skill_management_tools())

    if plan_mode:
        tools.extend(get_plan_tools())

    if has_github_repos:
        from server.tools.github_tools import get_github_tools

        tools.extend(get_github_tools())

    if has_local_repos:
        from server.tools.local_repo_tools import get_local_repo_tools

        tools.extend(get_local_repo_tools())

    return tools


async def create_unified_agent(
    llm_connection_id: str,
    model: str | None = None,
    notebook_id: str | None = None,
    database_schemas: list[dict] | None = None,
    tenant_id: UUID | None = None,
    user_id: UUID | None = None,
    session: AsyncSession | None = None,
    plan_mode: bool = False,
    instructions: str | None = None,
    memory: str | None = None,
    learnings: list[dict] | None = None,
) -> tuple[Agent, dict[str, dict], list[str], dict[str, dict]]:
    """
    Create a unified agent with all tools - no handoffs.
    Combines query writing, saving, and dashboard generation in one agent.

    Supports multiple databases in a single notebook.

    Args:
        llm_connection_id: ID of the LLM connection to use
        model: Optional model name
        notebook_id: Optional notebook ID
        database_schemas: List of database schema dictionaries (from get_database_schema tool)
        user_id: Optional user ID for loading skill credentials
        session: Optional database session for loading skill credentials

    Returns:
        Tuple of (agent, enabled_skills dict, enabled_skill_names list, custom_skills dict)
    """
    try:
        workspace_instructions = instructions if instructions is not None else memory
        instructions = get_unified_agent_prompt_compact(
            database_schemas=database_schemas,
            model=model,
            plan_mode=plan_mode,
            instructions=workspace_instructions,
            learnings=learnings,
        )

        model_family = detect_model_family(model)
        logger.info(f"Using {model_family.upper()}-optimized prompt components for model: {model or 'default'}")

        github_repos = {}
        local_repos = {}
        if tenant_id and session:
            github_repos = await _load_github_repos_for_agent(tenant_id, user_id, session)
            local_repos = await _load_local_repos_for_agent(tenant_id, user_id, session)

        tools = _build_agent_tools(
            database_schemas,
            plan_mode=plan_mode,
            has_github_repos=bool(github_repos),
            has_local_repos=bool(local_repos),
        )

        if database_schemas:
            db_types = {db.get("db_type", "").lower() for db in database_schemas}
            logger.info(
                f"Creating unified agent for {len(database_schemas)} database(s) with types: {sorted(db_types)}"
            )
        else:
            logger.info("No database schemas provided, enabling SQL, MongoDB, and DuckDB tools")

        instructions, enabled_skills, enabled_skill_names, custom_skills = await _load_skills_for_agent(
            tenant_id, user_id, session, tools, instructions
        )

        if github_repos:
            instructions += _build_github_repos_hint(github_repos)
        if local_repos:
            instructions += _build_local_repos_hint(local_repos)

        logger.info(f"Creating unified agent with {len(tools)} tools for notebook: {notebook_id}")

        from agents import ModelSettings

        enable_parallel = supports_parallel_tool_calls(model)

        # Build ModelSettings with parallel tool calls and cache control
        model_settings_kwargs = {}
        extra_args = {}

        # Add cache_control_injection_points for Claude models (LiteLLM native feature)
        # OpenAI/Groq/Gemini have automatic caching, so this returns None for them
        cache_injection_points = get_cache_control_injection_points(model)
        if cache_injection_points:
            extra_args["cache_control_injection_points"] = cache_injection_points
            logger.info(f"Enabled prompt caching for model {model}")

        is_codex = model and ("codex" in model.lower())
        codex_settings = {}
        if is_codex:
            codex_settings["store"] = False

        if enable_parallel or extra_args or is_codex:
            model_settings_kwargs["model_settings"] = ModelSettings(
                parallel_tool_calls=True if enable_parallel else None,
                extra_args=extra_args if extra_args else None,
                **codex_settings,
            )

        agent = await ModelService.get_agent_with_dynamic_model(
            name="Byaan (Unified BI Agent)",
            instructions=instructions,
            tools=tools,
            handoffs=[],
            llm_connection_id=llm_connection_id,
            model=model,
            **model_settings_kwargs,
        )

        logger.info("Unified agent created successfully")
        return agent, enabled_skills, enabled_skill_names, custom_skills

    except Exception as e:
        logger.error(
            f"Error creating unified agent: {e}",
            posthog_context={
                "function": "create_unified_agent",
                "llm_connection_id": llm_connection_id,
                "model": model,
                "notebook_id": notebook_id,
                "database_count": len(database_schemas) if database_schemas else 0,
            },
        )
        raise


async def stream_handoff_agent_response(
    request: AgentRequest,
    session: AsyncSession,
    tenant_id: UUID | None = None,
    user_id: UUID | None = None,
) -> AsyncGenerator[str, None]:
    """Stream Server-Sent Events for the unified agent."""
    assistant_response = ""
    html_was_edited = False
    html_generation_started = False
    html_edit_completed = False
    last_tool_name = None
    last_tool_args: dict[str, Any] | None = None
    was_cancelled = False
    thread_id = None
    title_generation_task = None
    title_emitted = False
    is_first_message = False  # Track if this is the first conversation for title generation
    html_edit_session_id: str | None = None
    html_context_request_id: str | None = None
    tool_runtime_start: float | None = None
    tool_runtime_name: str | None = None
    tool_output_seen = False

    try:
        # Restore tenant context for background task (ContextVar not inherited by asyncio tasks)
        if tenant_id:
            set_tenant_id(tenant_id)

        logger.info("started the stream")
        start_event = json.dumps({"type": "started", "message": "Processing request..."}, ensure_ascii=False)
        yield f"data: {start_event}\n\n"

        # Handle notebook creation if requested
        if request.create_notebook and not request.notebook_id:
            try:
                logger.info("Creating new notebook from stream request")

                # Generate auto-name with timestamp
                from datetime import datetime

                now = datetime.now()
                auto_name = now.strftime("Notebook %b %d %I:%M %p")

                # Create notebook
                notebook = await NotebookService.create_notebook(
                    session,
                    NotebookCreate(notebook_name=auto_name, description=None),
                    tenant_id=tenant_id,
                    user_id=user_id,
                )

                # Update request with new notebook_id
                request.notebook_id = notebook.id

                # Associate datasources if provided (supports multiple)
                if request.datasource_ids:
                    logger.info(f"Associating {len(request.datasource_ids)} datasources with notebook {notebook.id}")
                    for datasource_id in request.datasource_ids:
                        await DatasetService.associate_dataset_with_notebook(session, datasource_id, notebook.id)

                # Yield notebook_created event
                notebook_event = json.dumps(
                    {
                        "type": "notebook_created",
                        "notebook_id": str(notebook.id),
                        "notebook_name": notebook.notebook_name,
                    },
                    ensure_ascii=False,
                )
                yield f"data: {notebook_event}\n\n"
                logger.info(f"Successfully created notebook {notebook.id}")

            except Exception as notebook_error:
                logger.error(
                    f"Error creating notebook: {notebook_error}",
                    exc_info=True,
                    posthog_context={
                        "function": "stream_handoff_agent_response.create_notebook",
                        "datasource_ids": request.datasource_ids,
                    },
                )
                error_event = json.dumps(
                    {
                        "type": "error",
                        "text": f"Failed to create notebook: {str(notebook_error)}",
                    },
                    ensure_ascii=False,
                )
                yield f"data: {error_event}\n\n"
                return

        if not request.llm_connection_id:
            error_msg = "No LLM connection specified. Please select an AI model."
            event_data = json.dumps({"type": "error", "text": error_msg}, ensure_ascii=False)
            yield f"data: {event_data}\n\n"
            return

        database_schemas: list[dict] = []
        redaction_rules: dict[str, dict] = {}

        if request.notebook_id:
            try:
                # Fetch ALL datasets for the notebook (supports multi-database)
                datasets = await DatasetService.get_datasets_by_notebook(session, request.notebook_id)

                if datasets:
                    conn_repo = ConnectionRepository(session)

                    for idx, dataset in enumerate(datasets, start=1):
                        try:
                            cached_schema = None
                            effective_db_type = None
                            datasource_id = None
                            connection_name = None

                            if dataset.type == "file":
                                # File-based dataset
                                dataset_with_files = await DatasetService.get_dataset(session, dataset.id)
                                if not dataset_with_files or not dataset_with_files.files:
                                    logger.warning(f"No files found in dataset {dataset.id}, skipping")
                                    continue

                                cached_schema = await DataFrameFileService.get_file_schema_multi(
                                    dataset_with_files.files,
                                    session=session,
                                    dataset=dataset_with_files,
                                    use_cache=True,
                                    save_to_cache=True,
                                )
                                effective_db_type = "duckdb"
                                datasource_id = dataset.id
                                connection_name = dataset_with_files.name or f"File Dataset {idx}"

                            elif dataset.type == "connection":
                                # Connection-based dataset
                                connection = await conn_repo.get(dataset.connection_id)
                                if not connection:
                                    logger.warning(f"Connection {dataset.connection_id} not found, skipping")
                                    continue

                                cached_schema = ConnectionService.get_cached_schema(connection)
                                if not cached_schema:
                                    logger.warning(f"No cached schema for connection {dataset.connection_id}, skipping")
                                    continue

                                effective_db_type = connection.type.lower()
                                datasource_id = dataset.id  # Use dataset.id for annotations consistency
                                connection_name = connection.name

                            else:
                                logger.warning(f"Unsupported dataset type: {dataset.type}, skipping")
                                continue

                            if not cached_schema:
                                continue

                            # Annotate schema with user annotations
                            annotated_schema = await DatabaseOperationsService.annotate_schema_with_user_annotations(
                                datasource_id, cached_schema, session
                            )

                            redacted_columns = await RedactionService.get_redacted_columns(datasource_id, session)
                            redacted_tables = await RedactionService.get_redacted_tables(datasource_id, session)
                            if redacted_columns or redacted_tables:
                                rule_key = (
                                    str(dataset.connection_id) if dataset.type == "connection" else str(dataset.id)
                                )
                                redaction_rules[rule_key] = {
                                    "columns": {t: list(cols) for t, cols in redacted_columns.items()},
                                    "tables": list(redacted_tables),
                                }

                            # Format schema for prompt
                            if dataset.type == "file":
                                formatted_schema = DataFrameFileService.format_file_schema_for_prompt(annotated_schema)
                            else:
                                formatted_schema = DatabaseOperationsService.format_schema_for_prompt(
                                    annotated_schema, effective_db_type
                                )

                            # Create schema summary with column names (compact format)
                            # Filter out redacted tables/columns so the LLM never sees them
                            if dataset.type == "file":
                                schema_tables = annotated_schema.get("schema", {})
                                tables_with_columns = {}
                                for table_name, table_info in schema_tables.items():
                                    if table_info.get("redacted_table"):
                                        continue
                                    columns = [
                                        col.get("name")
                                        for col in table_info.get("columns", [])
                                        if not col.get("redacted")
                                    ]
                                    tables_with_columns[table_name] = columns
                                schema_summary = {
                                    "type": "DuckDB File Dataset",
                                    "dataset_id": dataset.id,
                                    "files_count": len(tables_with_columns),
                                    "tables": tables_with_columns,
                                }
                            elif effective_db_type == "mongo":
                                collections = annotated_schema.get("schema", {})
                                collections_with_fields = {}
                                for coll_name, coll_info in collections.items():
                                    if coll_info.get("redacted_table"):
                                        continue
                                    fields = []
                                    redacted_field_names = set(coll_info.get("redacted_fields", []))
                                    if "nested_schema" in coll_info and "properties" in coll_info["nested_schema"]:
                                        fields = [
                                            f
                                            for f in coll_info["nested_schema"]["properties"].keys()
                                            if f not in redacted_field_names
                                        ]
                                    collections_with_fields[coll_name] = fields
                                schema_summary = {
                                    "type": "MongoDB",
                                    "database": annotated_schema.get("database_name", "unknown"),
                                    "collections_count": len(collections_with_fields),
                                    "collections": collections_with_fields,
                                }
                            else:
                                tables = annotated_schema.get("schema", {})
                                tables_with_columns = {}
                                for table_name, table_info in tables.items():
                                    if table_info.get("redacted_table"):
                                        continue
                                    columns = [
                                        col.get("name")
                                        for col in table_info.get("columns", [])
                                        if not col.get("redacted")
                                    ]
                                    tables_with_columns[table_name] = columns
                                schema_summary = {
                                    "type": "SQL/PostgreSQL"
                                    if effective_db_type == "pg"
                                    else f"SQL/{effective_db_type.upper()}",
                                    "database": annotated_schema.get("database_name", "unknown"),
                                    "tables_count": len(tables_with_columns),
                                    "tables": tables_with_columns,
                                }

                            # Build database entry
                            database_entry = {
                                "database_number": idx,
                                "dataset_id": dataset.id,
                                "dataset_type": dataset.type,
                                "db_type": effective_db_type,
                                "formatted_schema": formatted_schema,
                                "schema_summary": schema_summary,
                                "connection_name": connection_name,
                            }

                            if dataset.type == "connection" and connection:
                                database_entry["connection_id"] = dataset.connection_id

                            database_schemas.append(database_entry)

                            logger.info(
                                f"Loaded schema {idx} for notebook {request.notebook_id}: {connection_name} ({effective_db_type})"
                            )

                        except Exception as dataset_error:
                            logger.error(
                                f"Error processing dataset {dataset.id}: {dataset_error}",
                                exc_info=True,
                                posthog_context={
                                    "function": "stream_handoff_agent_response.process_dataset",
                                    "notebook_id": request.notebook_id,
                                    "dataset_id": dataset.id,
                                    "dataset_type": dataset.type,
                                },
                            )
                            # Continue with other datasets

                    if database_schemas:
                        logger.info(
                            f"Loaded {len(database_schemas)} database schema(s) for notebook {request.notebook_id}"
                        )
                    else:
                        logger.warning(f"No cached schemas found for notebook {request.notebook_id}")
                else:
                    logger.warning(f"No datasets found for notebook {request.notebook_id}")

            except Exception as db_conn_error:
                logger.error(
                    f"Error getting datasets for notebook {request.notebook_id}: {db_conn_error}",
                    posthog_context={
                        "function": "stream_handoff_agent_response.get_datasets",
                        "notebook_id": request.notebook_id,
                    },
                )
                # Continue without database schemas

        # Send progress event after database setup
        progress_event = json.dumps({"type": "progress", "message": "Initializing agent..."}, ensure_ascii=False)
        yield f"data: {progress_event}\n\n"

        # SHARED SETUP FOR BOTH CLAUDE MCP AND OPENAI PATHS
        # ============================================

        # Load workspace instructions for system prompt injection (serves as both instructions and memory)
        notebook_memory: str | None = None
        if tenant_id:
            try:
                settings_repo = SettingRepository(session)
                setting = await settings_repo.get_by_key("workspace_instructions")
                if setting and setting.setting_value:
                    notebook_memory = setting.setting_value
                    logger.info(f"Loaded workspace instructions for tenant {tenant_id} ({len(notebook_memory)} chars)")
            except Exception as mem_error:
                logger.warning(f"Failed to load workspace instructions: {mem_error}")

        relevant_learnings: list[dict] | None = None
        if tenant_id:
            try:
                from server.repositories.learning import LearningRepository

                learning_repo = LearningRepository(session)
                search_query = _extract_search_keywords(request.message) if request.message else ""
                logger.info(
                    f"[LEARNING] Search keywords extracted: '{search_query}' from message: '{request.message[:100]}'"
                )

                dataset_ids = (
                    [str(ds.get("dataset_id", "")) for ds in database_schemas if ds.get("dataset_id")]
                    if database_schemas
                    else []
                )

                if search_query or dataset_ids:
                    all_results = []
                    seen_ids = set()

                    if search_query:
                        title_results = await learning_repo.search_by_title(search_query, limit=10)
                        for r in title_results:
                            if str(r.id) not in seen_ids:
                                all_results.append(r)
                                seen_ids.add(str(r.id))

                    for ds_id in dataset_ids:
                        ds_results = await learning_repo.search_by_dataset_id(ds_id, limit=10)
                        for r in ds_results:
                            if str(r.id) not in seen_ids:
                                all_results.append(r)
                                seen_ids.add(str(r.id))

                    if all_results:
                        relevant_learnings = [
                            {"id": str(r.id), "title": r.title, "learning": r.learning} for r in all_results
                        ]
                        for r in all_results:
                            logger.info(f"[LEARNING] Matched title: '{r.title}'")
                    else:
                        logger.info("[LEARNING] No matching learnings found")
            except Exception as learn_error:
                logger.warning(f"Failed to load learnings: {learn_error}")
                await session.rollback()

        # Create agent session for database persistence (uses SQLite locally, PostgreSQL in hosted mode)
        session_notebook_id = str(request.notebook_id) if request.notebook_id else "default"
        agent_session = await create_agent_session(session_notebook_id)

        # Save user message immediately (skip in preview mode)
        if request.notebook_id and not request.is_preview:
            try:
                thread_id = await MessageService.save_agent_user_message(
                    session,
                    request.notebook_id,
                    request.message,
                    request.db_type,
                    request.attachments,
                )
                logger.info(f"Saved user message immediately for notebook {request.notebook_id}")
            except Exception as user_msg_error:
                logger.error(
                    f"Failed to save user message for notebook {request.notebook_id}: {user_msg_error}",
                    posthog_context={
                        "function": "stream_handoff_agent_response.save_user_message",
                        "notebook_id": request.notebook_id,
                    },
                )

        # Check if using Claude Code authentication
        use_claude_sdk = await is_using_claude_code_auth(request.llm_connection_id, session)

        # Check if this is the first message by checking session history (skip in preview mode)
        title_generation_task = None
        is_first_message = False
        title_generation_triggered = False
        partial_assistant_response = ""
        TITLE_TRIGGER_CHARS = 100

        if request.notebook_id and not request.is_preview:
            try:
                message_repo = MessageRepository(session)
                existing_messages = await message_repo.get_recent_messages(str(request.notebook_id), limit=2)
                is_first_message = len(existing_messages) <= 1
            except Exception as msg_check_error:
                logger.warning(f"Failed to check message count for notebook {request.notebook_id}: {msg_check_error}")
                is_first_message = False

        logger.info(f"[ROUTING CHECK] llm_connection_id={request.llm_connection_id}, use_claude_sdk={use_claude_sdk}")

        # Track if title has been emitted
        title_emitted = False

        async def trigger_title_generation(user_msg: str, partial_response: str):
            """Trigger title generation with user message + partial assistant response"""
            try:
                generated_title = await generate_notebook_title(
                    user_message=user_msg,
                    assistant_response=partial_response,
                    llm_connection_id=request.llm_connection_id,
                    model=request.model,
                    session=session,
                    use_claude_sdk=use_claude_sdk,
                )

                notebook_repo = NotebookRepository(session)
                await notebook_repo.update(
                    request.notebook_id,
                    {"notebook_name": generated_title},
                )
                await session.commit()

                logger.info(
                    f"[TITLE GENERATION] Generated and saved title for notebook {request.notebook_id}: {generated_title}"
                )
                return generated_title
            except Exception as title_error:
                logger.error(
                    f"[TITLE GENERATION] Error generating title: {title_error}",
                    posthog_context={
                        "function": "stream_handoff_agent_response.parallel_title_gen",
                        "notebook_id": request.notebook_id,
                    },
                )
                return None

        if use_claude_sdk:
            logger.info("🚀 USING CLAUDE AGENT SDK (Claude Code Authentication)")

            claude_assistant_response = ""
            claude_was_cancelled = False
            captured_claude_session_id = None
            claude_stream_task: asyncio.Task | None = None

            # Load existing Claude session ID for conversation continuity
            resume_session_id = None
            if request.notebook_id:
                try:
                    notebook_repo = NotebookRepository(session)
                    notebook = await notebook_repo.get(request.notebook_id)
                    if notebook and notebook.claude_session_id:
                        resume_session_id = notebook.claude_session_id
                        logger.info(f"[CLAUDE SDK] Found existing session ID: {resume_session_id}")
                    else:
                        logger.info("[CLAUDE SDK] No existing session ID - will create new session")
                except Exception as session_load_error:
                    logger.error(f"[CLAUDE SDK] Error loading session ID: {session_load_error}")

            try:
                instructions = get_unified_agent_prompt_compact(
                    database_schemas=database_schemas,
                    model=request.model,
                    plan_mode=request.plan_mode,
                    instructions=notebook_memory,
                    learnings=relevant_learnings,
                )

                github_repos = await _load_github_repos_for_agent(tenant_id, user_id, session)
                github_token = await _get_github_token_for_agent(tenant_id, user_id, session)
                local_repos = await _load_local_repos_for_agent(tenant_id, user_id, session)

                tools = _build_agent_tools(
                    database_schemas,
                    plan_mode=request.plan_mode,
                    has_github_repos=bool(github_repos),
                    has_local_repos=bool(local_repos),
                )

                instructions, enabled_skills, enabled_skill_names, custom_skills = await _load_skills_for_agent(
                    tenant_id, user_id, session, tools, instructions
                )

                if github_repos:
                    instructions += _build_github_repos_hint(github_repos)
                if local_repos:
                    instructions += _build_local_repos_hint(local_repos)

                logger.info(f"Claude SDK: {len(tools)} tools prepared for multi-turn execution")

                context = {
                    "llm_connection_id": request.llm_connection_id,
                    "model": request.model,
                    "db_type": request.db_type,
                    "notebook_id": request.notebook_id,
                    "current_version": request.current_version,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "enabled_skill_names": enabled_skill_names,
                    "redaction_rules": redaction_rules,
                    "plan_mode": request.plan_mode,
                    "plan_started": request.plan_mode and bool(request.notebook_id) and not is_first_message,
                }
                _add_skill_credentials_to_context(context, enabled_skills, custom_skills)

                if github_repos:
                    context["github_repos"] = github_repos
                    context["github_token"] = github_token
                if local_repos:
                    context["local_repos"] = local_repos

                # Add database_schemas to context for tool instructions
                if database_schemas:
                    context["database_schemas"] = database_schemas

                claude_queue: asyncio.Queue[dict | object] = asyncio.Queue()
                claude_stream_done = object()

                async def run_claude_stream():
                    try:
                        async for event in stream_claude_with_mcp_tools(
                            prompt=request.message,
                            tools=tools,
                            model=request.model,
                            instructions=instructions,
                            context=context,
                            session_id=session_notebook_id,
                            agent_session=agent_session,
                            resume_session_id=resume_session_id,
                            attachments=request.attachments,
                        ):
                            await claude_queue.put(event)
                    except Exception as stream_error:
                        logger.error(f"[CLAUDE MCP] Stream task error: {stream_error}", exc_info=True)
                        await claude_queue.put({"type": "error", "text": f"Claude Agent SDK error: {stream_error}"})
                    finally:
                        await claude_queue.put(claude_stream_done)

                claude_stream_task = asyncio.create_task(run_claude_stream(), name="claude_stream_worker")

                while True:
                    event = await claude_queue.get()
                    if event is claude_stream_done:
                        break
                    if not isinstance(event, dict):
                        continue
                    if (
                        is_first_message
                        and not title_generation_triggered
                        and len(partial_assistant_response.strip()) >= TITLE_TRIGGER_CHARS
                        and request.llm_connection_id
                    ):
                        # Use up to 100 chars
                        title_response_snippet = partial_assistant_response[:TITLE_TRIGGER_CHARS]
                        title_generation_task = asyncio.create_task(
                            trigger_title_generation(request.message, title_response_snippet)
                        )
                        title_generation_triggered = True

                    if title_generation_task and not title_emitted:
                        if title_generation_task.done():
                            try:
                                generated_title = title_generation_task.result()
                                if generated_title:
                                    title_event = json.dumps(
                                        {
                                            "type": "title_generation",
                                            "title": generated_title,
                                            "thread_id": str(request.notebook_id),
                                        },
                                        ensure_ascii=False,
                                    )
                                    yield f"data: {title_event}\n\n"
                                    title_emitted = True
                            except Exception as title_emit_error:
                                logger.error(f"[TITLE GENERATION] Error emitting title: {title_emit_error}")
                                title_emitted = True

                    if event.get("type") == "claude_session_id":
                        captured_claude_session_id = event.get("session_id")
                        # Only log if it's actually a new session
                        if captured_claude_session_id != resume_session_id:
                            logger.info(f"[CLAUDE SDK] Captured new session ID: {captured_claude_session_id}")
                        continue

                    # Capture full response for database save
                    if event.get("type") == "response_complete":
                        claude_assistant_response = event.get("full_response", "")
                        continue

                    # Events already in SSE format, just wrap in data: envelope
                    event_json = json.dumps(event, ensure_ascii=False)
                    yield f"data: {event_json}\n\n"

                    if event.get("type") == "content":
                        text = event.get("text", "")
                        claude_assistant_response += text

                        if is_first_message and not title_generation_triggered:
                            partial_assistant_response += text

            except asyncio.CancelledError:
                claude_was_cancelled = True
                logger.info("[CLAUDE MCP] Request cancelled by client for notebook %s", request.notebook_id)

            except Exception as claude_error:
                error_msg = f"Claude Agent SDK error: {claude_error}"
                logger.error(error_msg, exc_info=True)
                event_data = json.dumps({"type": "error", "text": error_msg}, ensure_ascii=False)
                yield f"data: {event_data}\n\n"

            finally:
                if claude_stream_task:
                    claude_stream_task.cancel()
                    try:
                        await asyncio.shield(claude_stream_task)
                    except asyncio.CancelledError:
                        pass
                    except Exception as stream_close_error:
                        logger.warning(f"[CLAUDE MCP] Stream task close warning: {stream_close_error}")

                # On cancellation, skip expensive operations and clean up quickly
                if claude_was_cancelled:
                    if title_generation_task and not title_generation_task.done():
                        title_generation_task.cancel()
                        try:
                            await title_generation_task
                        except asyncio.CancelledError:
                            pass

                    if request.notebook_id and thread_id and not request.is_preview:
                        try:

                            async def save_interrupted_message():
                                await session.rollback()

                                metadata = {
                                    "interrupted": True,
                                    "partial_response": True,
                                }
                                response_content = claude_assistant_response.strip() or "[Response interrupted]"
                                await MessageService.save_agent_assistant_message(
                                    session,
                                    thread_id,
                                    response_content,
                                    request.db_type,
                                    metadata_extra=metadata,
                                )
                                await session.commit()

                            await asyncio.shield(save_interrupted_message())
                            logger.info(
                                "[CLAUDE MCP] Saved partial response (interrupted) for notebook %s in thread %s (length: %d)",
                                request.notebook_id,
                                thread_id,
                                len(claude_assistant_response),
                            )
                        except Exception as save_exc:
                            logger.error(
                                "[CLAUDE MCP] Error saving assistant message on abort: %s",
                                save_exc,
                                exc_info=True,
                                posthog_context={
                                    "function": "stream_handoff_agent_response.claude_save_on_abort",
                                    "notebook_id": request.notebook_id,
                                    "thread_id": thread_id,
                                },
                            )
                    return

                # Normal completion path (not cancelled)
                if (
                    is_first_message
                    and not title_generation_triggered
                    and request.llm_connection_id
                    and partial_assistant_response.strip()
                ):
                    title_generation_task = asyncio.create_task(
                        trigger_title_generation(request.message, partial_assistant_response)
                    )
                    title_generation_triggered = True

                if title_generation_task and not title_emitted:
                    try:
                        generated_title = await title_generation_task
                        if generated_title:
                            try:
                                title_event = json.dumps(
                                    {
                                        "type": "title_generation",
                                        "title": generated_title,
                                        "thread_id": str(request.notebook_id),
                                    },
                                    ensure_ascii=False,
                                )
                                yield f"data: {title_event}\n\n"
                                title_emitted = True
                            except (asyncio.CancelledError, GeneratorExit):
                                pass
                    except Exception as title_wait_error:
                        logger.error(f"[TITLE GENERATION] Error waiting for title: {title_wait_error}")

                if request.notebook_id and thread_id and claude_assistant_response.strip() and not request.is_preview:
                    try:
                        metadata = {
                            "interrupted": claude_was_cancelled,
                            "partial_response": claude_was_cancelled,
                        }

                        await MessageService.save_agent_assistant_message(
                            session,
                            thread_id,
                            claude_assistant_response,
                            request.db_type,
                            metadata_extra=metadata,
                        )

                        # 2. Store Claude session ID for conversation continuity
                        # Only store if it's a new session ID
                        if captured_claude_session_id and captured_claude_session_id != resume_session_id:
                            try:
                                notebook_repo = NotebookRepository(session)
                                await notebook_repo.update(
                                    request.notebook_id, {"claude_session_id": captured_claude_session_id}
                                )
                                await session.commit()
                                logger.info(
                                    f"[CLAUDE SDK] Stored new session ID {captured_claude_session_id} for notebook {request.notebook_id}"
                                )
                            except Exception as session_store_error:
                                logger.error(f"[CLAUDE SDK] Failed to store session ID: {session_store_error}")

                        # The SDK stores sessions in ~/.claude/ and loads them automatically
                        logger.info(
                            "[CLAUDE MCP] Saved %s response for notebook %s (length: %d)",
                            "partial" if claude_was_cancelled else "complete",
                            request.notebook_id,
                            len(claude_assistant_response),
                        )

                    except Exception as save_exc:
                        logger.error(
                            "[CLAUDE MCP] Error saving assistant message: %s",
                            save_exc,
                            posthog_context={
                                "function": "stream_handoff_agent_response.claude_save_assistant",
                                "notebook_id": request.notebook_id,
                                "thread_id": thread_id,
                            },
                        )

                return

        # OpenAI Agents SDK path
        try:
            agent, enabled_skills, enabled_skill_names, custom_skills = await create_unified_agent(
                llm_connection_id=request.llm_connection_id,
                model=request.model,
                notebook_id=request.notebook_id,
                database_schemas=database_schemas if database_schemas else None,
                tenant_id=tenant_id,
                user_id=user_id,
                session=session,
                plan_mode=request.plan_mode,
                memory=notebook_memory,
                learnings=relevant_learnings,
            )
        except Exception as agent_error:
            error_msg = f"Failed to create unified agent: {agent_error}"
            logger.error(
                error_msg,
                posthog_context={
                    "function": "stream_handoff_agent_response.create_agent",
                    "notebook_id": request.notebook_id,
                    "llm_connection_id": request.llm_connection_id,
                    "model": request.model,
                    "database_count": len(database_schemas),
                },
            )
            event_data = json.dumps({"type": "error", "text": error_msg}, ensure_ascii=False)
            yield f"data: {event_data}\n\n"
            return

        # Send progress event after agent creation
        progress_event = json.dumps(
            {"type": "progress", "message": "Initializing session..."},
            ensure_ascii=False,
        )
        yield f"data: {progress_event}\n\n"

        context: dict[str, Any] = {
            "llm_connection_id": request.llm_connection_id,
            "model": request.model,
            "db_type": request.db_type,
            "notebook_id": request.notebook_id,
            "current_version": request.current_version,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "enabled_skill_names": enabled_skill_names,
            "redaction_rules": redaction_rules,
            "plan_mode": request.plan_mode,
            "plan_started": request.plan_mode and bool(request.notebook_id) and not is_first_message,
        }
        _add_skill_credentials_to_context(context, enabled_skills, custom_skills)

        github_repos = await _load_github_repos_for_agent(tenant_id, user_id, session)
        if github_repos:
            context["github_repos"] = github_repos
            github_token = await _get_github_token_for_agent(tenant_id, user_id, session)
            context["github_token"] = github_token

        local_repos = await _load_local_repos_for_agent(tenant_id, user_id, session)
        if local_repos:
            context["local_repos"] = local_repos

        # Send progress event before starting stream
        progress_event = json.dumps({"type": "progress", "message": "Starting analysis..."}, ensure_ascii=False)
        yield f"data: {progress_event}\n\n"

        # ============================================
        # TIER 1: SMART PRUNING (runs at START of turn, before LLM call)
        # TIER 1.5: COMPACTION (triggers at 60% context usage)
        # ============================================
        try:
            from server.utils.model_limits import (
                get_model_context_limit,
                should_trigger_compaction,
                should_trigger_handoff,
            )
            from server.utils.token_estimator import estimate_messages_tokens_fast

            # Get conversation state before pruning
            session_items_before = await agent_session.get_items()

            if session_items_before:  # Only if there's existing history
                session_messages_before = agent_session._items_to_messages_for_counting(session_items_before)
                tokens_before = estimate_messages_tokens_fast(session_messages_before)

                # Count tool outputs before pruning
                tool_outputs_before = [
                    item
                    for item in session_items_before
                    if (isinstance(item, dict) and item.get("role") == "tool")
                    or (hasattr(item, "role") and item.role == "tool")
                ]

                model_for_limit = request.model or "gpt-4"
                context_limit = get_model_context_limit(model_for_limit)
                usage_pct = (tokens_before / context_limit * 100) if context_limit > 0 else 0

                logger.info(
                    f"[CONTEXT START] Notebook {request.notebook_id}: "
                    f"{tokens_before:,} tokens ({usage_pct:.1f}% of {context_limit:,}), "
                    f"{len(session_items_before)} items, "
                    f"{len(tool_outputs_before)} tool outputs"
                )

                # Tier 1: Prune old tool outputs BEFORE LLM call
                prune_stats = await agent_session.prune_tool_outputs(dry_run=False)

                if prune_stats.get("pruning_performed"):
                    tokens_saved = prune_stats.get("tokens_saved", 0)
                    outputs_pruned = prune_stats.get("outputs_pruned", 0)
                    tokens_after_prune = prune_stats.get("tokens_after", tokens_before)

                    logger.info(
                        f"[TIER 1 PRUNE] Notebook {request.notebook_id}: "
                        f"Pruned {outputs_pruned} tool outputs, "
                        f"saved {tokens_saved:,} tokens "
                        f"({tokens_before:,} → {tokens_after_prune:,})"
                    )
                    tokens_before = tokens_after_prune  # Update for Tier 1.5 check
                else:
                    logger.debug(
                        f"[TIER 1 SKIP] Notebook {request.notebook_id}: "
                        f"No pruning needed (insufficient savings or below threshold)"
                    )

                # Tier 1.5: Compaction at 60% threshold
                # Replaces tool call results with placeholders for aggressive savings
                if should_trigger_compaction(tokens_before, model_for_limit):
                    logger.info(
                        f"[TIER 1.5 TRIGGER] Notebook {request.notebook_id}: "
                        f"{tokens_before:,} tokens ({usage_pct:.1f}%) exceeds 60% threshold - running compaction"
                    )

                    compact_stats = await agent_session.compact_conversation(
                        model=model_for_limit,
                        dry_run=False,
                    )

                    if compact_stats.get("compaction_performed"):
                        tokens_saved_compact = compact_stats.get("tokens_saved", 0)
                        tokens_after_compact = compact_stats.get("tokens_after", tokens_before)
                        items_redacted = compact_stats.get("items_redacted", 0)

                        logger.info(
                            f"[TIER 1.5 COMPLETED] Notebook {request.notebook_id}: "
                            f"Compacted {items_redacted} tool results to placeholders, "
                            f"saved {tokens_saved_compact:,} tokens "
                            f"({tokens_before:,} → {tokens_after_compact:,})"
                        )
                    else:
                        logger.debug(
                            f"[TIER 1.5 SKIP] Notebook {request.notebook_id}: Compaction did not perform changes"
                        )
                else:
                    logger.debug(
                        f"[TIER 1.5 SKIP] Notebook {request.notebook_id}: "
                        f"Context usage below 60% threshold, compaction not needed"
                    )
            else:
                logger.debug(f"[CONTEXT START] Notebook {request.notebook_id}: No existing history, starting fresh")

        except Exception as context_mgmt_error:
            # Context management is non-critical, log but don't fail
            logger.error(
                f"Context management error (start) for notebook {request.notebook_id}: {context_mgmt_error}",
                exc_info=True,
                posthog_context={
                    "function": "stream_handoff_agent_response.context_management_start",
                    "notebook_id": request.notebook_id,
                },
            )
        # ============================================
        # END TIER 1 & TIER 1.5 CONTEXT MANAGEMENT
        # ============================================

        # Build message content following multimodal pattern
        # Input must be a list of MESSAGE objects (with role and content), not just content blocks
        # Use "input_text" and "input_image" types for Agents SDK (Responses API format)
        content_blocks: list[dict] = [{"type": "input_text", "text": request.message}]

        if request.attachments:
            logger.info(f"Processing {len(request.attachments)} attachments for notebook {request.notebook_id}")

            # Add images to content blocks
            for attachment in request.attachments:
                # Create data URI format required by OpenAI SDK
                data_uri = f"data:{attachment['mime_type']};base64,{attachment['file_data']}"

                # Use Agents SDK format: type="input_image" with image_url as string
                content_blocks.append(
                    {
                        "type": "input_image",
                        "image_url": data_uri,  # Direct string, not nested object
                        "detail": "high",
                    }
                )
                logger.info(f"Added image attachment: {attachment['file_name']} ({attachment['mime_type']})")

        # Wrap content blocks in a user message
        # Input should be: [{"role": "user", "content": [content blocks]}]
        message_content = [{"role": "user", "content": content_blocks}]

        try:
            # Lazy import Runner for streaming (Agent already imported in create_unified_agent)
            from agents import RunItemStreamEvent, Runner

            # Unified approach: Always use RunConfig with session_input_callback
            # This works for both text-only and multimodal (text + images) inputs
            logger.info(
                "Processing message with %d content parts (text + %d images) for notebook %s",
                len(content_blocks),
                len(request.attachments) if request.attachments else 0,
                request.notebook_id,
            )

            # Create RunConfig with the session_input_callback
            # This tells the SDK how to merge new inputs with conversation history
            run_cfg = RunConfig(session_input_callback=merge_multimodal_input_with_history)

            # Single unified call for all input types
            streaming_result = Runner.run_streamed(
                agent,
                input=message_content,  # List of content parts (always list format)
                context=context,
                session=agent_session,  # Session memory enabled for all messages
                run_config=run_cfg,  # Callback handles merging with history
                max_turns=50,
            )
        except Exception as runner_error:
            error_msg = f"Failed to initialize streaming runner: {runner_error}"
            logger.error(
                error_msg,
                posthog_context={
                    "function": "stream_handoff_agent_response.init_runner",
                    "notebook_id": request.notebook_id,
                    "llm_connection_id": request.llm_connection_id,
                    "model": request.model,
                    "db_type": request.db_type,
                },
            )
            event_data = json.dumps({"type": "error", "text": error_msg}, ensure_ascii=False)
            yield f"data: {event_data}\n\n"
            return

        model_name = (request.model or "").lower()
        is_gpt5 = "gpt-5" in model_name
        is_sonnet = "sonnet" in model_name
        in_reasoning_mode = False

        try:
            event_stream = streaming_result.stream_events()
        except Exception as stream_init_error:
            error_msg = f"Failed to initialize event stream: {stream_init_error}"
            logger.error(
                error_msg,
                posthog_context={
                    "function": "stream_handoff_agent_response.init_event_stream",
                    "notebook_id": request.notebook_id,
                    "llm_connection_id": request.llm_connection_id,
                    "model": request.model,
                    "db_type": request.db_type,
                },
            )
            event_data = json.dumps({"type": "error", "text": error_msg}, ensure_ascii=False)
            yield f"data: {event_data}\n\n"
            return

        async for event in event_stream:
            try:
                if (
                    is_first_message
                    and not title_generation_triggered
                    and len(partial_assistant_response.strip()) >= TITLE_TRIGGER_CHARS
                    and request.llm_connection_id
                ):
                    # Use up to 100 chars
                    title_response_snippet = partial_assistant_response[:TITLE_TRIGGER_CHARS]
                    title_generation_task = asyncio.create_task(
                        trigger_title_generation(request.message, title_response_snippet)
                    )
                    title_generation_triggered = True

                if title_generation_task and not title_emitted and title_generation_task.done():
                    try:
                        generated_title = title_generation_task.result()
                        if generated_title:
                            title_event = json.dumps(
                                {
                                    "type": "title_generation",
                                    "title": generated_title,
                                    "thread_id": str(request.notebook_id),
                                },
                                ensure_ascii=False,
                            )
                            yield f"data: {title_event}\n\n"
                            title_emitted = True
                    except Exception as title_emit_error:
                        logger.error(f"[TITLE GENERATION] Error emitting title: {title_emit_error}")
                        title_emitted = True

                if hasattr(event, "type"):
                    etype = event.type

                    if etype in ["reasoning_item_created", "reasoning_started"]:
                        in_reasoning_mode = True
                        continue
                    elif etype in ["reasoning_item_done", "reasoning_completed"]:
                        in_reasoning_mode = False
                        continue

                    if is_sonnet:
                        if etype == "content_block_start" and getattr(getattr(event, "data", None), "type", None) in [
                            "thinking",
                            "redacted_thinking",
                        ]:
                            in_reasoning_mode = True
                            continue
                        elif etype == "content_block_stop" and getattr(getattr(event, "data", None), "type", None) in [
                            "thinking",
                            "redacted_thinking",
                        ]:
                            in_reasoning_mode = False
                            continue

                if in_reasoning_mode:
                    continue

                event_data_type = getattr(getattr(event, "data", None), "type", None)
                if event_data_type:
                    if event_data_type in {
                        "reasoning_text.delta",
                        "reasoning_text.done",
                        "analysis",
                        "reasoning",
                        "reasoning.delta",
                        "reasoning.done",
                        "internal_monologue",
                        "thinking",
                        "thinking_delta",
                        "redacted_thinking",
                        "reasoning_content",
                    }:
                        continue

                    if (is_gpt5 or is_sonnet) and event_data_type not in {
                        "output_text.delta",
                        "output_text",
                        "output_text.done",
                        "message.delta",
                        "response.output_text.delta",
                        "response.output_text.done",
                        "text",  # Allow text-type responses (fallback)
                        "text.delta",  # Allow text delta responses
                        "response.text.delta",  # Allow response text deltas
                    }:
                        continue

                if isinstance(event, RunItemStreamEvent):
                    item = event.item

                    if item.type == "tool_call_item":
                        tool_name, arguments, tool_description = (
                            "Unknown",
                            None,
                            "Unknown tool",
                        )
                        if getattr(item, "raw_item", None):
                            tool_name = getattr(item.raw_item, "name", tool_name)
                            arguments = getattr(item.raw_item, "arguments", None)

                        # Get friendly description for the tool
                        tool_friendly_description = get_user_friendly_tool_description(tool_name)

                        # Track the last tool name and args for output event handling
                        last_tool_name = tool_name
                        if isinstance(arguments, str):
                            try:
                                last_tool_args = json.loads(arguments)
                            except (json.JSONDecodeError, TypeError):
                                last_tool_args = None
                        elif isinstance(arguments, dict):
                            last_tool_args = arguments
                        else:
                            last_tool_args = None
                        tool_runtime_start = perf_counter()
                        tool_runtime_name = tool_name

                        if tool_name == "emit_plan_status" and last_tool_args:
                            plan_event = {
                                "type": "plan_status",
                                "action": last_tool_args.get("action", ""),
                                "notebook_id": str(request.notebook_id) if request.notebook_id else None,
                            }
                            action = last_tool_args.get("action", "")
                            if action == "start_plan" and last_tool_args.get("steps_json"):
                                try:
                                    steps = json.loads(last_tool_args["steps_json"])
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
                                plan_event["step_number"] = last_tool_args.get("step_number", 0)
                            yield f"data: {json.dumps(plan_event, ensure_ascii=False)}\n\n"

                        if tool_name in HTML_CONTEXT_FETCH_TOOLS:
                            html_context_request_id = str(uuid4())
                            logger.info(
                                "HTML context fetch started (tool=%s, notebook=%s, context_id=%s)",
                                tool_name,
                                request.notebook_id,
                                html_context_request_id,
                            )
                            context_event = json.dumps(
                                {
                                    "type": "html_context_refresh",
                                    "stage": "start",
                                    "message": "Fetching latest dashboard HTML...",
                                    "tool_name": tool_name,
                                    "tool_friendly_description": tool_friendly_description,
                                    "context_id": html_context_request_id,
                                    "edit_session_id": html_edit_session_id,
                                    "is_initial_fetch": not html_was_edited,
                                },
                                ensure_ascii=False,
                            )
                            yield f"data: {context_event}\n\n"

                        if tool_name in HTML_EDIT_START_TOOLS:
                            logger.info(
                                "HTML edit tool started (tool=%s, notebook=%s)",
                                tool_name,
                                request.notebook_id,
                            )
                            if not html_edit_session_id:
                                html_edit_session_id = str(uuid4())
                            summary_payload = _summarize_html_tool_args(tool_name, arguments)
                            if not html_was_edited:
                                html_was_edited = True
                            logger.info(f"HTML edit detected - tool: {tool_name}. Sending html_edit_detected event.")
                            html_edit_flag = json.dumps(
                                {
                                    "type": "html_edit_detected",
                                    "message": "Applying changes to HTML...",
                                    "tool_name": tool_name,
                                    "tool_friendly_description": tool_friendly_description,
                                    "edit_session_id": html_edit_session_id,
                                },
                                ensure_ascii=False,
                            )
                            yield f"data: {html_edit_flag}\n\n"

                            if summary_payload:
                                patch_event = json.dumps(
                                    {
                                        "type": "html_edit_patch",
                                        "tool_name": tool_name,
                                        "tool_friendly_description": tool_friendly_description,
                                        "edit_session_id": html_edit_session_id,
                                        "payload": summary_payload,
                                    },
                                    ensure_ascii=False,
                                )
                                yield f"data: {patch_event}\n\n"
                        if getattr(item, "agent", None) and getattr(item.agent, "tools", None):
                            tool = next(
                                (t for t in item.agent.tools if getattr(t, "name", None) == tool_name),
                                None,
                            )
                            if tool:
                                tool_description = getattr(tool, "description", tool_description)

                        logger.info(
                            "tool_call_item: %s - %s | raw_arguments=%r | parsed_arguments=%r",
                            tool_name,
                            tool_description,
                            arguments,
                            last_tool_args,
                        )

                        tool_call_id = f"tool_{hash(f'{tool_name}_{arguments}_{len(assistant_response)}') % 10000}"

                        # Only return arguments for query execution/saving tools
                        skill_description_override = None
                        if tool_name in [
                            "execute_mongo_query",
                            "execute_sql_query",
                            "execute_duckdb_query",
                            "save_query",
                        ]:
                            args_json = json.dumps(arguments) if arguments else "{}"
                        elif tool_name == "get_skill_definition" and arguments:
                            skill_name = arguments.get("skill_name", "")
                            skill_description_override = f"Loading {skill_name} skill documentation"
                            args_json = "{}"
                        elif tool_name == "update_custom_skill" and arguments:
                            skill_name = arguments.get("skill_name", "")
                            skill_description_override = f"Updating {skill_name} skill"
                            args_json = "{}"
                        elif tool_name == "execute_skill_api" and arguments:
                            from server.services.skill_discovery import SkillDiscovery

                            skill_name = arguments.get("skill_name", "")
                            skill_config = SkillDiscovery.get_skill_config(skill_name)
                            display_name = skill_config.display_name if skill_config else skill_name
                            emoji = skill_config.emoji if skill_config else "🔧"
                            is_graphql = arguments.get("is_graphql", False)
                            api_type = "GraphQL" if is_graphql else "REST"
                            display_info = {
                                "skill_name": skill_name,
                                "display_name": display_name,
                                "emoji": emoji,
                                "is_graphql": is_graphql,
                                "url": arguments.get("url", ""),
                                "method": arguments.get("method", "GET"),
                            }
                            if is_graphql:
                                if arguments.get("graphql_query"):
                                    display_info["query"] = arguments.get("graphql_query")
                            else:
                                if arguments.get("body"):
                                    display_info["body"] = arguments.get("body")
                            args_json = json.dumps(display_info)
                            skill_description_override = f"Executing {display_name} API"
                        else:
                            args_json = json.dumps(arguments) if arguments else "{}"

                        clean_description = skill_description_override or tool_friendly_description
                        tool_marker = f"[[TOOL_CALL:{tool_call_id}:{tool_name}|{clean_description}|{args_json}]]"

                        assistant_response += tool_marker
                        yield f"data: {json.dumps({'type': 'content', 'text': tool_marker, 'tool_friendly_description': tool_friendly_description}, ensure_ascii=False)}\n\n"

                    elif item.type == "tool_call_output_item":
                        tool_output_seen = True
                        output_markdown = "\n\nTool executed successfully\n\n"

                        assistant_response += output_markdown
                        yield f"data: {json.dumps({'type': 'content', 'text': output_markdown}, ensure_ascii=False)}\n\n"

                        if tool_runtime_start is not None and tool_runtime_name:
                            duration = perf_counter() - tool_runtime_start
                            logger.info(
                                "Tool %s completed in %.2fs (notebook=%s)",
                                tool_runtime_name,
                                duration,
                                request.notebook_id,
                            )
                            tool_runtime_start = None
                            tool_runtime_name = None

                        if last_tool_name in HTML_CONTEXT_FETCH_TOOLS and html_context_request_id:
                            context_complete_event = json.dumps(
                                {
                                    "type": "html_context_refresh",
                                    "stage": "complete",
                                    "message": "Latest dashboard HTML loaded",
                                    "tool_name": last_tool_name,
                                    "context_id": html_context_request_id,
                                    "edit_session_id": html_edit_session_id,
                                },
                                ensure_ascii=False,
                            )
                            yield f"data: {context_complete_event}\n\n"
                            logger.info(
                                "HTML context fetch completed (tool=%s, notebook=%s, context_id=%s)",
                                last_tool_name,
                                request.notebook_id,
                                html_context_request_id,
                            )
                            html_context_request_id = None

                        # Check if the last tool was save_query and emit query_saved event
                        if last_tool_name == "save_query":
                            query_saved_flag = json.dumps(
                                {
                                    "type": "query_saved",
                                    "message": "Query saved successfully",
                                },
                                ensure_ascii=False,
                            )
                            yield f"data: {query_saved_flag}\n\n"

                        if last_tool_name in ("add_instruction", "remove_instruction"):
                            memory_updated_event = json.dumps({"type": "memory_updated"}, ensure_ascii=False)
                            yield f"data: {memory_updated_event}\n\n"

                        if last_tool_name in ("add_learning", "update_learning", "remove_learning"):
                            learning_updated_event = json.dumps({"type": "learning_updated"}, ensure_ascii=False)
                            yield f"data: {learning_updated_event}\n\n"

                        # Check if the last tool was an HTML edit tool and emit html_edit_complete event
                        if last_tool_name in HTML_EDIT_COMPLETE_TOOLS:
                            html_edit_completed = True
                            logger.info(
                                f"HTML edit complete - tool: {last_tool_name}. Sending html_edit_complete event."
                            )
                            html_complete_flag = json.dumps(
                                {
                                    "type": "html_edit_complete",
                                    "message": "HTML edit applied successfully",
                                    "tool_name": last_tool_name,
                                    "edit_session_id": html_edit_session_id,
                                },
                                ensure_ascii=False,
                            )
                            yield f"data: {html_complete_flag}\n\n"
                            # Reset flag to allow subsequent edits to trigger the event
                            html_edit_completed = False
                            html_edit_session_id = None

                        # Check if the last tool was generate_dashboard_screenshot and emit dashboard_screenshot event
                        if last_tool_name == "generate_dashboard_screenshot" and last_tool_args:
                            try:
                                tool_result_raw = item.raw_item.output if hasattr(item, "raw_item") else None
                                if tool_result_raw:
                                    result_data = json.loads(tool_result_raw)
                                    if result_data.get("success") and result_data.get("image_url"):
                                        screenshot_event = json.dumps(
                                            {
                                                "type": "dashboard_screenshot",
                                                "message": "Dashboard screenshot generated",
                                                "image_url": result_data["image_url"],
                                                "size_bytes": result_data.get("size_bytes", 0),
                                            },
                                            ensure_ascii=False,
                                        )
                                        yield f"data: {screenshot_event}\n\n"
                                        logger.info("Dashboard screenshot event emitted (OpenAI path)")
                            except Exception as e:
                                logger.warning(f"Failed to emit dashboard_screenshot event: {e}")

                        # Emit datasource_selected when query execution tools complete
                        if (
                            last_tool_name
                            in {
                                "execute_sql_query",
                                "execute_mongo_query",
                                "execute_duckdb_query",
                            }
                            and last_tool_args
                        ):
                            connection_id = last_tool_args.get("connection_id")
                            dataset_id = last_tool_args.get("dataset_id")
                            datasource_id = connection_id or dataset_id
                            if datasource_id:
                                datasource_name = ""
                                datasource_type = ""
                                try:
                                    from server.db.session import get_async_session
                                    from server.repositories.datasets import DatasetRepository

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
                                    logger.warning(f"Could not fetch datasource info: {ds_err}")
                                datasource_selected_event = json.dumps(
                                    {
                                        "type": "datasource_selected",
                                        "datasource_id": str(datasource_id),
                                        "datasource_name": datasource_name,
                                        "datasource_type": datasource_type,
                                    },
                                    ensure_ascii=False,
                                )
                                yield f"data: {datasource_selected_event}\n\n"
                                logger.info(
                                    "Datasource selected event emitted (datasource=%s, tool=%s)",
                                    datasource_id,
                                    last_tool_name,
                                )

                elif hasattr(event, "type") and event.type == "raw_response_event" and hasattr(event, "data"):
                    data = event.data

                    # Handle various response formats from the Agents SDK
                    data_type = getattr(data, "type", None)

                    # Handle output_text (standard response from SDK)
                    if data_type == "output_text":
                        text_content = getattr(data, "text", None)
                        if text_content is not None and text_content != "":
                            assistant_response += text_content
                            if is_first_message and not title_generation_triggered:
                                partial_assistant_response += text_content
                            yield f"data: {json.dumps({'type': 'content', 'text': text_content}, ensure_ascii=False)}\n\n"
                            continue

                    # Handle text-type responses (fallback for multimodal format)
                    if data_type == "text":
                        text_content = getattr(data, "text", None)
                        if text_content is not None and text_content != "":
                            assistant_response += text_content
                            if is_first_message and not title_generation_triggered:
                                partial_assistant_response += text_content
                            yield f"data: {json.dumps({'type': 'content', 'text': text_content}, ensure_ascii=False)}\n\n"
                            continue

                    # Handle text delta streaming (for chunked responses)
                    if data_type in (
                        "text.delta",
                        "output_text.delta",
                        "response.text.delta",
                        "response.output_text.delta",
                    ):
                        text_delta = getattr(data, "delta", None) or getattr(data, "text", None)
                        if text_delta is not None and text_delta != "":
                            assistant_response += text_delta
                            if is_first_message and not title_generation_triggered:
                                partial_assistant_response += text_delta
                            yield f"data: {json.dumps({'type': 'content', 'text': text_delta}, ensure_ascii=False)}\n\n"
                            continue

                    # Handle standard delta-based streaming
                    if getattr(data, "delta", None) is not None and data.delta != "":
                        delta = data.delta

                        if hasattr(data, "reasoning_content") and data.reasoning_content:
                            continue

                        if hasattr(data, "type"):
                            delta_type = getattr(data, "type", "")
                            if "function_call_arguments" in delta_type.lower():
                                continue
                            if "reasoning" in delta_type.lower() or "analysis" in delta_type.lower():
                                continue

                        if is_gpt5 or is_sonnet:
                            reasoning_patterns = [
                                "Alright, I've got the Orchestrator instructions",
                                "I need to",
                                "Let me",
                                "I'll",
                                "Looking at",
                                "Checking",
                                "The user",
                                "My response will be:",
                                "Greeting the user",
                                "Internal reasoning:",
                                "Thinking about",
                                "First, I'll",
                                "Now I need to",
                                "I should",
                            ]

                            for pattern in reasoning_patterns:
                                if delta.strip().startswith(pattern):
                                    continue

                        assistant_response += delta
                        if is_first_message and not title_generation_triggered:
                            partial_assistant_response += delta
                        yield f"data: {json.dumps({'type': 'content', 'text': delta}, ensure_ascii=False)}\n\n"

            except Exception as event_error:
                logger.error(
                    f"Error processing event: {event_error}",
                    posthog_context={
                        "function": "stream_handoff_agent_response.event_processing",
                        "notebook_id": request.notebook_id,
                        "llm_connection_id": request.llm_connection_id,
                        "model": request.model,
                        "db_type": request.db_type,
                        "event_type": (getattr(event, "type", None) if "event" in locals() else None),
                        "event_class": (type(event).__name__ if "event" in locals() else None),
                        "last_tool_name": last_tool_name,
                        "assistant_response_length": len(assistant_response),
                    },
                )
                continue

        # Completion messages (save happens in finally block)
        if not (assistant_response or "").strip():
            fallback = json.dumps({"type": "content", "text": "Done."}, ensure_ascii=False)
            yield f"data: {fallback}\n\n"

        logger.info(
            "Unified agent final response (length=%d): %s",
            len(assistant_response),
            _truncate_text(assistant_response, 2000),
        )
        if last_tool_name and not tool_output_seen:
            logger.warning(
                "Run finished without tool output after tool call (tool=%s, args=%r, notebook=%s)",
                last_tool_name,
                last_tool_args,
                request.notebook_id,
            )

        done_event = json.dumps({"type": "done"}, ensure_ascii=False)
        yield f"data: {done_event}\n\n"

    except asyncio.CancelledError:
        # Client disconnected - set flag and let finally block save partial response
        was_cancelled = True
        logger.info("Request cancelled by client for notebook %s", request.notebook_id)
        # Don't yield error event or try to save here - finally block will handle it
    except Exception as exc:
        error_msg = f"Error in handoff agent response: {exc}"
        logger.error(
            error_msg,
            posthog_context={
                "function": "stream_handoff_agent_response",
                "notebook_id": request.notebook_id,
                "llm_connection_id": request.llm_connection_id,
                "model": request.model,
                "db_type": request.db_type,
                "assistant_response_length": len(assistant_response),
            },
        )
        event_data = json.dumps({"type": "error", "text": error_msg}, ensure_ascii=False)
        yield f"data: {event_data}\n\n"
    finally:
        # On cancellation, skip expensive operations and clean up quickly
        if was_cancelled:
            if title_generation_task and not title_generation_task.done():
                title_generation_task.cancel()
                try:
                    await title_generation_task
                except asyncio.CancelledError:
                    pass

            if request.notebook_id and thread_id and not request.is_preview:
                try:

                    async def save_interrupted_message():
                        await session.rollback()

                        metadata = {
                            "interrupted": True,
                            "partial_response": True,
                        }
                        response_content = assistant_response.strip() or "[Response interrupted]"
                        await MessageService.save_agent_assistant_message(
                            session,
                            thread_id,
                            response_content,
                            request.db_type,
                            metadata_extra=metadata,
                        )
                        await session.commit()

                    await asyncio.shield(save_interrupted_message())
                    logger.info(
                        "Saved partial response (interrupted) for notebook %s in thread %s (length: %d)",
                        request.notebook_id,
                        thread_id,
                        len(assistant_response),
                    )
                except Exception as save_exc:
                    logger.error(
                        "Error saving assistant message on abort: %s",
                        save_exc,
                        posthog_context={
                            "function": "stream_handoff_agent_response.save_on_abort",
                            "notebook_id": request.notebook_id,
                            "thread_id": thread_id,
                        },
                    )
            return

        # Normal completion path (not cancelled)
        if (
            is_first_message
            and not title_generation_triggered
            and request.llm_connection_id
            and partial_assistant_response.strip()
        ):
            title_generation_task = asyncio.create_task(
                trigger_title_generation(request.message, partial_assistant_response)
            )
            title_generation_triggered = True

        if title_generation_task and not title_emitted:
            try:
                generated_title = await title_generation_task
                if generated_title:
                    try:
                        title_event = json.dumps(
                            {
                                "type": "title_generation",
                                "title": generated_title,
                                "thread_id": str(request.notebook_id),
                            },
                            ensure_ascii=False,
                        )
                        yield f"data: {title_event}\n\n"
                        title_emitted = True
                    except (asyncio.CancelledError, GeneratorExit):
                        pass
            except Exception as title_wait_error:
                logger.error(f"[TITLE GENERATION] Error waiting for title: {title_wait_error}")

        # Always save here - single save path for both interrupted and completed responses (skip in preview mode)
        if request.notebook_id and thread_id and not request.is_preview:
            if assistant_response.strip():
                try:
                    metadata = {
                        "interrupted": was_cancelled,
                        "partial_response": was_cancelled,
                    }

                    await MessageService.save_agent_assistant_message(
                        session,
                        thread_id,
                        assistant_response,
                        request.db_type,
                        metadata_extra=metadata,
                    )

                    if was_cancelled:
                        logger.info(
                            "Saved partial response (interrupted) for notebook %s in thread %s (length: %d)",
                            request.notebook_id,
                            thread_id,
                            len(assistant_response),
                        )
                    else:
                        logger.info(
                            "Saved complete response for notebook %s in thread %s",
                            request.notebook_id,
                            thread_id,
                        )

                        # ============================================
                        # TIER 2: SESSION HANDOFF CHECK (runs at END of turn)
                        # Note: Tier 1 pruning now runs at START of turn (before LLM call)
                        # ============================================
                        try:
                            from server.utils.model_limits import (
                                get_model_context_limit,
                                should_trigger_handoff,
                            )
                            from server.utils.token_estimator import (
                                estimate_messages_tokens_fast,
                            )

                            # Check if we need handoff after the turn
                            session_items = await agent_session.get_items()
                            session_messages = agent_session._items_to_messages_for_counting(session_items)
                            current_tokens = estimate_messages_tokens_fast(session_messages)

                            model_for_limit = request.model or "gpt-4"
                            context_limit = get_model_context_limit(model_for_limit)
                            usage_pct_after = (current_tokens / context_limit * 100) if context_limit > 0 else 0

                            logger.info(
                                f"[CONTEXT END] Notebook {request.notebook_id}: "
                                f"{current_tokens:,} tokens ({usage_pct_after:.1f}% of {context_limit:,})"
                            )

                            if should_trigger_handoff(current_tokens, model_for_limit):
                                logger.warning(
                                    f"[TIER 2 TRIGGER] Notebook {request.notebook_id}: "
                                    f"{current_tokens:,} tokens ({usage_pct_after:.1f}% of {context_limit:,}) "
                                    f"exceeds 90% threshold - initiating session handoff"
                                )

                                handoff_stats = await agent_session.session_handoff(
                                    model=model_for_limit,
                                    llm_connection_id=request.llm_connection_id,
                                    dry_run=False,
                                )

                                if handoff_stats.get("handoff_performed"):
                                    tokens_saved_handoff = handoff_stats.get("tokens_saved", 0)
                                    tokens_after_handoff = handoff_stats.get("tokens_after", current_tokens)

                                    logger.info(
                                        f"[TIER 2 COMPLETED] Notebook {request.notebook_id}: "
                                        f"Handoff saved {tokens_saved_handoff:,} tokens "
                                        f"({current_tokens:,} → {tokens_after_handoff:,}), "
                                        f"conversation reset with summary"
                                    )
                            else:
                                logger.debug(
                                    f"[TIER 2 CHECK END] Notebook {request.notebook_id}: "
                                    f"{current_tokens:,} tokens ({usage_pct_after:.1f}%) - "
                                    f"handoff not needed"
                                )

                        except Exception as context_mgmt_error:
                            # Context management is non-critical, log but don't fail
                            logger.error(
                                f"Context management error (end) for notebook {request.notebook_id}: {context_mgmt_error}",
                                exc_info=True,
                                posthog_context={
                                    "function": "stream_handoff_agent_response.context_management_end",
                                    "notebook_id": request.notebook_id,
                                },
                            )
                        # ============================================
                        # END TIER 2 HANDOFF CHECK
                        # ============================================

                except Exception as save_exc:
                    logger.error(
                        "Error saving assistant message: %s",
                        save_exc,
                        posthog_context={
                            "function": "stream_handoff_agent_response.save_assistant_message",
                            "notebook_id": request.notebook_id,
                            "thread_id": thread_id,
                            "was_cancelled": was_cancelled,
                            "response_length": len(assistant_response),
                        },
                    )
