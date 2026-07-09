"""
Byaan MCP stdio server.

This is a standalone MCP server that uses stdin/stdout transport for direct
communication with Claude Code. It runs independently without requiring the
full FastAPI backend to be running.

Usage:
    python -m server.mcp.stdio_server

Desktop/community mode resolves the single local tenant automatically.
Self-hosted mode requires BYAAN_MCP_USER=<email> (and BYAAN_MCP_TENANT=<slug>
when the user belongs to multiple tenants); access is implicit via the ability
to exec inside the container, e.g.:

    docker exec -i -e BYAAN_MCP_USER=you@org.com <container> \\
        uv run --directory /app/server python -m server.mcp.stdio_server

For remote access to a self-hosted deployment, use the HTTP endpoint
(/api/mcp with a Bearer API key) instead — stdio never listens on a port.
"""

import asyncio
import os
import sys

from fastmcp import FastMCP

from server.db.session import AsyncSessionFactory
from server.prompts.prompts import get_unified_agent_prompt_compact
from server.utils.config_loader import is_self_hosted
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

# Module-level constants for instructions
BYAAN_STDIO_DESCRIPTION = """
Byaan MCP Server (stdio mode) - Local data analysis platform.

Use this server for requests involving databases, data analysis, SQL queries,
data visualization, dashboards, or exploring dataset schemas. Byaan connects to
PostgreSQL, MySQL, MongoDB, SQLite, and file-based datasets (CSV, Excel, Parquet).
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


async def _resolve_self_hosted_context(db_session):
    """
    Resolve identity for self-hosted stdio sessions from BYAAN_MCP_USER.

    There is no token here on purpose: reaching this process already requires
    shell/docker access to the host, which is the trust boundary. The env var
    only selects which existing user the session acts as, and is audit-logged.
    """
    from sqlalchemy import func, select

    from server.models.tenant import Tenant
    from server.models.tenant_member import TenantMember
    from server.models.user import User

    email = os.getenv("BYAAN_MCP_USER", "").strip().lower()
    if not email:
        raise Exception(
            "Self-hosted stdio mode requires BYAAN_MCP_USER=<email> to select the acting user. "
            "Example: docker exec -i -e BYAAN_MCP_USER=you@org.com <container> "
            "uv run --directory /app/server python -m server.mcp.stdio_server"
        )

    result = await db_session.execute(select(User).where(func.lower(User.email) == email))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise Exception(f"BYAAN_MCP_USER '{email}' does not match an active user.")

    owned = (await db_session.execute(select(Tenant).where(Tenant.owner_id == user.id))).scalars().all()
    member = (
        (
            await db_session.execute(
                select(Tenant)
                .join(TenantMember, TenantMember.tenant_id == Tenant.id)
                .where(TenantMember.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    tenants = {tenant.id: tenant for tenant in [*owned, *member]}
    if not tenants:
        raise Exception(f"User '{email}' does not belong to any tenant.")

    slugs = sorted(tenant.slug for tenant in tenants.values())
    tenant_slug = os.getenv("BYAAN_MCP_TENANT", "").strip()
    if tenant_slug:
        matches = [tenant for tenant in tenants.values() if tenant.slug == tenant_slug]
        if not matches:
            raise Exception(f"User '{email}' has no access to tenant '{tenant_slug}'. Available: {slugs}")
        tenant = matches[0]
    elif len(tenants) == 1:
        tenant = next(iter(tenants.values()))
    else:
        raise Exception(f"User '{email}' belongs to multiple tenants; set BYAAN_MCP_TENANT=<slug>. Available: {slugs}")

    logger.info(f"stdio self-hosted session: user={email} ({user.id}) tenant={tenant.slug} ({tenant.id})")
    return tenant.id, user.id


async def _resolve_local_context(db_session):
    """
    Resolve the tenant and user context for this stdio process.

    Desktop/community mode expects a single tenant in the database and only
    selects tenants whose owner actually exists in the users table.
    Self-hosted mode requires an explicit BYAAN_MCP_USER identity.
    """
    from sqlalchemy import select

    from server.auth.tenant_context import set_tenant_id
    from server.models.tenant import Tenant
    from server.models.user import User

    if is_self_hosted():
        tenant_id, user_id = await _resolve_self_hosted_context(db_session)
    else:
        result = await db_session.execute(
            select(Tenant).join(User, Tenant.owner_id == User.id).order_by(Tenant.created_at.desc()).limit(1)
        )
        tenant = result.scalar_one_or_none()

        if not tenant:
            raise Exception("No tenant found. Please complete onboarding first.")

        tenant_id, user_id = tenant.id, tenant.owner_id

    set_tenant_id(tenant_id)
    return tenant_id, user_id


async def _load_skills_for_stdio() -> tuple[dict, dict]:
    """
    Load all custom skills and enabled skills for the local tenant.
    This runs once at startup since stdio mode serves a single tenant.

    Returns:
        Tuple of (custom_skills, enabled_skills)
    """
    try:
        from server.repositories.custom_skill import CustomSkillRepository
        from server.services.crypto_service import CryptoService
        from server.services.skill_registry import SkillRegistry

        async with AsyncSessionFactory() as session:
            tenant_id, user_id = await _resolve_local_context(session)

            # Load custom skills
            custom_skills = {}
            try:
                repo = CustomSkillRepository(session)
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

            except Exception as e:
                logger.warning(f"Failed to load custom skills: {e}")

            # Load enabled skills
            enabled_skills = {}
            try:
                enabled_skills = await SkillRegistry.get_enabled_skills(tenant_id, user_id, session)
            except Exception as e:
                logger.warning(f"Failed to load enabled skills: {e}")

            logger.info(f"Loaded {len(custom_skills)} custom skills: {list(custom_skills.keys())}")
            logger.info(f"Loaded {len(enabled_skills)} enabled skills: {list(enabled_skills.keys())}")

            return custom_skills, enabled_skills

    except Exception as e:
        logger.error(f"Failed to load skills for stdio server: {e}", exc_info=True)
        return {}, {}


def _build_skills_hint_for_stdio(enabled_skills: dict[str, dict], custom_skills: dict[str, dict]) -> str:
    """
    Build the <external_skills> block with actual skill names and descriptions.
    This is the unified_agent approach - list all available skills by name.
    """
    if not enabled_skills and not custom_skills:
        return """
<external_skills>
No custom skills or external API integrations are currently configured.
</external_skills>"""

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


async def create_stdio_server() -> FastMCP:
    """
    Create and configure the stdio MCP server with all tools and instructions.
    Loads skills at startup since stdio mode serves a single tenant.
    """
    logger.info("Initializing Byaan MCP stdio server...")

    # Load skills at startup
    custom_skills, enabled_skills = await _load_skills_for_stdio()
    skills_hint = _build_skills_hint_for_stdio(enabled_skills, custom_skills)

    # Load learnings for pre-injection
    from server.mcp.learning_utils import fetch_relevant_learnings

    async with AsyncSessionFactory() as db_session:
        tenant_id, _ = await _resolve_local_context(db_session)
    relevant_learnings = await fetch_relevant_learnings(tenant_id)

    # Build full instructions
    base_prompt = get_unified_agent_prompt_compact(
        database_schemas=None, model=None, plan_mode=False, learnings=relevant_learnings
    )

    full_instructions = f"""
{LEARNING_ENFORCEMENT}

{BYAAN_STDIO_DESCRIPTION}

{base_prompt}

{skills_hint}
"""

    # Create FastMCP server
    mcp = FastMCP(
        "Byaan",
        instructions=full_instructions,
    )

    logger.info("Byaan MCP stdio server initialized successfully")
    logger.info(f"Skills loaded: {len(custom_skills)} custom, {len(enabled_skills)} enabled")

    return mcp


async def get_stdio_session() -> dict:
    """
    Get or create session data for stdio mode with database persistence.
    Uses a deterministic session_id so the same notebook is reused across server restarts.
    """
    from server.repositories.mcp_session import MCPSessionRepository

    async with AsyncSessionFactory() as db_session:
        tenant_id, user_id = await _resolve_local_context(db_session)

        # Deterministic session_id so the same session/notebook is reused across
        # restarts; per-user in self-hosted mode so engineers don't share notebooks
        if is_self_hosted():
            session_id = f"stdio-{tenant_id}-{user_id}"
        else:
            session_id = f"stdio-local-{tenant_id}"

        # Try to get existing session from database
        repo = MCPSessionRepository(db_session)
        mcp_session = await repo.get_by_session_id(session_id)

        if mcp_session:
            logger.info(f"Reusing existing stdio session {session_id} with notebook {mcp_session.notebook_id}")
            return {
                "tenant_id": mcp_session.tenant_id,
                "user_id": mcp_session.user_id,
                "notebook_id": mcp_session.notebook_id,
                "session_id": mcp_session.session_id,
            }
        else:
            logger.info(f"Creating new stdio session {session_id}")
            mcp_session = await repo.create(
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                mcp_api_key_id=None,
            )
            await db_session.commit()

            return {
                "tenant_id": mcp_session.tenant_id,
                "user_id": mcp_session.user_id,
                "notebook_id": mcp_session.notebook_id,
                "session_id": mcp_session.session_id,
            }


def register_stdio_tools(mcp: FastMCP):
    """
    Register all Byaan tools for stdio mode using shared tools.py.
    Provides stdio-specific session handling with database persistence.
    """
    from server.mcp.tool_wrappers import ensure_notebook_exists
    from server.mcp.tools import register_all_tools

    # Cached session data
    _session_cache = {}

    async def get_session_with_notebook():
        """Get session and ensure notebook exists, persisting to database."""
        if not _session_cache:
            _session_cache.update(await get_stdio_session())

        tenant_id = _session_cache["tenant_id"]
        user_id = _session_cache["user_id"]
        notebook_id = _session_cache.get("notebook_id")
        session_id = _session_cache["session_id"]

        # Ensure notebook exists
        old_notebook_id = notebook_id
        notebook_id = await ensure_notebook_exists(tenant_id, user_id, notebook_id, session_id)
        _session_cache["notebook_id"] = notebook_id

        # If a new notebook was created, persist it to the database session
        if notebook_id and old_notebook_id != notebook_id:
            from server.repositories.mcp_session import MCPSessionRepository

            async with AsyncSessionFactory() as db_session:
                repo = MCPSessionRepository(db_session)
                await repo.update_notebook(session_id, notebook_id)
                await db_session.commit()
                logger.info(f"Updated stdio session {session_id} with notebook {notebook_id}")

        return _session_cache

    # Register all tools using shared tools.py
    register_all_tools(mcp, get_session_with_notebook)
    logger.info("Registered all tools for stdio mode using shared tools.py")


async def main():
    """Main entry point for stdio server."""
    logger.info("=" * 60)
    logger.info("Starting Byaan MCP stdio server")
    logger.info(f"APP_MODE: {os.getenv('APP_MODE', 'not set')}")
    logger.info(f"Database: {os.getenv('DATABASE_URL', 'default')[:50]}...")
    logger.info("=" * 60)

    try:
        # Create server with skills loaded
        mcp = await create_stdio_server()

        # Register all tools
        register_stdio_tools(mcp)

        logger.info("Starting stdio transport...")
        await mcp.run_async()

    except Exception as e:
        logger.error(f"Failed to start stdio server: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
