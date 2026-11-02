"""
Byaan MCP Server - HTTP Transport

Exposes Byaan's AI agent via Model Context Protocol (MCP) over HTTP
for use in Claude Code, Cursor, and other MCP-compatible platforms.
"""

import hashlib
import secrets
from typing import Any
from uuid import UUID

from fastmcp import Context, FastMCP
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.tenant_context import set_tenant_id
from server.db.session import AsyncSessionFactory
from server.mcp.auth import MCPAuthError, validate_api_key
from server.mcp.session_manager import MCPSessionManager
from server.mcp.tool_wrappers import set_session_manager
from server.mcp.tools import register_all_tools
from server.models.tenant import Tenant
from server.models.user import User
from server.prompts.prompts import get_unified_agent_prompt_compact
from server.repositories.mcp_api_key import MCPAPIKeyRepository
from server.utils.config_loader import is_self_hosted
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

BYAAN_MCP_DESCRIPTION = """
Byaan is a local data analysis platform. Use this server for ANY request involving
databases, data analysis, SQL queries, data visualization, dashboards, or exploring
dataset schemas. Byaan connects to PostgreSQL, MySQL, MongoDB, SQLite, and file-based
datasets (CSV, Excel, Parquet). When the user asks about their data, tables, schemas,
metrics, reports, or dashboards, route the request through Byaan.

If the Byaan MCP server is not reachable or connection is refused, check that the
Byaan desktop app is running, or start the backend manually.
"""


LEARNING_ENFORCEMENT = """
<learning_enforcement>
You have amnesia — every conversation starts from zero. Learnings are your only cross-conversation memory.
Without them, you will repeat mistakes, rediscover things already known, and give wrong answers.
Learnings are NOT pre-loaded into this conversation — the ONLY way to access them is by calling search_learnings.

BEFORE ANY WORK: your first tool call for every user message must be search_learnings(query="<keywords>").
Use 1-2 broad terms from the user's question (core nouns/topics). If no results, try synonyms or related terms.
Results found → apply the insights directly. No results → proceed normally.

AFTER ANY WORK — ask yourself: "Did I just learn WHERE something lives, HOW something works, or WHAT to avoid?"
If yes, save it immediately:
- search_learnings first to check for duplicates. Same topic exists → update_learning. New → add_learning.
- Always pass dataset_id when the learning relates to a specific datasource.
- State the lesson directly: the plain fact, rule, or correction. No narrative, no code, no SQL or query text.

What to save: data location discoveries, error fixes, user corrections, schema gotchas, non-obvious patterns.
What NOT to save: query results, data values, totals, SQL or query code, code snippets — only plain-language facts and rules.
</learning_enforcement>"""

EXTERNAL_SKILLS_DISCOVERY = """
<external_skills>
CRITICAL: Custom skills and external API integrations may be available for this user.

MANDATORY AT START OF CONVERSATION:
Before responding to the first user query, you MUST call:
- search_enabled_skills(query="") - returns ALL available skills (enabled Byaan skills + custom skills)

This gives you the complete catalog of available skills. Then for subsequent requests:
- When a skill seems relevant, call get_skill_definition(skill_name) to load full instructions
- Follow the instructions provided by the skill

Skills contain critical instructions about data analysis, APIs, and domain-specific knowledge.
DO NOT skip the initial search_enabled_skills() call - it is required to discover what capabilities are available.
</external_skills>"""


def _get_base_instructions() -> str:
    """Get base system instructions for MCP clients."""
    base_prompt = get_unified_agent_prompt_compact(database_schemas=None, model=None, plan_mode=False)

    return f"""
{LEARNING_ENFORCEMENT}

<byaan_mcp_routing>
{BYAAN_MCP_DESCRIPTION}
</byaan_mcp_routing>

{EXTERNAL_SKILLS_DISCOVERY}

{base_prompt}
"""


mcp = FastMCP(
    "Byaan",
    instructions=_get_base_instructions(),
)
session_manager = MCPSessionManager()
set_session_manager(session_manager)


def _generate_session_id() -> str:
    return f"mcp_session_{secrets.token_urlsafe(32)}"


DEFAULT_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _resolve_local_context(db_session: AsyncSession) -> tuple[UUID, UUID]:
    result = await db_session.execute(
        select(Tenant)
        .join(User, Tenant.owner_id == User.id)
        .where(Tenant.is_personal.is_(True), Tenant.owner_id != DEFAULT_USER_ID)
        .order_by(Tenant.created_at.desc())
        .limit(1)
    )
    tenant = result.scalar_one_or_none()

    if not tenant:
        result = await db_session.execute(select(Tenant).join(User, Tenant.owner_id == User.id).limit(1))
        tenant = result.scalar_one_or_none()

    if not tenant:
        raise Exception("No tenant found. Please complete onboarding first.")

    tenant_id = tenant.id
    user_id = tenant.owner_id

    set_tenant_id(tenant_id)

    return tenant_id, user_id


async def get_or_create_session(headers: dict[str, str], session_id: str | None = None) -> dict[str, Any]:
    auth_header = headers.get("authorization") or headers.get("Authorization", "")
    mcp_session_id = session_id

    if not is_self_hosted():
        async with AsyncSessionFactory() as db_session:
            tenant_id, user_id = await _resolve_local_context(db_session)

            if mcp_session_id:
                session_data = await session_manager.get_session(mcp_session_id)
                if session_data:
                    if session_data["tenant_id"] != tenant_id:
                        raise Exception("Session does not belong to this tenant")
                    await session_manager.update_activity(mcp_session_id)
                    return session_data

            if not mcp_session_id:
                mcp_session_id = _generate_session_id()

            session_data = await session_manager.create_session(
                session_id=mcp_session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                mcp_api_key_id=None,
            )
            return session_data

    # Self-hosted mode: require API key authentication
    auth_header = headers.get("authorization") or headers.get("Authorization", "")

    if not auth_header or not auth_header.startswith("Bearer "):
        logger.error(f"Auth failed. Headers present: {list(headers.keys())}")
        raise Exception("Missing or invalid authorization header")

    api_key = auth_header[7:]

    async with AsyncSessionFactory() as session:
        try:
            tenant_id, user_id = await validate_api_key(api_key, session)
        except MCPAuthError as e:
            raise Exception(f"Authentication error: {str(e)}")

        set_tenant_id(tenant_id)

        api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        repo = MCPAPIKeyRepository(session)
        db_key = await repo.get_by_hash(api_key_hash)

        if not db_key:
            raise Exception("Invalid API key")

        if mcp_session_id:
            session_data = await session_manager.get_session(mcp_session_id)
            if session_data:
                if session_data["tenant_id"] != tenant_id:
                    raise Exception("Session does not belong to this tenant")
                await session_manager.update_activity(mcp_session_id)
                return session_data

        if not mcp_session_id:
            mcp_session_id = _generate_session_id()

        session_data = await session_manager.create_session(
            session_id=mcp_session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            mcp_api_key_id=db_key.id,
        )

        return session_data


def _build_skills_hint_for_mcp(enabled_skills: dict[str, dict], custom_skills: dict[str, dict]) -> str:
    """Build hint about available skills for MCP - matches unified_agent format."""
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


@mcp.prompt()
async def byaan_system_prompt(context: Context = None) -> str:
    """
    Get the full Byaan system prompt with workflow instructions, query rules, and tool usage guidance.

    This prompt provides comprehensive instructions on:
    - How to work with databases (SQL, MongoDB, DuckDB)
    - Dataset discovery and schema exploration workflow
    - Query execution best practices
    - Dashboard generation guidelines
    - Multi-database support

    Use this prompt to understand how to effectively orchestrate Byaan's tools for data analysis tasks.
    """
    try:
        headers = {}
        if context and hasattr(context, "request_context"):
            req_ctx = context.request_context
            if hasattr(req_ctx, "request") and hasattr(req_ctx.request, "headers"):
                headers = dict(req_ctx.request.headers)
            elif hasattr(req_ctx, "headers"):
                headers = dict(req_ctx.headers)

        mcp_session_id_from_header = headers.get("mcp-session-id") or headers.get("Mcp-Session-Id")
        session_data = await get_or_create_session(headers, session_id=mcp_session_id_from_header)

        notebook_id = session_data.get("notebook_id")
        tenant_id = session_data.get("tenant_id")
        user_id = session_data.get("user_id")

        async with AsyncSessionFactory() as db_session:
            from server.repositories.custom_skill import CustomSkillRepository
            from server.services.dataset import DatasetService
            from server.services.skill_registry import SkillRegistry

            set_tenant_id(tenant_id)

            database_schemas = []
            if notebook_id:
                datasets = await DatasetService.get_datasets_by_notebook(db_session, notebook_id)
                for dataset in datasets:
                    schema_info = {
                        "database_number": 1,
                        "dataset_id": str(dataset.id),
                        "db_type": dataset.type,
                        "formatted_schema": f"Dataset: {dataset.name}",
                        "schema_summary": {"type": dataset.type},
                    }
                    database_schemas.append(schema_info)

            # Load custom skills
            custom_skills = {}
            try:
                from server.services.crypto_service import CryptoService

                repo = CustomSkillRepository(db_session)
                skills = await repo.list_accessible(tenant_id, user_id)
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
                        "api_auth_header": skill.api_auth_header,
                        "api_domain": skill.api_domain,
                        "domain_active": skill.domain_active,
                    }

                    if skill.api_credentials_encrypted:
                        try:
                            decrypted = await CryptoService.decrypt_config(skill.api_credentials_encrypted, db_session)
                            entry["credentials"] = decrypted
                        except Exception as e:
                            logger.warning(f"Failed to decrypt credentials for custom skill {skill.name}: {e}")

                    custom_skills[skill.name] = entry
            except Exception as e:
                logger.warning(f"Failed to load custom skills: {e}")

            # Load enabled skills
            enabled_skills = {}
            try:
                enabled_skills = await SkillRegistry.get_enabled_skills(tenant_id, user_id, db_session)
            except Exception as e:
                logger.warning(f"Failed to load enabled skills: {e}")

            from server.mcp.learning_utils import fetch_relevant_learnings

            dataset_ids = [str(ds.get("dataset_id", "")) for ds in database_schemas if ds.get("dataset_id")]
            relevant_learnings = await fetch_relevant_learnings(
                tenant_id, dataset_ids=dataset_ids if dataset_ids else None
            )

            system_prompt = get_unified_agent_prompt_compact(
                database_schemas=database_schemas if database_schemas else None,
                model=None,
                plan_mode=False,
                learnings=relevant_learnings,
            )

            skills_hint = _build_skills_hint_for_mcp(enabled_skills, custom_skills)

            return system_prompt + skills_hint

    except Exception as e:
        logger.error(f"Error generating system prompt: {e}", exc_info=True)
        return get_unified_agent_prompt_compact(database_schemas=None, model=None, plan_mode=False)


# Register all granular tools
register_all_tools(mcp, get_or_create_session)
