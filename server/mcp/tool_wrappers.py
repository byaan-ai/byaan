"""
MCP tool wrappers for Byaan tools.

These wrappers adapt Byaan's internal tools (which use RunContextWrapper)
to work with FastMCP's tool protocol.
"""

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

from agents.run_context import RunContextWrapper

from server.auth.tenant_context import set_tenant_id
from server.db.session import AsyncSessionFactory
from server.utils.custom_logger import get_logger

if TYPE_CHECKING:
    from server.mcp.session_manager import MCPSessionManager

logger = get_logger(__name__)

LEARNING_HINT = (
    "\n\n[If this query revealed new patterns, gotchas, or schema details — "
    "save or update a learning with add_learning/update_learning. "
    "Search first with search_learnings to avoid duplicates.]"
)

_session_manager: "MCPSessionManager | None" = None


def set_session_manager(manager: "MCPSessionManager") -> None:
    """Set the global session manager instance."""
    global _session_manager
    _session_manager = manager


async def ensure_notebook_exists(
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None,
    session_id: str | None,
) -> UUID:
    """
    Ensure a notebook exists for the MCP session.
    Auto-creates one on first tool call if it doesn't exist.
    """
    if notebook_id:
        return notebook_id

    if not session_id:
        raise Exception("Cannot create notebook without session_id")

    async with AsyncSessionFactory() as db_session:
        from datetime import datetime

        from server.schemas.notebooks import NotebookCreate
        from server.services.notebook import NotebookService

        set_tenant_id(tenant_id)

        logger.info(f"Auto-creating notebook for MCP session {session_id} on first tool call")

        now = datetime.now()
        initial_name = now.strftime("MCP Notebook %b %d %I:%M %p")

        notebook_create = NotebookCreate(notebook_name=initial_name, description="Auto-created for MCP session")

        notebook = await NotebookService.create_notebook(
            session=db_session,
            payload=notebook_create,
            tenant_id=tenant_id,
            user_id=user_id,
        )

        await db_session.commit()
        new_notebook_id = notebook.id

        # Update session manager if in HTTP mode
        if _session_manager:
            await _session_manager.update_notebook(session_id, new_notebook_id)

        logger.info(f"Created notebook {new_notebook_id} and associated with MCP session {session_id}")

        return new_notebook_id


async def _load_custom_skills_for_mcp(tenant_id: UUID, user_id: UUID) -> dict[str, dict]:
    """Load custom skills for MCP context - matches unified_agent._load_custom_skills."""
    try:
        from server.repositories.custom_skill import CustomSkillRepository
        from server.services.crypto_service import CryptoService

        async with AsyncSessionFactory() as session:
            set_tenant_id(tenant_id)
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
        logger.warning(f"Failed to load custom skills for MCP: {e}")
        return {}


async def _load_enabled_skills_for_mcp(tenant_id: UUID, user_id: UUID) -> tuple[dict, list[str]]:
    """Load enabled skills for MCP context."""
    try:
        from server.services.skill_registry import SkillRegistry

        async with AsyncSessionFactory() as session:
            set_tenant_id(tenant_id)
            enabled_skills = await SkillRegistry.get_enabled_skills(tenant_id, user_id, session)
            enabled_skill_names = (
                list({data.get("skill_name") for data in enabled_skills.values()}) if enabled_skills else []
            )
            return enabled_skills, enabled_skill_names
    except Exception as e:
        logger.warning(f"Failed to load enabled skills for MCP: {e}")
        return {}, []


async def create_run_context(
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None,
    tool_name: str | None = None,
    session_version: int | None = None,
) -> RunContextWrapper[Any]:
    """
    Create a RunContextWrapper for calling Byaan tools.

    Args:
        tenant_id: Tenant UUID
        user_id: User UUID
        notebook_id: Notebook UUID (optional)
        tool_name: Name of the tool being invoked (optional)
        session_version: Dashboard version (optional)

    Returns:
        RunContextWrapper configured with context
    """
    custom_skills = await _load_custom_skills_for_mcp(tenant_id, user_id)
    enabled_skills, enabled_skill_names = await _load_enabled_skills_for_mcp(tenant_id, user_id)

    context = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "notebook_id": notebook_id,
        "session_version": session_version,
        "custom_skills": custom_skills,
        "enabled_skills": enabled_skills,
        "enabled_skill_names": enabled_skill_names,
    }
    for key, skill_data in enabled_skills.items():
        context[f"{key}_credentials"] = skill_data.get("credentials", {})

    # Create a minimal RunContextWrapper-like object
    class MCPRunContext:
        def __init__(self, ctx_dict, t_name):
            self.context = ctx_dict
            self.tool_name = t_name

    return MCPRunContext(context, tool_name)


async def search_datasets_wrapper(
    query: str,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
) -> str:
    """
    Search for databases and datasets.

    Args:
        query: Search query (e.g., "sales", "customers", "postgres")
        tenant_id: Tenant ID
        user_id: User ID
        notebook_id: Optional notebook context

    Returns:
        JSON string with matching datasets
    """
    from server.tools.agentic import search_datasets

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="search_datasets")
        tool_input = json.dumps({"query": query})
        result = await search_datasets.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in search_datasets_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def get_database_schema_wrapper(
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
) -> str:
    """
    Get the schema of the selected database connection.

    Returns schema with tables, columns, and relationships.

    Args:
        tenant_id: Tenant ID
        user_id: User ID
        notebook_id: Optional notebook context

    Returns:
        JSON string with database schema
    """
    from server.tools.agentic import get_database_schema

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="get_database_schema")
        tool_input = json.dumps({})
        result = await get_database_schema.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in get_database_schema_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


def _flatten_schema_for_mcp(schema_data: dict) -> dict:
    """
    Flatten nested schema to simple field:type format.

    Handles MongoDB, SQL databases (PostgreSQL, MySQL, SQLite), and file-based datasets (DuckDB) for compacted schema.
    """
    if not schema_data.get("success"):
        return schema_data

    dataset = schema_data.get("dataset", {})
    schema = dataset.get("schema", {})

    if not schema:
        return schema_data

    db_type = schema.get("database_type") or schema.get("datasource_type")

    if db_type == "mongo":
        flattened_collections = {}

        inner_schema = schema.get("schema", {})

        for collection_name, collection_data in inner_schema.items():
            if not isinstance(collection_data, dict):
                continue

            sample_fields = collection_data.get("sample_fields", [])
            nested_schema = collection_data.get("nested_schema", {})
            properties = nested_schema.get("properties", {})

            field_list = []
            for field in sample_fields:
                field_type = "unknown"
                if field in properties:
                    prop_types = properties[field].get("type", [])
                    if isinstance(prop_types, list) and prop_types:
                        field_type = prop_types[0] if len(prop_types) == 1 else "|".join(prop_types)
                    elif isinstance(prop_types, str):
                        field_type = prop_types
                field_list.append(f"{field}:{field_type}")

            flattened_collections[collection_name] = field_list

        dataset["schema"] = {
            "database_type": db_type,
            "database_name": schema.get("database_name"),
            **flattened_collections,
        }

    elif db_type in ("postgres", "mysql", "sqlite", "mssql", "pg", "duckdb"):
        flattened_tables = {}
        type_key = "database_type" if "database_type" in schema else "datasource_type"
        name_key = "database_name" if "database_name" in schema else "datasource_name"

        # SQL schemas have nested structure: schema.schema.{tables}
        inner_schema = schema.get("schema", {})

        for table_name, table_data in inner_schema.items():
            if not isinstance(table_data, dict):
                continue

            columns = table_data.get("columns", [])
            field_list = []

            for col in columns:
                if isinstance(col, dict):
                    col_name = col.get("name", "")
                    col_type = col.get("type", "unknown")
                    field_list.append(f"{col_name}:{col_type}")

            flattened_tables[table_name] = field_list

        dataset["schema"] = {
            type_key: db_type,
            name_key: schema.get(name_key),
            **flattened_tables,
        }

    return schema_data


async def get_dataset_schema_by_id_wrapper(
    dataset_id: str,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
    session_id: str | None = None,
) -> str:
    """
    Get schema for a specific dataset by its ID (flattened for MCP performance).

    This wrapper calls the original tool and transforms the schema to a flattened
    format (field:type).

    Args:
        dataset_id: Dataset UUID
        tenant_id: Tenant ID
        user_id: User ID
        notebook_id: Optional notebook context
        session_id: MCP session ID

    Returns:
        JSON string with flattened dataset schema
    """
    from server.tools.agentic import get_dataset_schema_by_id

    try:
        set_tenant_id(tenant_id)
        notebook_id = await ensure_notebook_exists(tenant_id, user_id, notebook_id, session_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="get_dataset_schema_by_id")
        tool_input = json.dumps({"dataset_id": dataset_id})
        result = await get_dataset_schema_by_id.on_invoke_tool(ctx=ctx, input=tool_input)

        result_data = json.loads(result)
        flattened = _flatten_schema_for_mcp(result_data)
        return json.dumps(flattened, indent=2)

    except Exception as e:
        logger.error(f"Error in get_dataset_schema_by_id_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def execute_sql_query_wrapper(
    connection_id: str,
    query: str,
    limit: int,
    timeout: int,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
) -> str:
    """
    Execute a SQL query on PostgreSQL, MySQL, or SQLite.

    Args:
        connection_id: Database connection UUID
        query: SQL query to execute
        limit: Max rows to return (max 50)
        timeout: Query timeout in seconds
        tenant_id: Tenant ID
        user_id: User ID
        notebook_id: Optional notebook context

    Returns:
        JSON string with query results
    """
    from server.tools.sql import execute_sql_query

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="execute_sql_query")
        tool_input = json.dumps({"connection_id": connection_id, "query": query, "limit": limit, "timeout": timeout})
        result = await execute_sql_query.on_invoke_tool(ctx=ctx, input=tool_input)
        return result + LEARNING_HINT
    except Exception as e:
        logger.error(f"Error in execute_sql_query_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def execute_mongo_query_wrapper(
    connection_id: str,
    query: str,
    limit: int,
    timeout: int,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
) -> str:
    """
    Execute a MongoDB query using shell syntax.

    Args:
        connection_id: MongoDB connection UUID
        query: MongoDB query in shell syntax (e.g., db.collection.find({...}))
        limit: Max documents to return (max 50)
        timeout: Query timeout in seconds
        tenant_id: Tenant ID
        user_id: User ID
        notebook_id: Optional notebook context

    Returns:
        JSON string with query results
    """
    from server.tools.mongo import execute_mongo_query

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="execute_mongo_query")
        tool_input = json.dumps(
            {
                "connection_id": connection_id,
                "query": query,
                "limit": limit,
                "timeout": timeout,
            }
        )
        result = await execute_mongo_query.on_invoke_tool(ctx=ctx, input=tool_input)
        return result + LEARNING_HINT
    except Exception as e:
        logger.error(f"Error in execute_mongo_query_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def execute_duckdb_query_wrapper(
    dataset_id: str,
    query: str,
    limit: int,
    timeout: int,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
) -> str:
    """
    Execute a DuckDB query on file-based datasets (CSV, Excel, Parquet).

    Args:
        dataset_id: Dataset UUID
        query: SQL query to execute
        limit: Max rows to return (max 50)
        timeout: Query timeout in seconds
        tenant_id: Tenant ID
        user_id: User ID
        notebook_id: Optional notebook context

    Returns:
        JSON string with query results
    """
    from server.tools.dataframe import execute_duckdb_query

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="execute_duckdb_query")
        tool_input = json.dumps({"dataset_id": dataset_id, "query": query, "limit": limit, "timeout": timeout})
        result = await execute_duckdb_query.on_invoke_tool(ctx=ctx, input=tool_input)
        return result + LEARNING_HINT
    except Exception as e:
        logger.error(f"Error in execute_duckdb_query_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def start_html_generation_wrapper(
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID,
) -> str:
    """
    Start generating a new dashboard HTML.

    Args:
        tenant_id: Tenant ID
        user_id: User ID
        notebook_id: Notebook UUID

    Returns:
        JSON string with generation status
    """
    from server.tools.agentic import start_html_generation

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="start_html_generation")
        tool_input = json.dumps({})
        result = await start_html_generation.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in start_html_generation_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def get_existing_html_wrapper(
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID,
) -> str:
    """
    Get the current dashboard HTML content.

    Args:
        tenant_id: Tenant ID
        user_id: User ID
        notebook_id: Notebook UUID

    Returns:
        JSON string with HTML content
    """
    from server.tools.agentic import get_existing_html

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="get_existing_html")
        tool_input = json.dumps({})
        result = await get_existing_html.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in get_existing_html_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def apply_html_patch_wrapper(
    patch_text: str,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID,
) -> str:
    """
    Apply a patch to modify the dashboard HTML.

    Args:
        patch_text: Unified diff patch text
        tenant_id: Tenant ID
        user_id: User ID
        notebook_id: Notebook UUID

    Returns:
        JSON string with patch result
    """
    from server.tools.agentic import apply_html_patch

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="apply_html_patch")
        tool_input = json.dumps({"patch_text": patch_text})
        result = await apply_html_patch.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in apply_html_patch_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def dashboard_search_replace_wrapper(
    diff_content: str,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID,
) -> str:
    """
    Search and replace content in the dashboard.

    Args:
        diff_content: Search/replace diff content
        tenant_id: Tenant ID
        user_id: User ID
        notebook_id: Notebook UUID

    Returns:
        JSON string with operation result
    """
    from server.tools.agentic import dashboard_search_replace

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="dashboard_search_replace")
        tool_input = json.dumps({"diff_content": diff_content})
        result = await dashboard_search_replace.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in dashboard_search_replace_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def get_chart_styling_wrapper(
    chart_types: list[str] | None,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
) -> str:
    """
    Get chart styling guidelines and best practices.

    Args:
        chart_types: Optional list of chart types to get styling for
        tenant_id: Tenant ID
        user_id: User ID
        notebook_id: Optional notebook context

    Returns:
        JSON string with styling guidelines
    """
    from server.tools.agentic import get_chart_styling

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="get_chart_styling")
        tool_input = json.dumps({"chart_types": chart_types})
        result = await get_chart_styling.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in get_chart_styling_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def get_user_instructions_wrapper(
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
) -> str:
    """
    Get user's custom instructions and preferences.

    Args:
        tenant_id: Tenant ID
        user_id: User ID
        notebook_id: Optional notebook context

    Returns:
        Text with user instructions
    """
    from server.tools.agentic import get_user_instructions

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="get_user_instructions")
        tool_input = json.dumps({})
        result = await get_user_instructions.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in get_user_instructions_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def get_user_style_guidelines_wrapper(
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
) -> str:
    """
    Get user's style guidelines for dashboards.

    Args:
        tenant_id: Tenant ID
        user_id: User ID
        notebook_id: Optional notebook context

    Returns:
        Text with style guidelines
    """
    from server.tools.agentic import get_user_style_guidelines

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="get_user_style_guidelines")
        tool_input = json.dumps({})
        result = await get_user_style_guidelines.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in get_user_style_guidelines_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def saved_query_schema_wrapper(
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
) -> str:
    """
    Get schema of saved queries.

    Args:
        tenant_id: Tenant ID
        user_id: User ID
        notebook_id: Optional notebook context

    Returns:
        JSON string with saved queries schema
    """
    from server.tools.agentic import saved_query_schema

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="saved_query_schema")
        tool_input = json.dumps({})
        result = await saved_query_schema.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in saved_query_schema_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def save_query_wrapper(
    query: str,
    name: str,
    connection_id: str,
    is_dashboard: bool,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
) -> str:
    """
    Save a query for reuse.

    Args:
        query: SQL/MongoDB query text
        name: Query name
        connection_id: Connection UUID
        is_dashboard: Whether this is a dashboard query
        tenant_id: Tenant ID
        user_id: User ID
        notebook_id: Optional notebook context

    Returns:
        JSON string with save result
    """
    from server.tools.agentic import save_query

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="save_query")
        tool_input = json.dumps(
            {"query": query, "name": name, "connection_id": connection_id, "is_dashboard": is_dashboard}
        )
        result = await save_query.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in save_query_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def get_filter_options_wrapper(
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID,
) -> str:
    """
    Get available filter options for the dashboard.

    Args:
        tenant_id: Tenant ID
        user_id: User ID
        notebook_id: Notebook UUID

    Returns:
        JSON string with filter options
    """
    from server.tools.filters import get_filter_options

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="get_filter_options")
        tool_input = json.dumps({})
        result = await get_filter_options.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in get_filter_options_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def define_dashboard_filters_wrapper(
    filters_config: str,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID,
) -> str:
    """
    Define filters for the dashboard.

    Args:
        filters_config: JSON string with filter configuration
        tenant_id: Tenant ID
        user_id: User ID
        notebook_id: Notebook UUID

    Returns:
        JSON string with operation result
    """
    from server.tools.filters import define_dashboard_filters

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="define_dashboard_filters")
        tool_input = json.dumps({"filters_config": filters_config})
        result = await define_dashboard_filters.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in define_dashboard_filters_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def update_dashboard_filter_wrapper(
    filter_id: str,
    updates: str,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID,
) -> str:
    """
    Update an existing dashboard filter.

    Args:
        filter_id: Filter ID
        updates: JSON string with filter updates
        tenant_id: Tenant ID
        user_id: User ID
        notebook_id: Notebook UUID

    Returns:
        JSON string with operation result
    """
    from server.tools.filters import update_dashboard_filter

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="update_dashboard_filter")
        tool_input = json.dumps({"filter_id": filter_id, "updates": updates})
        result = await update_dashboard_filter.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in update_dashboard_filter_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def remove_dashboard_filter_wrapper(
    filter_id: str,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID,
) -> str:
    """
    Remove a dashboard filter.

    Args:
        filter_id: Filter ID
        tenant_id: Tenant ID
        user_id: User ID
        notebook_id: Notebook UUID

    Returns:
        JSON string with operation result
    """
    from server.tools.filters import remove_dashboard_filter

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="remove_dashboard_filter")
        tool_input = json.dumps({"filter_id": filter_id})
        result = await remove_dashboard_filter.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in remove_dashboard_filter_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def get_dashboard_filter_config_wrapper(
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID,
) -> str:
    """
    Get current dashboard filter configuration.

    Args:
        tenant_id: Tenant ID
        user_id: User ID
        notebook_id: Notebook UUID

    Returns:
        JSON string with filter configuration
    """
    from server.tools.filters import get_dashboard_filter_config

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="get_dashboard_filter_config")
        tool_input = json.dumps({})
        result = await get_dashboard_filter_config.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in get_dashboard_filter_config_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def search_instructions_wrapper(
    query: str,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
) -> str:
    from server.tools.instruction import search_instructions

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="search_instructions")
        tool_input = json.dumps({"query": query})
        result = await search_instructions.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in search_instructions_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def add_learning_wrapper(
    title: str,
    learning: str,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
    dataset_id: str = "",
) -> str:
    from server.tools.learnings import add_learning

    try:
        logger.info(f"[LEARNING] MCP add_learning: tenant_id={tenant_id}, title='{title[:80]}'")
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="add_learning")
        tool_input = json.dumps({"title": title, "learning": learning, "dataset_id": dataset_id})
        result = await add_learning.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in add_learning_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def update_learning_wrapper(
    learning_id: str,
    learning: str,
    title: str = "",
    tenant_id: UUID = None,
    user_id: UUID = None,
    notebook_id: UUID | None = None,
    dataset_id: str = "",
) -> str:
    from server.tools.learnings import update_learning

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="update_learning")
        tool_input = json.dumps(
            {"learning_id": learning_id, "learning": learning, "title": title, "dataset_id": dataset_id}
        )
        result = await update_learning.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in update_learning_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def search_learnings_wrapper(
    query: str,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
    dataset_id: str = "",
) -> str:
    from server.tools.learnings import search_learnings

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="search_learnings")
        tool_input = json.dumps({"query": query, "dataset_id": dataset_id})
        result = await search_learnings.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in search_learnings_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def get_learning_wrapper(
    learning_id: str,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
) -> str:
    from server.tools.learnings import get_learning

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="get_learning")
        tool_input = json.dumps({"learning_id": learning_id})
        result = await get_learning.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in get_learning_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def remove_learning_wrapper(
    learning_id: str,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
) -> str:
    from server.tools.learnings import remove_learning

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="remove_learning")
        tool_input = json.dumps({"learning_id": learning_id})
        result = await remove_learning.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in remove_learning_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def emit_plan_status_wrapper(
    action: str,
    steps_json: str,
    step_number: int,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
) -> str:
    from server.tools.plan_tools import emit_plan_status

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="emit_plan_status")
        tool_input = json.dumps({"action": action, "steps_json": steps_json, "step_number": step_number})
        result = await emit_plan_status.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in emit_plan_status_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def search_enabled_skills_wrapper(
    query: str,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
) -> str:
    from server.tools.skill_executor import search_enabled_skills

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="search_enabled_skills")
        tool_input = json.dumps({"query": query})
        result = await search_enabled_skills.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in search_enabled_skills_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def get_skill_definition_wrapper(
    skill_name: str,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
) -> str:
    from server.tools.skill_executor import get_skill_definition

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="get_skill_definition")
        tool_input = json.dumps({"skill_name": skill_name})
        result = await get_skill_definition.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in get_skill_definition_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def execute_skill_api_wrapper(
    skill_name: str,
    endpoint_path: str,
    method: str,
    body: str,
    headers: str,
    is_graphql: bool,
    graphql_query: str,
    graphql_variables: str,
    scope: str,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
) -> str:
    from server.tools.skill_executor import execute_skill_api

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="execute_skill_api")
        tool_input = json.dumps(
            {
                "skill_name": skill_name,
                "endpoint_path": endpoint_path,
                "method": method,
                "body": body,
                "headers": headers,
                "is_graphql": is_graphql,
                "graphql_query": graphql_query,
                "graphql_variables": graphql_variables,
                "scope": scope,
            }
        )
        result = await execute_skill_api.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in execute_skill_api_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def save_skill_query_wrapper(
    skill_name: str,
    name: str,
    endpoint_path: str,
    method: str,
    body: str,
    is_graphql: bool,
    graphql_query: str,
    graphql_variables: str,
    scope: str,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
) -> str:
    from server.tools.skill_executor import save_skill_query as save_skill_query_tool

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="save_skill_query")
        tool_input = json.dumps(
            {
                "skill_name": skill_name,
                "name": name,
                "endpoint_path": endpoint_path,
                "method": method,
                "body": body,
                "is_graphql": is_graphql,
                "graphql_query": graphql_query,
                "graphql_variables": graphql_variables,
                "scope": scope,
            }
        )
        result = await save_skill_query_tool.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in save_skill_query_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def update_custom_skill_wrapper(
    skill_name: str,
    instructions: str | None,
    description: str | None,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
) -> str:
    from server.tools.skill_executor import update_custom_skill

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="update_custom_skill")
        tool_input = json.dumps(
            {
                "skill_name": skill_name,
                "instructions": instructions,
                "description": description,
            }
        )
        result = await update_custom_skill.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in update_custom_skill_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def create_custom_skill_wrapper(
    name: str,
    description: str,
    instructions: str,
    tenant_id: UUID,
    user_id: UUID,
    notebook_id: UUID | None = None,
) -> str:
    from server.tools.skill_executor import create_custom_skill

    try:
        set_tenant_id(tenant_id)
        ctx = await create_run_context(tenant_id, user_id, notebook_id, tool_name="create_custom_skill")
        tool_input = json.dumps(
            {
                "name": name,
                "description": description,
                "instructions": instructions,
            }
        )
        result = await create_custom_skill.on_invoke_tool(ctx=ctx, input=tool_input)
        return result
    except Exception as e:
        logger.error(f"Error in create_custom_skill_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})
