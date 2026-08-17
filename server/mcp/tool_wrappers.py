"""
MCP tool wrappers for Byaan tools.

These wrappers adapt Byaan's internal tools (which use RunContextWrapper)
to work with FastMCP's tool protocol.
"""

import hashlib
import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

from agents.run_context import RunContextWrapper
from fastapi import HTTPException
from sqlalchemy import select

from server.auth.scopes import Scope, get_scopes_for_role, has_scope
from server.auth.tenant_context import set_tenant_id
from server.db.session import AsyncSessionFactory
from server.models.dashboard import Dashboard, DashboardAsset
from server.models.notebooks import Notebook
from server.models.tenant import Tenant
from server.models.tenant_member import TenantMember, TenantRole
from server.repositories.dashboard import DashboardRepository
from server.services.dashboard import DashboardService
from server.utils.custom_logger import get_logger
from server.utils.error_sanitizer import sanitize_error_message, sanitize_error_payload

if TYPE_CHECKING:
    from server.mcp.session_manager import MCPSessionManager

logger = get_logger(__name__)

LEARNING_HINT = (
    "\n\n[If this query revealed new patterns, gotchas, or schema details — "
    "save or update a learning with add_learning/update_learning. "
    "Search first with search_learnings to avoid duplicates.]"
)

_session_manager: "MCPSessionManager | None" = None

MCP_EVALUATION_DEFAULT_LIMIT = 20
MCP_EVALUATION_MAX_LIMIT = 50
MCP_DASHBOARD_DEFAULT_LIMIT = 20
MCP_DASHBOARD_MAX_LIMIT = 50


def _json_error(error: Exception | str, *, operation: str | None = None) -> str:
    if isinstance(error, HTTPException):
        detail = sanitize_error_payload(error.detail)
        message = detail if isinstance(detail, str) else detail.get("message", detail.get("error", str(detail)))
        payload: dict[str, Any] = {
            "success": False,
            "error": message,
            "status_code": error.status_code,
            "retryable": error.status_code >= 500,
        }
        if operation:
            payload["operation"] = operation
        if isinstance(detail, dict):
            payload["details"] = detail
        return json.dumps(payload, ensure_ascii=False, default=str)
    payload = {"success": False, "error": sanitize_error_message(error)}
    if operation:
        payload["operation"] = operation
    return json.dumps(payload, ensure_ascii=False, default=str)


def _json_success(**payload: Any) -> str:
    return json.dumps({"success": True, **payload}, ensure_ascii=False, default=str)


def _stable_feedback_case_key(feedback: dict[str, Any]) -> str:
    if feedback.get("case_key"):
        return str(feedback["case_key"])
    feedback_id = feedback.get("feedback_id") or feedback.get("trace_id")
    if feedback_id:
        return f"mcp-feedback-{feedback_id}"
    digest = hashlib.sha256(json.dumps(feedback, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
    return f"mcp-feedback-{digest}"


async def _require_mcp_dashboard_scope(db_session, tenant_id: UUID, user_id: UUID, scope: Scope) -> TenantRole:
    tenant = await db_session.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    role = TenantRole.OWNER if tenant.owner_id == user_id else None
    if role is None:
        membership = await db_session.scalar(
            select(TenantMember).where(TenantMember.tenant_id == tenant_id, TenantMember.user_id == user_id)
        )
        if not membership:
            raise HTTPException(status_code=403, detail="MCP principal is not a tenant member")
        role = TenantRole(membership.role)
    if not has_scope(get_scopes_for_role(role), scope):
        raise HTTPException(status_code=403, detail=f"Permission denied. Required scope: {scope.value}")
    return role


async def _assert_mcp_notebook_access(db_session, tenant_id: UUID, user_id: UUID, notebook_id: UUID) -> None:
    notebook = await db_session.scalar(select(Notebook).where(Notebook.id == notebook_id, Notebook.tenant_id == tenant_id))
    if not notebook:
        raise HTTPException(status_code=404, detail="Notebook not found")
    role = await _require_mcp_dashboard_scope(db_session, tenant_id, user_id, Scope.DASHBOARD_SHARE)
    if role == TenantRole.MEMBER and str(notebook.created_by) != str(user_id):
        raise HTTPException(status_code=403, detail="MCP principal can only use notebooks they created")


def _asset_compact(asset: DashboardAsset) -> dict[str, Any]:
    return {
        "id": str(asset.id),
        "slug": asset.slug,
        "name": asset.name,
        "description": asset.description,
        "notebook_id": str(asset.notebook_id) if asset.notebook_id else None,
        "lifecycle": asset.lifecycle,
        "etag": asset.etag,
        "current_draft_version_id": str(asset.current_draft_version_id) if asset.current_draft_version_id else None,
        "published_version_id": str(asset.published_version_id) if asset.published_version_id else None,
        "freshness_policy": asset.freshness_policy_json or {},
        "health_summary": asset.health_summary_json or {},
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
    }


def _version_compact(version: Dashboard, *, include_manifest: bool = False) -> dict[str, Any]:
    payload = {
        "id": str(version.id),
        "version_num": version.version_num,
        "status": version.status,
        "content_hash": version.content_hash,
        "manifest_schema_version": version.manifest_schema_version,
        "pinned_model_versions": version.pinned_model_versions_json or {},
        "pinned_source_snapshots": version.pinned_source_snapshots_json or [],
        "validation_result": version.validation_result_json or {},
        "migration_state": version.migration_state,
        "is_published_immutable": version.is_published_immutable,
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }
    if include_manifest:
        payload["manifest"] = version.manifest_json or {}
    return payload


def _lineage_from_manifest(manifest: dict[str, Any] | None, tile_id: str = "") -> dict[str, Any]:
    manifest = manifest or {}
    tile_data_view_ids = {
        tile.get("data_view_id") for tile in manifest.get("tiles", []) if not tile_id or tile.get("id") == tile_id
    }
    data_views = []
    for data_view in manifest.get("data_views", []):
        if tile_id and data_view.get("id") not in tile_data_view_ids:
            continue
        data_views.append(
            {
                "id": data_view.get("id"),
                "kind": data_view.get("kind"),
                "question": data_view.get("question"),
                "lineage": data_view.get("lineage") or data_view.get("saved_query", {}).get("lineage", []),
                "evidence": data_view.get("evidence") or [],
            }
        )
    return {
        "dashboard_id": manifest.get("dashboard_id"),
        "semantic_bindings": manifest.get("semantic_bindings") or [],
        "data_views": data_views,
        "migration": manifest.get("migration") or {},
    }


def _compact_dashboard_run(run: dict[str, Any], limit: int = MCP_DASHBOARD_DEFAULT_LIMIT) -> dict[str, Any]:
    limit = max(1, min(limit, MCP_DASHBOARD_MAX_LIMIT))
    views = []
    for view in run.get("views", []):
        result = view.get("result")
        has_more = False
        if isinstance(result, list):
            has_more = len(result) > limit
            result = result[:limit]
        views.append(
            {
                "data_view_id": view.get("data_view_id"),
                "status": view.get("status"),
                "schema": view.get("schema", []),
                "result": result,
                "row_count": view.get("row_count", 0),
                "cached": view.get("cached", False),
                "stale": view.get("stale", False),
                "as_of": view.get("as_of"),
                "warnings": view.get("warnings", []),
                "error": view.get("error"),
                "evidence": view.get("evidence", []),
                "lineage": view.get("lineage", []),
                "pagination": {"limit": limit, "has_more": has_more},
            }
        )
    return {
        "contract_version": run.get("contract_version"),
        "run_id": run.get("run_id"),
        "dashboard_id": run.get("dashboard_id"),
        "dashboard_version_id": run.get("dashboard_version_id"),
        "actor_type": run.get("actor_type"),
        "actor_id": run.get("actor_id"),
        "correlation_id": run.get("correlation_id"),
        "idempotency_key": run.get("idempotency_key"),
        "mode": run.get("mode"),
        "normalized_filters": run.get("normalized_filters", {}),
        "filter_digest": run.get("filter_digest"),
        "pinned_versions": run.get("pinned_versions", {}),
        "execution_plan_digest": run.get("execution_plan_digest"),
        "overall_freshness": run.get("overall_freshness"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "views": views,
        "warnings": run.get("warnings", []),
        "errors": run.get("errors", []),
    }


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


async def list_sharing_grants_wrapper(
    tenant_id: UUID,
    user_id: UUID,
    object_type: str | None = None,
    object_id: str | None = None,
    legacy_surface: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> str:
    try:
        from server.serializers.sharing import sharing_grant_payload
        from server.services.sharing import SharingService

        set_tenant_id(tenant_id)
        async with AsyncSessionFactory() as session:
            grants = await SharingService(session).list_grants(
                tenant_id=tenant_id,
                object_type=object_type,
                object_id=object_id,
                legacy_surface=legacy_surface,
                status=status,
                limit=limit,
            )
        return json.dumps(
            {
                "success": True,
                "items": [sharing_grant_payload(grant) for grant in grants],
                "total": len(grants),
            }
        )
    except Exception as e:
        logger.error(f"Error in list_sharing_grants_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def describe_sharing_grant_wrapper(grant_id: str, tenant_id: UUID, user_id: UUID) -> str:
    try:
        from server.serializers.sharing import sharing_grant_evidence_payload
        from server.services.sharing import SharingService

        set_tenant_id(tenant_id)
        async with AsyncSessionFactory() as session:
            evidence = await SharingService(session).grant_evidence(tenant_id=tenant_id, grant_id=grant_id)
        return json.dumps({"success": True, **sharing_grant_evidence_payload(evidence)})
    except Exception as e:
        logger.error(f"Error in describe_sharing_grant_wrapper: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


async def search_evaluation_suites_wrapper(
    query: str,
    target_kind: str,
    status: str,
    tenant_id: UUID,
    user_id: UUID,
    limit: int = MCP_EVALUATION_DEFAULT_LIMIT,
) -> str:
    try:
        from server.serializers.evaluation import evaluation_suite_payload
        from server.services.evaluation import EvaluationService

        set_tenant_id(tenant_id)
        async with AsyncSessionFactory() as session:
            suites = await EvaluationService(session).list_suites(
                tenant_id=tenant_id,
                query=query,
                target_kind=target_kind,
                status=status,
                limit=max(1, min(limit, MCP_EVALUATION_MAX_LIMIT)),
            )
        return _json_success(items=[evaluation_suite_payload(suite) for suite in suites], total=len(suites))
    except Exception as e:
        logger.error(f"Error in search_evaluation_suites_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="search_evaluation_suites")


async def describe_evaluation_suite_wrapper(
    suite_id: str,
    tenant_id: UUID,
    user_id: UUID,
    include_manifests: bool = False,
) -> str:
    try:
        from server.serializers.evaluation import evaluation_suite_payload
        from server.services.evaluation import EvaluationService

        set_tenant_id(tenant_id)
        async with AsyncSessionFactory() as session:
            suite, versions = await EvaluationService(session).describe_suite(
                tenant_id=tenant_id,
                suite_id=UUID(suite_id),
            )
        return _json_success(
            suite=evaluation_suite_payload(
                suite,
                versions=versions,
                include_versions=True,
                include_manifests=include_manifests,
            )
        )
    except Exception as e:
        logger.error(f"Error in describe_evaluation_suite_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="describe_evaluation_suite")


async def list_evaluation_cases_wrapper(
    suite_version_id: str,
    tenant_id: UUID,
    user_id: UUID,
    include_expected_contract: bool = False,
    limit: int = MCP_EVALUATION_DEFAULT_LIMIT,
) -> str:
    try:
        from server.serializers.evaluation import evaluation_case_payload
        from server.services.evaluation import EvaluationService

        set_tenant_id(tenant_id)
        async with AsyncSessionFactory() as session:
            cases = await EvaluationService(session).list_cases(
                tenant_id=tenant_id,
                suite_version_id=UUID(suite_version_id),
            )
        limit = max(1, min(limit, MCP_EVALUATION_MAX_LIMIT))
        return _json_success(
            items=[
                evaluation_case_payload(case, include_expected_contract=include_expected_contract)
                for case in cases[:limit]
            ],
            total=len(cases),
            has_more=len(cases) > limit,
        )
    except Exception as e:
        logger.error(f"Error in list_evaluation_cases_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="list_evaluation_cases")


async def create_evaluation_case_draft_wrapper(
    suite_version_id: str,
    case_json: str,
    tenant_id: UUID,
    user_id: UUID,
) -> str:
    try:
        from server.serializers.evaluation import evaluation_case_payload
        from server.services.evaluation import EvaluationService

        payload = json.loads(case_json or "{}")
        if not isinstance(payload, dict):
            raise ValueError("case_json must be a JSON object")
        set_tenant_id(tenant_id)
        async with AsyncSessionFactory() as session:
            case, created = await EvaluationService(session).create_case_draft(
                tenant_id=tenant_id,
                suite_version_id=UUID(suite_version_id),
                case_key=str(payload["case_key"]),
                title=str(payload.get("title") or payload["case_key"]),
                target_kinds=list(payload.get("target_kinds") or []),
                operation=str(payload.get("operation") or "answer_question"),
                question=str(payload["question"]),
                expected_contract=dict(payload.get("expected_contract") or {}),
                provenance=dict(payload.get("provenance") or {"source": "manual"}),
                tags=[str(tag) for tag in payload.get("tags") or []],
                actor_id=str(user_id),
                actor_type="agent",
            )
        return _json_success(case=evaluation_case_payload(case), created=created)
    except Exception as e:
        logger.error(f"Error in create_evaluation_case_draft_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="create_evaluation_case_draft")


async def preview_evaluation_ground_truth_wrapper(
    expected_contract_json: str,
    tenant_id: UUID,
    user_id: UUID,
) -> str:
    try:
        from server.schemas.evaluation import EvaluationExpectedContract

        payload = json.loads(expected_contract_json or "{}")
        if not isinstance(payload, dict):
            raise ValueError("expected_contract_json must be a JSON object")
        expected = EvaluationExpectedContract.model_validate(payload)
        ground_truth = expected.ground_truth_sql
        if ground_truth is None:
            return _json_success(ground_truth={"present": False, "readonly": None})
        return _json_success(
            ground_truth={
                "present": True,
                "readonly": True,
                "dialect": ground_truth.dialect,
                "must_reference": ground_truth.must_reference,
                "must_not_reference": ground_truth.must_not_reference,
            }
        )
    except Exception as e:
        logger.error(f"Error in preview_evaluation_ground_truth_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="preview_evaluation_ground_truth")


async def run_evaluation_wrapper(
    suite_version_id: str,
    target_snapshot_json: str,
    idempotency_key: str,
    tenant_id: UUID,
    user_id: UUID,
) -> str:
    try:
        from server.serializers.evaluation import evaluation_run_payload
        from server.services.evaluation import EvaluationService

        payload = json.loads(target_snapshot_json or "{}")
        if not isinstance(payload, dict):
            raise ValueError("target_snapshot_json must be a JSON object")
        set_tenant_id(tenant_id)
        async with AsyncSessionFactory() as session:
            run = await EvaluationService(session).create_preflight_run(
                tenant_id=tenant_id,
                suite_version_id=UUID(suite_version_id),
                target_snapshot_payload=payload,
                actor_id=str(user_id),
                idempotency_key=idempotency_key or None,
                actor_type="agent",
            )
        return _json_success(run=evaluation_run_payload(run))
    except Exception as e:
        logger.error(f"Error in run_evaluation_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="run_evaluation")


async def get_evaluation_run_wrapper(
    run_id: str,
    tenant_id: UUID,
    user_id: UUID,
    include_case_results: bool = True,
    limit: int = MCP_EVALUATION_DEFAULT_LIMIT,
) -> str:
    try:
        from server.serializers.evaluation import evaluation_case_run_payload, evaluation_run_payload
        from server.services.evaluation import EvaluationService

        set_tenant_id(tenant_id)
        async with AsyncSessionFactory() as session:
            run, case_runs = await EvaluationService(session).get_run_report(
                tenant_id=tenant_id,
                run_id=UUID(run_id),
            )
        limit = max(1, min(limit, MCP_EVALUATION_MAX_LIMIT))
        payload: dict[str, Any] = {"run": evaluation_run_payload(run)}
        if include_case_results:
            payload["case_runs"] = [
                evaluation_case_run_payload(case_run, assessments=assessments)
                for case_run, assessments in case_runs[:limit]
            ]
            payload["has_more_case_runs"] = len(case_runs) > limit
        return _json_success(**payload)
    except Exception as e:
        logger.error(f"Error in get_evaluation_run_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="get_evaluation_run")


async def compare_evaluation_runs_wrapper(
    baseline_run_id: str,
    candidate_run_id: str,
    tenant_id: UUID,
    user_id: UUID,
) -> str:
    try:
        from server.services.evaluation import EvaluationService

        set_tenant_id(tenant_id)
        async with AsyncSessionFactory() as session:
            comparison = await EvaluationService(session).compare_runs(
                tenant_id=tenant_id,
                baseline_run_id=UUID(baseline_run_id),
                candidate_run_id=UUID(candidate_run_id),
            )
        return _json_success(comparison=sanitize_error_payload(comparison))
    except Exception as e:
        logger.error(f"Error in compare_evaluation_runs_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="compare_evaluation_runs")


async def describe_evaluation_failure_wrapper(
    run_id: str,
    tenant_id: UUID,
    user_id: UUID,
    limit: int = MCP_EVALUATION_DEFAULT_LIMIT,
) -> str:
    try:
        from server.serializers.evaluation import evaluation_case_run_payload, evaluation_run_payload
        from server.services.evaluation import EvaluationService

        set_tenant_id(tenant_id)
        async with AsyncSessionFactory() as session:
            run, case_runs = await EvaluationService(session).get_run_report(
                tenant_id=tenant_id,
                run_id=UUID(run_id),
            )
        failures = []
        for case_run, assessments in case_runs:
            failed_assessments = [
                assessment for assessment in assessments if assessment.status != "passed" or assessment.hard_fail
            ]
            if case_run.status != "passed" or failed_assessments:
                failures.append(evaluation_case_run_payload(case_run, assessments=failed_assessments))
        limit = max(1, min(limit, MCP_EVALUATION_MAX_LIMIT))
        return _json_success(
            run=evaluation_run_payload(run),
            failures=failures[:limit],
            total=len(failures),
            has_more=len(failures) > limit,
        )
    except Exception as e:
        logger.error(f"Error in describe_evaluation_failure_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="describe_evaluation_failure")


async def create_advisor_change_set_wrapper(
    skill_suggestion_id: str,
    suite_version_id: str,
    affected_case_ids: list[str] | None,
    tenant_id: UUID,
    user_id: UUID,
) -> str:
    try:
        from server.serializers.evaluation import advisor_change_set_payload, advisor_suggestion_payload
        from server.services.evaluation import EvaluationService

        set_tenant_id(tenant_id)
        async with AsyncSessionFactory() as session:
            change_set, suggestions, created = await EvaluationService(session).create_advisor_change_set_from_skill_suggestion(
                tenant_id=tenant_id,
                suggestion_id=UUID(skill_suggestion_id),
                suite_version_id=UUID(suite_version_id) if suite_version_id else None,
                affected_case_ids=[UUID(case_id) for case_id in affected_case_ids or []],
                actor_id=str(user_id),
            )
        return _json_success(
            change_set=advisor_change_set_payload(change_set),
            advisor_suggestions=[advisor_suggestion_payload(suggestion) for suggestion in suggestions],
            created=created,
        )
    except Exception as e:
        logger.error(f"Error in create_advisor_change_set_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="create_advisor_change_set")


async def run_advisor_gate_wrapper(
    change_set_id: str,
    target_snapshot_json: str,
    gate_kind: str,
    idempotency_key: str,
    tenant_id: UUID,
    user_id: UUID,
) -> str:
    try:
        from server.serializers.evaluation import advisor_change_set_payload, evaluation_run_payload
        from server.services.evaluation import EvaluationService

        payload = json.loads(target_snapshot_json or "{}")
        if not isinstance(payload, dict):
            raise ValueError("target_snapshot_json must be a JSON object")
        set_tenant_id(tenant_id)
        async with AsyncSessionFactory() as session:
            change_set, run = await EvaluationService(session).create_advisor_gate_run(
                tenant_id=tenant_id,
                change_set_id=UUID(change_set_id),
                gate_kind=gate_kind,
                target_snapshot_payload=payload,
                actor_id=str(user_id),
                idempotency_key=idempotency_key or None,
            )
        return _json_success(change_set=advisor_change_set_payload(change_set), run=evaluation_run_payload(run))
    except Exception as e:
        logger.error(f"Error in run_advisor_gate_wrapper: {e}", exc_info=True)
        return _json_error(e, operation=f"run_advisor_{gate_kind}")


async def submit_evaluation_feedback_wrapper(
    suite_version_id: str,
    feedback_json: str,
    tenant_id: UUID,
    user_id: UUID,
) -> str:
    try:
        from server.serializers.evaluation import evaluation_case_payload
        from server.services.evaluation import EvaluationService

        feedback = json.loads(feedback_json or "{}")
        if not isinstance(feedback, dict):
            raise ValueError("feedback_json must be a JSON object")
        set_tenant_id(tenant_id)
        async with AsyncSessionFactory() as session:
            service = EvaluationService(session)
            legacy_id = feedback.get("legacy_conversation_evaluation_id") or feedback.get("conversation_evaluation_id")
            if legacy_id:
                case, created = await service.promote_conversation_evaluation_to_case_draft(
                    tenant_id=tenant_id,
                    evaluation_id=UUID(str(legacy_id)),
                    suite_version_id=UUID(suite_version_id),
                    question=feedback.get("question"),
                    tags=list(feedback.get("tags") or []),
                    actor_id=str(user_id),
                )
            else:
                description = str(feedback.get("description") or "")
                expected_contract = dict(feedback.get("expected_contract") or {})
                if not expected_contract:
                    expected_contract = {
                        "semantic_intent": {"description": description},
                        "answer": {"must_include_any": list(feedback.get("must_include_any") or [])},
                    }
                case, created = await service.create_case_draft(
                    tenant_id=tenant_id,
                    suite_version_id=UUID(suite_version_id),
                    case_key=_stable_feedback_case_key(feedback),
                    title=str(feedback.get("title") or description or "MCP evaluation feedback")[:255],
                    target_kinds=list(feedback.get("target_kinds") or ["agent_answer"]),
                    operation=str(feedback.get("operation") or "answer_question"),
                    question=str(feedback.get("question") or description or "Review feedback regression"),
                    expected_contract=expected_contract,
                    provenance=sanitize_error_payload(
                        {
                            "source": feedback.get("source") or "human_feedback",
                            "feedback_id": feedback.get("feedback_id"),
                            "trace_id": feedback.get("trace_id"),
                            "principal": feedback.get("principal") or {},
                        }
                    ),
                    tags=[str(tag) for tag in feedback.get("tags") or []],
                    actor_id=str(user_id),
                    actor_type="agent",
                )
        return _json_success(case=evaluation_case_payload(case), created=created)
    except Exception as e:
        logger.error(f"Error in submit_evaluation_feedback_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="submit_evaluation_feedback")


async def _select_dashboard_version(
    repo: DashboardRepository,
    tenant_id: UUID,
    asset: DashboardAsset,
    version: str,
) -> Dashboard | None:
    if version in {"", "published"}:
        version_id = asset.published_version_id or asset.current_draft_version_id
        if not version_id:
            return None
        return await repo.get_asset_version(tenant_id=tenant_id, asset_id=asset.id, version_id=version_id)
    if version == "draft":
        if not asset.current_draft_version_id:
            return None
        return await repo.get_asset_version(tenant_id=tenant_id, asset_id=asset.id, version_id=asset.current_draft_version_id)
    return await repo.get_asset_version_by_num(tenant_id=tenant_id, asset_id=asset.id, version_num=int(version))


async def search_dashboards_wrapper(
    query: str,
    tags: list[str] | None,
    status: str,
    freshness: str,
    tenant_id: UUID,
    user_id: UUID,
    limit: int = MCP_DASHBOARD_DEFAULT_LIMIT,
) -> str:
    try:
        async with AsyncSessionFactory() as session:
            set_tenant_id(tenant_id)
            await _require_mcp_dashboard_scope(session, tenant_id, user_id, Scope.DASHBOARD_READ)
            repo = DashboardRepository(session)
            assets = await repo.list_assets(tenant_id, limit=MCP_DASHBOARD_MAX_LIMIT)
            query_lower = query.lower().strip()
            tag_filter = {tag.lower() for tag in tags or []}
            items = []
            for asset in assets:
                if status and asset.lifecycle != status:
                    continue
                if query_lower and query_lower not in asset.name.lower() and query_lower not in asset.slug.lower():
                    continue
                asset_tags = {str(tag).lower() for tag in asset.tags_json or []}
                if tag_filter and not tag_filter.issubset(asset_tags):
                    continue
                freshness_status = (asset.health_summary_json or {}).get("freshness")
                if freshness and freshness_status != freshness:
                    continue
                items.append(_asset_compact(asset))
            limit = max(1, min(limit, MCP_DASHBOARD_MAX_LIMIT))
            return _json_success(items=items[:limit], total=len(items), cursor=None, has_more=len(items) > limit)
    except Exception as e:
        logger.error(f"Error in search_dashboards_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="search_dashboards")


async def describe_dashboard_wrapper(
    dashboard_id: str,
    version: str,
    detail: str,
    tenant_id: UUID,
    user_id: UUID,
) -> str:
    try:
        asset_id = UUID(dashboard_id)
        async with AsyncSessionFactory() as session:
            set_tenant_id(tenant_id)
            await _require_mcp_dashboard_scope(session, tenant_id, user_id, Scope.DASHBOARD_READ)
            repo = DashboardRepository(session)
            asset = await repo.get_asset(asset_id, tenant_id)
            if not asset:
                raise HTTPException(status_code=404, detail="Dashboard asset not found")
            selected_version = await _select_dashboard_version(repo, tenant_id, asset, version)
            return _json_success(
                dashboard=_asset_compact(asset),
                version=_version_compact(selected_version, include_manifest=detail != "compact") if selected_version else None,
            )
    except Exception as e:
        logger.error(f"Error in describe_dashboard_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="describe_dashboard")


async def query_dashboard_wrapper(
    dashboard_id: str,
    data_view_ids: list[str] | None,
    filters: dict[str, Any] | None,
    cursor: str,
    limit: int,
    tenant_id: UUID,
    user_id: UUID,
) -> str:
    try:
        if cursor:
            raise HTTPException(status_code=400, detail="Cursor pagination is not available for dashboard runs yet")
        async with AsyncSessionFactory() as session:
            set_tenant_id(tenant_id)
            await _require_mcp_dashboard_scope(session, tenant_id, user_id, Scope.DASHBOARD_EXPORT)
            run = await DashboardService().query_dashboard(
                session=session,
                tenant_id=tenant_id,
                asset_id=UUID(dashboard_id),
                actor_id=str(user_id),
                actor_type="agent",
                filters=filters or {},
                data_view_ids=data_view_ids,
                correlation_id="mcp",
            )
            return _json_success(run=_compact_dashboard_run(run, limit=limit))
    except Exception as e:
        logger.error(f"Error in query_dashboard_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="query_dashboard")


async def get_dashboard_state_wrapper(
    dashboard_id: str,
    filters_json: str,
    data_view_ids: list[str] | None,
    tenant_id: UUID,
    user_id: UUID,
    limit: int = MCP_DASHBOARD_DEFAULT_LIMIT,
) -> str:
    try:
        filters = json.loads(filters_json or "{}")
        run_result = await query_dashboard_wrapper(dashboard_id, data_view_ids, filters, "", limit, tenant_id, user_id)
        run_payload = json.loads(run_result)
        if not run_payload.get("success"):
            return run_result
        return _json_success(state=run_payload["run"])
    except Exception as e:
        logger.error(f"Error in get_dashboard_state_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="get_dashboard_state")


async def explain_dashboard_tile_wrapper(
    dashboard_id: str,
    tile_id: str,
    tenant_id: UUID,
    user_id: UUID,
) -> str:
    try:
        asset_id = UUID(dashboard_id)
        async with AsyncSessionFactory() as session:
            set_tenant_id(tenant_id)
            await _require_mcp_dashboard_scope(session, tenant_id, user_id, Scope.DASHBOARD_READ)
            repo = DashboardRepository(session)
            asset = await repo.get_asset(asset_id, tenant_id)
            if not asset:
                raise HTTPException(status_code=404, detail="Dashboard asset not found")
            version = await _select_dashboard_version(repo, tenant_id, asset, "published")
            if not version or not version.manifest_json:
                raise HTTPException(status_code=404, detail="Dashboard version not found")
            manifest = version.manifest_json
            tile = next((item for item in manifest.get("tiles", []) if item.get("id") == tile_id), None)
            if not tile:
                raise HTTPException(status_code=404, detail="Dashboard tile not found")
            data_view = next(
                (item for item in manifest.get("data_views", []) if item.get("id") == tile.get("data_view_id")),
                None,
            )
            return _json_success(
                tile=tile,
                data_view=data_view,
                pinned_versions={
                    "semantic_models": version.pinned_model_versions_json or {},
                    "source_snapshots": version.pinned_source_snapshots_json or [],
                },
                lineage=_lineage_from_manifest(manifest, tile_id),
            )
    except Exception as e:
        logger.error(f"Error in explain_dashboard_tile_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="explain_dashboard_tile")


async def get_dashboard_lineage_wrapper(
    dashboard_id: str,
    tile_id: str,
    tenant_id: UUID,
    user_id: UUID,
) -> str:
    try:
        asset_id = UUID(dashboard_id)
        async with AsyncSessionFactory() as session:
            set_tenant_id(tenant_id)
            await _require_mcp_dashboard_scope(session, tenant_id, user_id, Scope.DASHBOARD_READ)
            repo = DashboardRepository(session)
            asset = await repo.get_asset(asset_id, tenant_id)
            if not asset:
                raise HTTPException(status_code=404, detail="Dashboard asset not found")
            version = await _select_dashboard_version(repo, tenant_id, asset, "published")
            return _json_success(
                dashboard_id=str(asset.id),
                version_id=str(version.id) if version else None,
                lineage=_lineage_from_manifest(version.manifest_json if version else None, tile_id),
            )
    except Exception as e:
        logger.error(f"Error in get_dashboard_lineage_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="get_dashboard_lineage")


async def create_dashboard_draft_wrapper(
    slug: str,
    notebook_id: str,
    manifest_json: str,
    tenant_id: UUID,
    user_id: UUID,
    description: str = "",
    tags: list[str] | None = None,
) -> str:
    try:
        manifest = json.loads(manifest_json)
        notebook_uuid = UUID(notebook_id)
        async with AsyncSessionFactory() as session:
            set_tenant_id(tenant_id)
            await _assert_mcp_notebook_access(session, tenant_id, user_id, notebook_uuid)
            asset = await DashboardService().create_asset_draft(
                session=session,
                tenant_id=tenant_id,
                actor_id=user_id,
                manifest_payload=manifest,
                slug=slug,
                notebook_id=notebook_uuid,
                description=description,
                tags=tags or [],
                actor_type="agent",
            )
            return _json_success(dashboard=_asset_compact(asset))
    except Exception as e:
        logger.error(f"Error in create_dashboard_draft_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="create_dashboard_draft")


async def patch_dashboard_draft_wrapper(
    dashboard_id: str,
    base_etag: str,
    json_patch: str,
    change_summary: str,
    tenant_id: UUID,
    user_id: UUID,
) -> str:
    try:
        patch_operations = json.loads(json_patch or "[]")
        if not isinstance(patch_operations, list):
            raise HTTPException(status_code=400, detail="json_patch must be a JSON array")
        async with AsyncSessionFactory() as session:
            set_tenant_id(tenant_id)
            await _require_mcp_dashboard_scope(session, tenant_id, user_id, Scope.DASHBOARD_SHARE)
            version = await DashboardService().apply_draft_patch(
                session=session,
                tenant_id=tenant_id,
                asset_id=UUID(dashboard_id),
                actor_id=user_id,
                base_etag=base_etag,
                patch_operations=patch_operations,
                change_summary=change_summary,
                actor_type="agent",
            )
            return _json_success(version=_version_compact(version, include_manifest=True))
    except Exception as e:
        logger.error(f"Error in patch_dashboard_draft_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="patch_dashboard_draft")


async def validate_dashboard_wrapper(dashboard_id: str, tenant_id: UUID, user_id: UUID) -> str:
    try:
        async with AsyncSessionFactory() as session:
            set_tenant_id(tenant_id)
            await _require_mcp_dashboard_scope(session, tenant_id, user_id, Scope.DASHBOARD_SHARE)
            repo = DashboardRepository(session)
            asset = await repo.get_asset(UUID(dashboard_id), tenant_id)
            if not asset:
                raise HTTPException(status_code=404, detail="Dashboard asset not found")
            if not asset.current_draft_version_id:
                raise HTTPException(status_code=409, detail="Dashboard has no editable draft")
            draft = await repo.get_asset_version(
                tenant_id=tenant_id,
                asset_id=asset.id,
                version_id=asset.current_draft_version_id,
            )
            if not draft or not draft.manifest_json:
                raise HTTPException(status_code=404, detail="Dashboard draft not found")
            manifest = DashboardService.validate_manifest_payload(draft.manifest_json)
            return _json_success(validation=DashboardService.validation_summary(manifest), manifest=manifest)
    except Exception as e:
        logger.error(f"Error in validate_dashboard_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="validate_dashboard")


async def preview_dashboard_wrapper(
    dashboard_id: str,
    filters: dict[str, Any] | None,
    data_view_ids: list[str] | None,
    tenant_id: UUID,
    user_id: UUID,
    limit: int = MCP_DASHBOARD_DEFAULT_LIMIT,
) -> str:
    try:
        async with AsyncSessionFactory() as session:
            set_tenant_id(tenant_id)
            await _require_mcp_dashboard_scope(session, tenant_id, user_id, Scope.DASHBOARD_EXPORT)
            run = await DashboardService().preview_dashboard(
                session=session,
                tenant_id=tenant_id,
                asset_id=UUID(dashboard_id),
                actor_id=str(user_id),
                actor_type="agent",
                filters=filters or {},
                data_view_ids=data_view_ids,
                correlation_id="mcp-preview",
            )
            return _json_success(run=_compact_dashboard_run(run, limit=limit))
    except Exception as e:
        logger.error(f"Error in preview_dashboard_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="preview_dashboard")


async def publish_dashboard_wrapper(
    dashboard_id: str,
    base_etag: str,
    change_summary: str,
    tenant_id: UUID,
    user_id: UUID,
) -> str:
    try:
        async with AsyncSessionFactory() as session:
            set_tenant_id(tenant_id)
            await _require_mcp_dashboard_scope(session, tenant_id, user_id, Scope.DASHBOARD_SHARE)
            version = await DashboardService().publish(
                session=session,
                tenant_id=tenant_id,
                asset_id=UUID(dashboard_id),
                actor_id=user_id,
                base_etag=base_etag,
                change_summary=change_summary,
                actor_type="agent",
            )
            return _json_success(version=_version_compact(version, include_manifest=True))
    except Exception as e:
        logger.error(f"Error in publish_dashboard_wrapper: {e}", exc_info=True)
        return _json_error(e, operation="publish_dashboard")
