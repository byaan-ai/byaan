import base64
import json
import os
from typing import Any
from uuid import UUID

from agents import function_tool
from agents.run_context import RunContextWrapper

from server.auth.tenant_context import set_tenant_id
from server.db.session import get_async_session
from server.prompts.chart_styling import get_chart_instructions
from server.prompts.defaults import DEFAULT_STYLE_GUIDELINES, DEFAULT_USER_INSTRUCTIONS
from server.repositories.connections import ConnectionRepository
from server.repositories.dashboard import DashboardRepository
from server.repositories.datasets import DatasetRepository
from server.repositories.queries import QueryRepository
from server.repositories.settings import SettingRepository
from server.services.connections import ConnectionService
from server.services.database_operations import DatabaseOperationsService
from server.services.dataset import DatasetService
from server.services.file_operations import DataFrameFileService
from server.services.filter_inference_service import DashboardFilterInferenceService
from server.services.posthog_service import PostHogService
from server.services.query_service import QueryService
from server.services.redaction_service import RedactionService
from server.services.screenshot_service import ScreenshotService, ScreenshotServiceError
from server.tools.plan_tools import check_plan_gate
from server.utils.custom_logger import get_logger
from server.utils.dashboard_editing import (
    DashboardEditError,
    DashboardPatchError,
    DashboardSearchReplaceError,
    apply_dashboard_patch,
    apply_search_replace,
)

logger = get_logger(__name__)


def _auto_bootstrap_filters_enabled() -> bool:
    raw_value = os.getenv("AUTO_BOOTSTRAP_DASHBOARD_FILTERS", "true").strip().lower()
    return raw_value not in {"0", "false", "no", "off"}


async def _load_dashboard_body(
    ctx: RunContextWrapper[Any], dashboard_repo: DashboardRepository
) -> tuple[str, int | None]:
    notebook_id = ctx.context.get("notebook_id")
    session_version = ctx.context.get("session_version")

    if session_version:
        dashboard = await dashboard_repo.get_version(notebook_id, session_version)
        if not dashboard:
            raise DashboardEditError(f"Session version {session_version} not found for notebook {notebook_id}.")
        return dashboard.html_content, session_version

    base_version = ctx.context.get("current_version")
    if base_version:
        dashboard = await dashboard_repo.get_version(notebook_id, base_version)
        if not dashboard:
            raise DashboardEditError(f"Base version {base_version} not found for notebook {notebook_id}.")
        return dashboard.html_content, None

    latest_dashboard = await dashboard_repo.get_latest_version(notebook_id)
    if latest_dashboard:
        return latest_dashboard.html_content, None

    BASE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard</title>
</head>
<body>
    <div id="root"></div>
    <p>No content yet. Start building your dashboard!</p>
</body>
</html>"""
    return BASE_TEMPLATE, None


async def _persist_dashboard_body(
    ctx: RunContextWrapper[Any],
    dashboard_repo: DashboardRepository,
    session,
    new_content: str,
    session_version: int | None,
) -> int:
    notebook_id = ctx.context.get("notebook_id")
    tenant_id = ctx.context.get("tenant_id")
    set_tenant_id(tenant_id)

    if session_version:
        await dashboard_repo.update_version_content(notebook_id, session_version, new_content)
        await session.flush()
        return session_version

    new_dashboard = await dashboard_repo.create_with_version(notebook_id, new_content, tenant_id)
    ctx.context["session_version"] = new_dashboard.version_num
    return new_dashboard.version_num


@function_tool
async def dashboard_search_replace(ctx: RunContextWrapper[Any], diff_content: str) -> str:
    """
    Apply one or more SEARCH/REPLACE blocks to the Dashboard body.

    Each block must follow:
    <<<<<<< SEARCH
    ...existing content...
    =======
    ...replacement content...
    >>>>>>> REPLACE
    """
    if gate_error := check_plan_gate(ctx):
        return gate_error

    notebook_id = ctx.context.get("notebook_id")
    tenant_id = ctx.context.get("tenant_id")

    try:
        async for session in get_async_session():
            # Set tenant context inside async for loop
            set_tenant_id(tenant_id)

            dashboard_repo = DashboardRepository(session)
            try:
                content, session_version = await _load_dashboard_body(ctx, dashboard_repo)
            except DashboardEditError as load_error:
                return json.dumps(
                    {
                        "success": False,
                        "error": str(load_error),
                        "notebook_id": notebook_id,
                    },
                    indent=2,
                    default=str,
                )

            try:
                new_content = apply_search_replace(content, diff_content)
            except DashboardSearchReplaceError as sre:
                return json.dumps(
                    {
                        "success": False,
                        "error": str(sre),
                        "notebook_id": notebook_id,
                    },
                    indent=2,
                    default=str,
                )

            version_num = await _persist_dashboard_body(ctx, dashboard_repo, session, new_content, session_version)
            logger.info(
                "Applied dashboard_search_replace for notebook %s version %s",
                notebook_id,
                version_num,
            )
            return json.dumps(
                {
                    "success": True,
                    "message": "Applied search/replace blocks",
                    "notebook_id": notebook_id,
                    "version_num": version_num,
                },
                indent=2,
                default=str,
            )
    except Exception as e:
        logger.error("Error in dashboard_search_replace: %s", e, exc_info=True)
        PostHogService.capture_error(
            error=e,
            context={
                "function": "dashboard_search_replace",
                "notebook_id": notebook_id,
            },
        )
        return json.dumps({"success": False, "error": str(e), "notebook_id": notebook_id}, indent=2, default=str)


@function_tool
async def apply_html_patch(ctx: RunContextWrapper[Any], patch_text: str) -> str:
    """
    Apply a structured patch (*** Begin Patch ... *** End Patch) to the Dashboard body.
    """
    if gate_error := check_plan_gate(ctx):
        return gate_error

    notebook_id = ctx.context.get("notebook_id")
    tenant_id = ctx.context.get("tenant_id")

    try:
        async for session in get_async_session():
            # Set tenant context inside async for loop
            set_tenant_id(tenant_id)

            dashboard_repo = DashboardRepository(session)
            try:
                content, session_version = await _load_dashboard_body(ctx, dashboard_repo)
            except DashboardEditError as load_error:
                return json.dumps(
                    {
                        "success": False,
                        "error": str(load_error),
                        "notebook_id": notebook_id,
                    },
                    indent=2,
                    default=str,
                )

            try:
                new_content = apply_dashboard_patch(content, patch_text)
            except DashboardPatchError as p_err:
                return json.dumps(
                    {
                        "success": False,
                        "error": str(p_err),
                        "notebook_id": notebook_id,
                    },
                    indent=2,
                    default=str,
                )

            version_num = await _persist_dashboard_body(ctx, dashboard_repo, session, new_content, session_version)
            logger.info(
                "Applied HTML patch for notebook %s version %s",
                notebook_id,
                version_num,
            )
            return json.dumps(
                {
                    "success": True,
                    "message": "Applied HTML patch",
                    "notebook_id": notebook_id,
                    "version_num": version_num,
                },
                indent=2,
                default=str,
            )
    except Exception as e:
        logger.error("Error in apply_html_patch: %s", e, exc_info=True)
        PostHogService.capture_error(
            error=e,
            context={"function": "apply_html_patch", "notebook_id": notebook_id},
        )
        return json.dumps({"success": False, "error": str(e), "notebook_id": notebook_id}, indent=2, default=str)


@function_tool
async def start_html_generation(ctx: RunContextWrapper[Any]) -> str:
    """Signal that the agent is about to generate or edit dashboard HTML."""
    if gate_error := check_plan_gate(ctx):
        return gate_error

    notebook_id = ctx.context.get("notebook_id")
    current_version = ctx.context.get("current_version")

    try:
        logger.info(
            "HTML generation start signal for notebook %s, current_version: %s",
            notebook_id,
            current_version,
        )

        return json.dumps(
            {
                "success": True,
                "message": "HTML generation started",
                "notebook_id": notebook_id,
                "current_version": current_version,
            },
            indent=2,
            default=str,
        )
    except Exception as e:
        logger.error("Error in start_html_generation: %s", e)
        PostHogService.capture_error(
            error=e,
            context={
                "function": "start_html_generation",
                "notebook_id": notebook_id,
                "current_version": current_version,
            },
        )
        return json.dumps(
            {
                "success": False,
                "error": str(e),
                "notebook_id": notebook_id,
                "current_version": current_version,
            },
            indent=2,
            default=str,
        )


@function_tool
async def save_query(
    ctx: RunContextWrapper[Any], query: str, name: str, connection_id: str, is_dashboard: bool = False
) -> str:
    """
    Save a query to the database for later use in the dashboard.

    Args:
        ctx: Run context wrapper
        query: The SQL/NoSQL query to save
        name: Name for the saved query
        connection_id: Database connection ID to associate the query with
        is_dashboard: If True, signals that a dashboard should be created with this saved query

    Returns:
        JSON string with save result including query_id and dashboard_requested flag for handoff logic.
    """
    if gate_error := check_plan_gate(ctx):
        return gate_error

    notebook_id = ctx.context.get("notebook_id")
    db_type = ctx.context.get("db_type")

    # connection_id is required for multi-datasource notebooks - agent must provide it explicitly
    if not connection_id:
        return json.dumps(
            {
                "success": False,
                "error": "connection_id is required. Please specify which database connection to use for this query.",
                "notebook_id": notebook_id,
            },
            indent=2,
            default=str,
        )

    logger.info(
        f"Saving query '{name}' - Type: {db_type}, Notebook: {notebook_id}, Connection: {connection_id}, Dashboard: {is_dashboard}"
    )

    tenant_id = ctx.context.get("tenant_id")
    user_id = ctx.context.get("user_id")

    try:
        async for session in get_async_session():
            try:
                # Set tenant context for this tool execution (must be inside async for loop)
                set_tenant_id(tenant_id)

                result = await QueryService.execute_and_save_query(
                    session=session,
                    query=query,
                    connection_id=connection_id,
                    notebook_id=notebook_id,
                    db_type=db_type,
                    name=name,
                    created_by=str(user_id) if user_id else None,
                )

                if result["success"]:
                    query_id = result.get("query_id")
                    logger.info(f"Query '{name}' saved with ID: {query_id}, dashboard_requested: {is_dashboard}")

                    bootstrap_status = "skipped"
                    bootstrap_message = "Auto filter bootstrap not requested"
                    auto_filters_added: list[dict[str, Any]] = []
                    auto_filters_added_count = 0
                    updated_query_contracts: list[str] = []

                    if is_dashboard:
                        if _auto_bootstrap_filters_enabled():
                            try:
                                bootstrap_result = await DashboardFilterInferenceService.bootstrap_for_saved_query(
                                    session=session,
                                    notebook_id=str(notebook_id),
                                    query_id=str(query_id),
                                )
                                bootstrap_status = str(bootstrap_result.get("status", "skipped"))
                                bootstrap_message = str(bootstrap_result.get("message", ""))
                                auto_filters_added = bootstrap_result.get("filters", []) or []
                                auto_filters_added_count = int(bootstrap_result.get("added_count", 0))
                                updated_query_contracts = bootstrap_result.get("updated_query_contracts", []) or []
                            except Exception as bootstrap_error:
                                logger.error(
                                    "Auto filter bootstrap failed for query %s: %s",
                                    query_id,
                                    bootstrap_error,
                                    exc_info=True,
                                )
                                bootstrap_status = "error"
                                bootstrap_message = str(bootstrap_error)
                        else:
                            bootstrap_status = "skipped"
                            bootstrap_message = "AUTO_BOOTSTRAP_DASHBOARD_FILTERS is disabled"

                    return json.dumps(
                        {
                            "success": True,
                            "message": f"Query '{name}' saved successfully",
                            "query_id": str(query_id),
                            "query_name": name,
                            "notebook_id": str(notebook_id),
                            "schema": result.get("generated_schema", {}),
                            "dashboard_requested": is_dashboard,
                            "filter_bootstrap_status": bootstrap_status,
                            "filter_bootstrap_message": bootstrap_message,
                            "auto_filters_added_count": auto_filters_added_count,
                            "auto_filters_added": auto_filters_added,
                            "updated_query_contracts": updated_query_contracts,
                        },
                        indent=2,
                        default=str,
                    )
                else:
                    error_msg = result.get("error", "Unknown error occurred")
                    logger.error(f"Failed to save query '{name}': {error_msg}")
                    return json.dumps(
                        {
                            "success": False,
                            "error": error_msg,
                            "notebook_id": str(notebook_id),
                            "dashboard_requested": False,
                        },
                        indent=2,
                        default=str,
                    )
            finally:
                await session.close()

    except Exception as e:
        logger.error(
            f"Error in save_query: {str(e)}",
            exc_info=True,
            posthog_context={
                "function": "save_query",
                "notebook_id": notebook_id if "notebook_id" in locals() else None,
                "db_type": db_type if "db_type" in locals() else None,
                "query_name": name if "name" in locals() else None,
            },
        )
        return json.dumps(
            {
                "success": False,
                "error": str(e),
                "notebook_id": str(notebook_id) if notebook_id else None,
                "dashboard_requested": False,
            },
            indent=2,
            default=str,
        )


@function_tool
async def get_database_schema(ctx: RunContextWrapper[Any]) -> str:
    """
    Get the complete database schema for ALL databases connected to this notebook.

    Use this tool when users ask about:
    - What tables/collections exist in the database(s)
    - Database schema or structure
    - Available fields/columns
    - Data types and relationships
    - Better understand the data from user-provided datasource annotations

    This provides immediate access to all database schemas in the notebook.
    When multiple databases are present, they will be labeled clearly (e.g., "Database 1 (PostgreSQL)", "Database 2 (MongoDB)").

    IMPORTANT: This tool returns the connection_id for EACH database. You'll need these connection_ids
    when executing queries using execute_sql_query or execute_mongo_query tools, or when saving queries
    with the save_query tool. Each database in the response includes:
    - connection_id: The ID needed to execute queries against this specific database
    - connection_name: Human-readable name of the connection
    - db_type: Database type (pg, mongo, mysql, etc.)
    - formatted_schema: Complete schema information with user column annotations / table descriptions
    - schema_summary: User-friendly summary of tables/collections
    Note: The formatted_schema may contain optional user-provided table descriptions and column annotations (shown in parentheses). These are optional context hints that users can add to help understand the data
    Args:
        ctx: Run context wrapper containing notebook_id and connection info

    Returns:
        JSON string with formatted schemas for all databases, including connection_id and metadata for each connection
    """

    notebook_id = ctx.context.get("notebook_id")
    tenant_id = ctx.context.get("tenant_id")

    logger.info(f"Fetching database schemas for notebook: {notebook_id}")

    try:
        async for session in get_async_session():
            # Set tenant context inside async for loop
            set_tenant_id(tenant_id)

            try:
                # Get ALL datasets (supports both connection and file types)
                datasets = await DatasetService.get_datasets_by_notebook(session, notebook_id)

                if not datasets:
                    return json.dumps(
                        {
                            "success": False,
                            "error": "No datasets found for this notebook",
                            "notebook_id": notebook_id,
                        },
                        indent=2,
                        default=str,
                    )

                # Multi-database support: Process ALL datasets
                conn_repo = ConnectionRepository(session)
                databases = []
                database_types = set()

                # Process each dataset
                for idx, dataset in enumerate(datasets, start=1):
                    logger.info(f"Processing dataset {idx}: {dataset.id}, type: {dataset.type}")

                    # Get cached schema based on dataset type
                    cached_schema = None
                    connection = None
                    effective_db_type = None
                    datasource_id = None

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
                    else:
                        logger.warning(f"Unsupported dataset type: {dataset.type}, skipping")
                        continue

                    if not cached_schema:
                        logger.warning(f"No cached schema for dataset {dataset.id}, skipping")
                        continue

                    database_types.add(effective_db_type)

                    # Annotate schema with user annotations
                    logger.info(f"Annotating schema for datasource: {datasource_id}")
                    annotated_schema = await DatabaseOperationsService.annotate_schema_with_user_annotations(
                        datasource_id, cached_schema, session
                    )

                    # Format schema for the prompt
                    if dataset.type == "file":
                        formatted_schema = DataFrameFileService.format_file_schema_for_prompt(annotated_schema)
                    else:
                        formatted_schema = DatabaseOperationsService.format_schema_for_prompt(
                            annotated_schema, effective_db_type
                        )

                    # Prepare user-friendly summary (navigational info only, column details are in formatted_schema)
                    schema_summary = {}
                    if dataset.type == "file":
                        # File dataset schema
                        schema_tables = annotated_schema.get("schema", {})
                        visible_files = [
                            {
                                "name": table_info.get("filename", table_name),
                                "type": table_info.get("file_type", "unknown"),
                            }
                            for table_name, table_info in schema_tables.items()
                            if not table_info.get("redacted_table")
                        ]
                        schema_summary = {
                            "type": "DuckDB File Dataset",
                            "dataset_id": str(dataset.id),
                            "files_count": len(visible_files),
                            "files": visible_files,
                        }
                    elif effective_db_type == "mongo":
                        # MongoDB schema
                        collections = annotated_schema.get("schema", {})
                        visible_collections = [
                            {
                                "name": coll_name,
                                "document_count": coll_info.get("count", 0),
                            }
                            for coll_name, coll_info in collections.items()
                            if not coll_info.get("redacted_table")
                        ]
                        schema_summary = {
                            "type": "MongoDB",
                            "database": annotated_schema.get("database_name", "unknown"),
                            "collections_count": len(visible_collections),
                            "collections": visible_collections,
                        }
                    else:
                        # SQL database schema
                        tables = annotated_schema.get("schema", {})
                        visible_tables = [t for t, info in tables.items() if not info.get("redacted_table")]
                        schema_summary = {
                            "type": "SQL/PostgreSQL"
                            if effective_db_type == "pg"
                            else f"SQL/{effective_db_type.upper()}",
                            "database": annotated_schema.get("database_name", "unknown"),
                            "tables_count": len(visible_tables),
                            "tables": visible_tables,
                        }

                    logger.info(f"Successfully fetched schema for notebook {notebook_id}")

                    logger.info(f"Schema Summary: {schema_summary}")
                    logger.info(f"Formatted Schema here: {formatted_schema}")

                    # Add to databases list with appropriate IDs
                    database_entry = {
                        "database_number": idx,
                        "dataset_id": str(dataset.id),
                        "dataset_type": dataset.type,
                        "db_type": effective_db_type,
                        "formatted_schema": formatted_schema,
                        "schema_summary": schema_summary,
                    }

                    # Add connection-specific fields for connection datasets
                    if dataset.type == "connection" and connection:
                        database_entry["connection_id"] = str(dataset.connection_id)
                        database_entry["connection_name"] = connection.name

                    databases.append(database_entry)

                if not databases:
                    return json.dumps(
                        {
                            "success": False,
                            "error": "No valid database schemas found. Please refresh the connection schemas.",
                            "notebook_id": notebook_id,
                        },
                        indent=2,
                        default=str,
                    )

                logger.info(f"Successfully fetched {len(databases)} database schema(s) for notebook {notebook_id}")

                # Return multi-database response
                return json.dumps(
                    {
                        "success": True,
                        "notebook_id": str(notebook_id),
                        "total_databases": len(databases),
                        "database_types": sorted(database_types),
                        "databases": databases,
                    },
                    indent=2,
                    default=str,
                )

            finally:
                await session.close()

    except Exception as e:
        logger.error(
            f"Error fetching database schema: {str(e)}",
            exc_info=True,
            posthog_context={
                "function": "get_database_schema",
                "notebook_id": notebook_id if "notebook_id" in locals() else None,
            },
        )
        return json.dumps({"success": False, "error": str(e), "notebook_id": notebook_id}, indent=2, default=str)


@function_tool
async def get_chart_styling(ctx: RunContextWrapper[Any], chart_types: list[str] | None = None) -> str:
    """
    Get chart styling examples for dashboard creation.

    MUST call this tool BEFORE creating/modifying any dashboard or chart.
    Use the chart styling as given in examples and do not simply the charts

    Available chart types: "bar_chart", "horizontal_bar_chart", "line_chart", "area_chart",
    "pie_chart", "donut_chart", "scatter_plot", "stacked_bar_chart", "grouped_bar_chart"

    Usage:
    - For dashboard with unspecified charts: chart_types=None (returns all examples)
    - For single chart: chart_types=["pie_chart"]
    - For multiple specific charts: chart_types=["pie_chart", "bar_chart", "line_chart"]

    Args:
        ctx: Run context wrapper
        chart_types: List of chart types to get examples for, or None for all (default: None)

    Returns:
        JSON with chart styling examples and patterns
    """
    notebook_id = ctx.context.get("notebook_id")

    logger.info(f"Fetching chart styling examples for notebook: {notebook_id}, chart_types: {chart_types}")

    try:
        # Get chart instructions based on types
        chart_instructions = get_chart_instructions(chart_types)

        logger.info(
            f"Successfully retrieved chart styling examples for {chart_types or 'all'} for notebook {notebook_id}"
        )

        return json.dumps(
            {
                "success": True,
                "chart_types": chart_types or "all",
                "examples": chart_instructions,
                "notebook_id": notebook_id,
            },
            indent=2,
            default=str,
        )

    except Exception as e:
        logger.error(f"Error fetching chart styling examples: {str(e)}")
        return json.dumps(
            {
                "success": False,
                "error": str(e),
                "notebook_id": notebook_id,
            },
            indent=2,
            default=str,
        )


@function_tool
async def get_existing_html(ctx: RunContextWrapper[Any]) -> str:
    """
    Get the existing HTML content from the notebook's dashboard in the database.

    Use this tool to fetch the current dashboard HTML before making modifications.
    This provides the complete HTML content that can be analyzed and edited.

    IMPORTANT - USE FOR SELF-REVIEW:
    After creating or editing dashboard HTML, ALWAYS call this tool to review your work.
    When reviewing HTML with JSX/React code:
    - Carefully check for syntax errors in the <script type="text/babel"> section
    - Look for unbalanced braces {} in JSX expressions
    - Verify React.createElement calls have proper parentheses and commas
    - Check that JSX attributes use correct React prop names (className not class)
    - Ensure all tags are properly closed
    If you spot errors, you can use apply_search_replace  to fix them immediately.

    Args:
        ctx: Run context wrapper containing notebook_id

    Returns:
        JSON string with HTML content and metadata
    """
    notebook_id = ctx.context.get("notebook_id")
    tenant_id = ctx.context.get("tenant_id")

    logger.info(f"Fetching existing HTML for notebook: {notebook_id}")

    try:
        if not notebook_id:
            return json.dumps(
                {
                    "success": False,
                    "error": "No notebook_id provided in context",
                    "html": None,
                },
                indent=2,
                default=str,
            )

        async for session in get_async_session():
            # Set tenant context inside async for loop
            set_tenant_id(tenant_id)

            try:
                dashboard_repo = DashboardRepository(session)
                latest_dashboard = await dashboard_repo.get_latest_version(notebook_id)

                if not latest_dashboard:
                    logger.info(f"HTML dashboard does not exist yet for notebook {notebook_id}")
                    starter_html, _ = await _load_dashboard_body(ctx, dashboard_repo)
                    return json.dumps(
                        {
                            "success": True,
                            "notebook_id": notebook_id,
                            "html": starter_html,
                            "version_num": None,
                            "is_starter_template": True,
                            "message": "No existing HTML dashboard found. Returning starter template for editing.",
                        },
                        indent=2,
                        default=str,
                    )

                # Get the HTML content from database
                html_content = latest_dashboard.html_content
                html_size = len(html_content.encode("utf-8"))

                logger.info(
                    f"Successfully fetched HTML for notebook {notebook_id} (size: {html_size} bytes, version: {latest_dashboard.version_num})"
                )

                return json.dumps(
                    {
                        "success": True,
                        "notebook_id": notebook_id,
                        "html": html_content,
                        "html_size": html_size,
                        "version_num": latest_dashboard.version_num,
                        "created_at": (
                            latest_dashboard.created_at.isoformat() if latest_dashboard.created_at else None
                        ),
                    },
                    indent=2,
                    default=str,
                )
            finally:
                await session.close()

    except Exception as e:
        logger.error(
            f"Error fetching existing HTML: {str(e)}",
            exc_info=True,
            posthog_context={
                "function": "get_existing_html",
                "notebook_id": notebook_id if "notebook_id" in locals() else None,
            },
        )
        return json.dumps({"success": False, "error": str(e), "notebook_id": notebook_id}, indent=2, default=str)


@function_tool
async def saved_query_schema(ctx: RunContextWrapper[Any]) -> str:
    """
    Get schema insights for all saved queries in the current notebook. This tool returns detailed information about
    what data schema each query will return, including column names, types, and query metadata.

    Use this when you need to understand what data structures are available in the current notebook before
    creating dashboards or working with saved queries.

    Args:
        ctx: Run context wrapper containing notebook_id

    Returns:
        JSON string with schema insights for all queries in the current notebook
    """
    notebook_id = ctx.context.get("notebook_id")
    tenant_id = ctx.context.get("tenant_id")

    try:
        # Validate notebook_id exists
        if not notebook_id:
            return json.dumps(
                {"success": False, "error": "No notebook_id provided in context"},
                indent=2,
                default=str,
            )

        query_contexts = []

        async for session in get_async_session():
            # Set tenant context inside async for loop
            set_tenant_id(tenant_id)

            try:
                query_repo = QueryRepository(session)

                # Get all queries for this notebook
                queries_list = await query_repo.get_by_notebook_id(notebook_id)

                if not queries_list:
                    return json.dumps(
                        {
                            "success": True,
                            "notebook_id": notebook_id,
                            "total_queries": 0,
                            "query_insights": [],
                            "message": "No saved queries found for this notebook",
                        },
                        indent=2,
                        default=str,
                    )

                # Fetch full details for each query
                for query_id, query_name in queries_list:
                    try:
                        saved_query = await query_repo.get_with_relations(query_id)

                        if saved_query:
                            # Get db_type from dataset
                            db_type = None
                            if saved_query.dataset:
                                if saved_query.dataset.type == "connection" and saved_query.dataset.connection:
                                    db_type = saved_query.dataset.connection.type
                                elif saved_query.dataset.type == "file" and saved_query.dataset.files:
                                    db_type = "duckdb"

                            single_query_context = {
                                "id": saved_query.id,
                                "name": saved_query.name,
                                "query": saved_query.query,
                                "output_schema": saved_query.output_schema,
                                "db_type": db_type,
                                "dataset_id": saved_query.dataset_id,
                                "created_at": (
                                    str(saved_query.created_at) if hasattr(saved_query, "created_at") else None
                                ),
                            }
                            query_contexts.append(single_query_context)
                            logger.info(f"Fetched schema context for query: {saved_query.name} (ID: {query_id})")
                        else:
                            logger.warning(f"Query not found for ID: {query_id}")
                    except Exception as single_query_error:
                        logger.error(
                            f"Error fetching query {query_id}: {str(single_query_error)}",
                            exc_info=True,
                            posthog_context={
                                "function": "saved_query_schema.single_query",
                                "query_id": query_id,
                                "notebook_id": notebook_id,
                            },
                        )
            finally:
                await session.close()

        return json.dumps(
            {
                "success": True,
                "notebook_id": notebook_id,
                "total_queries": len(query_contexts),
                "query_insights": query_contexts,
            },
            indent=2,
            default=str,
        )

    except Exception as e:
        logger.error(
            f"Error in saved_query_schema: {str(e)}",
            exc_info=True,
            posthog_context={
                "function": "saved_query_schema",
                "notebook_id": notebook_id if "notebook_id" in locals() else None,
            },
        )
        return json.dumps(
            {
                "success": False,
                "error": str(e),
                "notebook_id": notebook_id if notebook_id else None,
            },
            indent=2,
            default=str,
        )


@function_tool
async def get_user_instructions(ctx: RunContextWrapper[Any]) -> str:
    """
    Get the user's global instructions including query preferences, AI-remembered patterns, and general guidelines.

    Use this tool:
    - Before generating any queries to understand user preferences
    - To load AI-remembered patterns and preferences
    - To maintain consistency across all interactions

    Args:
        ctx: Run context wrapper

    Returns:
        JSON string with user's instructions
    """
    try:
        logger.info("Fetching user instructions")
        tenant_id = ctx.context.get("tenant_id")

        async for session in get_async_session():
            try:
                setting = None
                if tenant_id:
                    set_tenant_id(tenant_id)
                    repo = SettingRepository(session)
                    setting = await repo.get_by_key("workspace_instructions")

                if setting:
                    instructions = setting.setting_value
                    logger.info("Successfully retrieved custom instructions")
                else:
                    instructions = DEFAULT_USER_INSTRUCTIONS
                    logger.info("Using default instructions (user hasn't customized yet)")

                return json.dumps(
                    {
                        "success": True,
                        "instructions": instructions,
                        "is_custom": setting is not None,
                    },
                    indent=2,
                    default=str,
                )
            finally:
                await session.close()

    except Exception as e:
        logger.error(
            f"Error fetching instructions: {str(e)}",
            exc_info=True,
            posthog_context={
                "function": "get_user_instructions",
            },
        )
        return json.dumps(
            {
                "success": True,
                "instructions": DEFAULT_USER_INSTRUCTIONS,
                "is_custom": False,
                "error_note": "Using defaults due to error fetching custom instructions",
            },
            indent=2,
            default=str,
        )


@function_tool
async def get_user_style_guidelines(ctx: RunContextWrapper[Any]) -> str:
    """
    Get user's custom brand and style guidelines for visualizations.

    This tool returns the user's personalized styling preferences including:
    - Brand color palette (primary, secondary, accent colors)
    - Chart styling preferences (types, layouts)
    - Data visualization best practices

    Use this tool:
    - Before creating or editing any dashboard/visualization
    - To ensure visualizations match user's brand identity
    - To maintain consistent styling across all charts

    Args:
        ctx: Run context wrapper

    Returns:
        JSON string with user's style guidelines
    """
    try:
        logger.info("Fetching user's style guidelines")
        tenant_id = ctx.context.get("tenant_id")

        async for session in get_async_session():
            try:
                setting = None
                if tenant_id:
                    set_tenant_id(tenant_id)
                    repo = SettingRepository(session)
                    setting = await repo.get_by_key("workspace_style_guidelines")

                if setting:
                    guidelines = setting.setting_value
                    logger.info("Successfully retrieved custom style guidelines")
                else:
                    guidelines = DEFAULT_STYLE_GUIDELINES
                    logger.info("Using default style guidelines (user hasn't customized yet)")

                return json.dumps(
                    {
                        "success": True,
                        "guidelines": guidelines,
                        "is_custom": setting is not None,
                    },
                    indent=2,
                    default=str,
                )
            finally:
                await session.close()

    except Exception as e:
        logger.error(
            f"Error fetching style guidelines: {str(e)}",
            exc_info=True,
            posthog_context={
                "function": "get_user_style_guidelines",
            },
        )
        return json.dumps(
            {
                "success": True,
                "guidelines": DEFAULT_STYLE_GUIDELINES,
                "is_custom": False,
                "error_note": "Using defaults due to error fetching custom guidelines",
            },
            indent=2,
            default=str,
        )


@function_tool
async def save_skill_query(
    ctx: RunContextWrapper[Any],
    name: str,
    skill_name: str,
    api_config: str,
    output_schema: str,
    scope: str = "user",
    is_dashboard: bool = False,
) -> str:
    """
    Save an API query configuration for dashboard use.

    Use this tool after successfully calling execute_skill_api() to persist the API call
    configuration. This enables dashboards to refresh data by re-executing the same API call.

    Args:
        ctx: Run context wrapper
        name: Name for the saved query (e.g., "Linear Issues", "Notion Pages")
        skill_name: Name of the skill (e.g., "linear", "notion")
        api_config: JSON string containing the API call configuration:
            - url: The API endpoint URL
            - method: HTTP method (GET, POST, etc.)
            - is_graphql: Whether this is a GraphQL request
            - graphql_query: The GraphQL query string (if is_graphql=True)
            - graphql_variables: GraphQL variables as JSON object (optional)
            - body: Request body for REST requests (optional)
        output_schema: JSON string describing the expected response structure
        scope: Credential scope to use ("user" or "org"), defaults to "user"
        is_dashboard: If True, signals that a dashboard should be created with this query

    Returns:
        JSON string with save result including query_id
    """
    notebook_id = ctx.context.get("notebook_id")
    tenant_id = ctx.context.get("tenant_id")
    user_id = ctx.context.get("user_id")

    logger.info(f"Saving skill query '{name}' for skill '{skill_name}' - Notebook: {notebook_id}")

    try:
        config_dict = json.loads(api_config)
    except json.JSONDecodeError:
        return json.dumps({"success": False, "error": "Invalid api_config JSON"}, indent=2)

    try:
        schema_dict = json.loads(output_schema) if output_schema else {}
    except json.JSONDecodeError:
        schema_dict = {}

    try:
        async for session in get_async_session():
            try:
                set_tenant_id(tenant_id)
                query_repo = QueryRepository(session)

                saved_query = await query_repo.create(
                    {
                        "name": name,
                        "query": "",
                        "output_schema": json.dumps(schema_dict),
                        "dataset_id": None,
                        "notebook_id": notebook_id,
                        "created_by": user_id,
                        "query_type": "api",
                        "skill_name": skill_name,
                        "skill_scope": scope,
                        "api_config": json.dumps(config_dict),
                    }
                )

                logger.info(f"Skill query '{name}' saved with ID: {saved_query.id}")
                return json.dumps(
                    {
                        "success": True,
                        "message": f"API query '{name}' saved successfully",
                        "query_id": str(saved_query.id),
                        "query_name": name,
                        "skill_name": skill_name,
                        "notebook_id": str(notebook_id),
                        "dashboard_requested": is_dashboard,
                    },
                    indent=2,
                    default=str,
                )
            finally:
                await session.close()

    except Exception as e:
        logger.error(f"Error in save_skill_query: {str(e)}", exc_info=True)
        return json.dumps(
            {"success": False, "error": str(e), "notebook_id": str(notebook_id) if notebook_id else None},
            indent=2,
            default=str,
        )


async def _search_datasets_impl(tenant_id: str | None, query: str) -> str:
    logger.info(f"Searching datasets with query: '{query}'")

    try:
        async for session in get_async_session():
            try:
                set_tenant_id(tenant_id)
                dataset_repo = DatasetRepository(session)

                if query.strip():
                    datasets = await dataset_repo.search_by_name(query)
                else:
                    datasets = await dataset_repo.get_all_for_tenant()

                results = []
                for ds in datasets:
                    ds_info = {
                        "id": str(ds.id),
                        "name": ds.name,
                        "type": ds.type,
                    }

                    if ds.description:
                        ds_info["description"] = ds.description

                    if ds.type == "connection" and ds.connection:
                        ds_info["connection_type"] = ds.connection.type
                        ds_info["connection_name"] = ds.connection.name
                        if ds.connection.description:
                            ds_info["connection_description"] = ds.connection.description
                        if ds.connection.schema_cache:
                            try:
                                schema = json.loads(ds.connection.schema_cache)
                                tables = list(schema.get("tables", {}).keys())[:10]
                                if tables:
                                    ds_info["tables"] = tables
                            except json.JSONDecodeError:
                                pass
                    elif ds.type == "file" and ds.files:
                        ds_info["file_count"] = len(ds.files)
                        ds_info["files"] = [{"name": f.name, "type": f.type} for f in ds.files[:5]]
                        if ds.schema_cache:
                            try:
                                schema = json.loads(ds.schema_cache)
                                tables = list(schema.get("tables", {}).keys())[:10]
                                if tables:
                                    ds_info["tables"] = tables
                            except json.JSONDecodeError:
                                pass

                    results.append(ds_info)

                count = len(results)
                if count == 0:
                    msg = (
                        f"No datasources found matching '{query}'. The user has not connected any matching databases or uploaded any matching files yet. "
                        "Ask the user to connect a database or upload a file from the Datasources page."
                        if query
                        else "No datasources available. The user has not connected any databases or uploaded any files yet. "
                        "Ask the user to connect a database or upload a file from the Datasources page."
                    )
                else:
                    msg = f"Found {count} dataset(s) matching '{query}'" if query else f"Found {count} dataset(s)"
                return json.dumps(
                    {"success": True, "datasets": results, "total": len(results), "message": msg},
                    indent=2,
                    default=str,
                )
            finally:
                await session.close()

    except Exception as e:
        logger.error(f"Error in search_datasets: {str(e)}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)}, indent=2, default=str)


@function_tool
async def search_datasets(ctx: RunContextWrapper[Any], query: str = "") -> str:
    """
    Search available datasets by name, description, connection name, and table names.

    Use this tool when the user asks a question but no dataset is pre-selected.
    Search for datasets that might contain relevant data for the user's question.
    Pass an empty string to list ALL available datasets.

    Args:
        ctx: Run context wrapper
        query: Search term to find matching datasets. Empty string returns all datasets.

    Returns:
        JSON string with matching datasets including their IDs, names, types, and basic info
    """
    tenant_id = ctx.context.get("tenant_id")

    return await _search_datasets_impl(tenant_id, query)


def _filter_redacted_from_schema(
    schema: dict, redacted_tables: set[str], redacted_columns: dict[str, set[str]]
) -> dict:
    """Remove redacted tables and columns from a schema dict before returning to the LLM."""
    if not redacted_tables and not redacted_columns:
        return schema

    tables = schema.get("tables", {})
    if tables:
        filtered = {}
        for table_name, table_info in tables.items():
            if table_name in redacted_tables:
                continue
            if table_name in redacted_columns and isinstance(table_info, dict):
                cols = table_info.get("columns", [])
                if cols:
                    table_info = {
                        **table_info,
                        "columns": [c for c in cols if c.get("name") not in redacted_columns[table_name]],
                    }
            filtered[table_name] = table_info
        schema = {**schema, "tables": filtered}

    collections = schema.get("collections", {})
    if collections:
        filtered = {}
        for coll_name, coll_info in collections.items():
            if coll_name in redacted_tables:
                continue
            if coll_name in redacted_columns and isinstance(coll_info, dict):
                fields = coll_info.get("fields", [])
                if fields:
                    coll_info = {
                        **coll_info,
                        "fields": [
                            f
                            for f in fields
                            if (f if isinstance(f, str) else f.get("name")) not in redacted_columns[coll_name]
                        ],
                    }
            filtered[coll_name] = coll_info
        schema = {**schema, "collections": filtered}

    return schema


@function_tool
async def get_dataset_schema_by_id(ctx: RunContextWrapper[Any], dataset_id: str) -> str:
    """
    Get detailed schema for a specific dataset AND associate it with the current notebook.

    Use this after search_datasets() to inspect a dataset's structure before querying.
    This tool auto-associates the dataset with the notebook so you can query it immediately.

    IMPORTANT: The response includes `query_connection_id` which you MUST use when calling
    execute_sql_query or execute_mongo_query tools.

    Args:
        ctx: Run context wrapper
        dataset_id: The dataset ID to get schema for

    Returns:
        JSON string with:
        - query_connection_id: The ID to use in execute_sql_query/execute_mongo_query
        - dataset schema including tables, columns, and types
        - For database datasets: connection details
        - For file datasets: file list and DuckDB schema
    """
    tenant_id = ctx.context.get("tenant_id")
    notebook_id = ctx.context.get("notebook_id")

    logger.info(f"Getting schema for dataset: {dataset_id}, notebook: {notebook_id}")

    try:
        async for session in get_async_session():
            try:
                set_tenant_id(tenant_id)

                dataset = await DatasetService.get_dataset(session, dataset_id)
                if not dataset:
                    return json.dumps(
                        {"success": False, "error": f"Dataset {dataset_id} not found"},
                        indent=2,
                    )

                if notebook_id:
                    try:
                        await DatasetService.associate_dataset_with_notebook(session, dataset_id, notebook_id)
                        logger.info(f"Associated dataset {dataset_id} with notebook {notebook_id}")
                    except Exception as assoc_err:
                        logger.warning(f"Failed to associate dataset with notebook: {assoc_err}")

                redacted_columns = await RedactionService.get_redacted_columns(str(dataset.id), session)
                redacted_tables = await RedactionService.get_redacted_tables(str(dataset.id), session)

                if redacted_columns or redacted_tables:
                    rule_key = str(dataset.connection_id) if dataset.type == "connection" else str(dataset.id)
                    redaction_rules = ctx.context.setdefault("redaction_rules", {})
                    redaction_rules[rule_key] = {
                        "columns": {t: list(cols) for t, cols in redacted_columns.items()},
                        "tables": list(redacted_tables),
                    }

                schema_info = {
                    "id": str(dataset.id),
                    "name": dataset.name,
                    "type": dataset.type,
                }

                if dataset.type == "connection" and dataset.connection:
                    conn = dataset.connection
                    schema_info["connection_id"] = str(conn.id)
                    schema_info["query_connection_id"] = str(conn.id)
                    schema_info["connection_type"] = conn.type
                    schema_info["connection_name"] = conn.name

                    if conn.schema_cache:
                        try:
                            schema = json.loads(conn.schema_cache)
                            schema = _filter_redacted_from_schema(schema, redacted_tables, redacted_columns)
                            schema_info["schema"] = schema
                        except json.JSONDecodeError:
                            pass

                elif dataset.type == "file":
                    schema_info["query_connection_id"] = str(dataset.id)
                    schema_info["files"] = [
                        {"id": str(f.id), "name": f.name, "type": f.type, "size": f.size} for f in dataset.files
                    ]

                    if dataset.schema_cache:
                        try:
                            schema = json.loads(dataset.schema_cache)
                            schema = _filter_redacted_from_schema(schema, redacted_tables, redacted_columns)
                            schema_info["schema"] = schema
                        except json.JSONDecodeError:
                            pass

                return json.dumps(
                    {"success": True, "dataset": schema_info},
                    indent=2,
                    default=str,
                )
            finally:
                await session.close()

    except Exception as e:
        logger.error(f"Error in get_dataset_schema_by_id: {str(e)}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)}, indent=2, default=str)


@function_tool
async def generate_dashboard_screenshot(ctx: RunContextWrapper[Any], dashboard_id: str) -> str:
    """
    Generate a PNG screenshot of a dashboard and return it as a base64-encoded data URL.

    Use this tool when:
    - User explicitly asks for a screenshot or image of the dashboard
    - User wants to see a visual representation of the current dashboard
    - You determine a visual would be helpful for the user

    Args:
        ctx: Run context wrapper
        dashboard_id: UUID of the dashboard/notebook to screenshot

    Returns:
        JSON string with success status and base64-encoded PNG image as data URL
    """
    try:
        from server.utils.deployment import is_feature_enabled

        if not is_feature_enabled("worker_features_enabled"):
            return json.dumps(
                {
                    "success": False,
                    "error": "Dashboard screenshot is not enabled in this deployment. Contact your administrator.",
                },
                indent=2,
                default=str,
            )

        logger.info(f"Generating dashboard screenshot for {dashboard_id}")

        notebook_id_uuid = UUID(dashboard_id)

        async for session in get_async_session():
            try:
                tenant_id = ctx.context.get("tenant_id")
                if tenant_id:
                    set_tenant_id(tenant_id)

                png_bytes = await ScreenshotService.capture(
                    session=session, dashboard_id=notebook_id_uuid, version=None
                )

                base64_png = base64.b64encode(png_bytes).decode("utf-8")
                data_url = f"data:image/png;base64,{base64_png}"

                logger.info(
                    f"Screenshot generated successfully ({len(png_bytes)} bytes)",
                    extra={"posthog_context": {"dashboard_id": dashboard_id}},
                )

                return json.dumps(
                    {
                        "success": True,
                        "message": "Dashboard screenshot generated successfully",
                        "image_url": data_url,
                        "size_bytes": len(png_bytes),
                    },
                    indent=2,
                    default=str,
                )
            finally:
                await session.close()

    except ValueError as e:
        logger.error(f"Invalid dashboard_id format: {dashboard_id}: {e}")
        return json.dumps({"success": False, "error": f"Invalid dashboard ID format: {str(e)}"}, indent=2, default=str)
    except ScreenshotServiceError as e:
        logger.warning(
            f"Screenshot service error for dashboard {dashboard_id}: {e}",
            extra={"posthog_context": {"dashboard_id": dashboard_id}},
        )
        return json.dumps(
            {
                "success": False,
                "error": f"Screenshot generation failed: {str(e)}",
                "error_type": "screenshot_service_error",
            },
            indent=2,
            default=str,
        )
    except Exception as e:
        logger.error(
            f"Unexpected error generating dashboard screenshot: {str(e)}",
            exc_info=True,
            posthog_context={"function": "generate_dashboard_screenshot", "dashboard_id": dashboard_id},
        )
        return json.dumps({"success": False, "error": f"Unexpected error: {str(e)}"}, indent=2, default=str)
