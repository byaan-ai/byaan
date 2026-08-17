"""
MCP tool registrations for FastMCP.

This file registers all Byaan tools as MCP tools with proper authentication and session handling.
"""

from uuid import UUID

from fastmcp import Context, FastMCP

from server.mcp.tool_wrappers import (
    add_learning_wrapper,
    apply_html_patch_wrapper,
    compare_evaluation_runs_wrapper,
    create_advisor_change_set_wrapper,
    create_custom_skill_wrapper,
    create_evaluation_case_draft_wrapper,
    dashboard_search_replace_wrapper,
    define_dashboard_filters_wrapper,
    describe_evaluation_failure_wrapper,
    describe_evaluation_suite_wrapper,
    describe_sharing_grant_wrapper,
    emit_plan_status_wrapper,
    ensure_notebook_exists,
    execute_duckdb_query_wrapper,
    execute_mongo_query_wrapper,
    execute_skill_api_wrapper,
    execute_sql_query_wrapper,
    get_chart_styling_wrapper,
    get_dashboard_filter_config_wrapper,
    get_database_schema_wrapper,
    get_dataset_schema_by_id_wrapper,
    get_evaluation_run_wrapper,
    get_existing_html_wrapper,
    get_filter_options_wrapper,
    get_learning_wrapper,
    get_skill_definition_wrapper,
    get_user_instructions_wrapper,
    get_user_style_guidelines_wrapper,
    list_evaluation_cases_wrapper,
    list_sharing_grants_wrapper,
    preview_evaluation_ground_truth_wrapper,
    remove_dashboard_filter_wrapper,
    remove_learning_wrapper,
    run_advisor_gate_wrapper,
    run_evaluation_wrapper,
    save_query_wrapper,
    save_skill_query_wrapper,
    saved_query_schema_wrapper,
    search_datasets_wrapper,
    search_enabled_skills_wrapper,
    search_evaluation_suites_wrapper,
    search_instructions_wrapper,
    search_learnings_wrapper,
    start_html_generation_wrapper,
    submit_evaluation_feedback_wrapper,
    update_custom_skill_wrapper,
    update_dashboard_filter_wrapper,
    update_learning_wrapper,
)
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)


async def extract_session_from_context(get_or_create_session_func, context: Context = None):
    """
    Extract session data from MCP context and ensure notebook exists.
    Auto-creates notebook on first tool call.

    Supports both HTTP mode (with Context/headers) and stdio mode (no Context).
    """
    # HTTP mode functions accept (headers, session_id) params
    # stdio mode functions accept no params
    import inspect

    sig = inspect.signature(get_or_create_session_func)
    is_stdio_mode = len(sig.parameters) == 0

    if is_stdio_mode:
        session_data = await get_or_create_session_func()
        return session_data
    else:
        headers = {}
        if context and hasattr(context, "request_context"):
            req_ctx = context.request_context
            if hasattr(req_ctx, "request") and hasattr(req_ctx.request, "headers"):
                headers = dict(req_ctx.request.headers)
            elif hasattr(req_ctx, "headers"):
                headers = dict(req_ctx.headers)

        mcp_session_id = headers.get("mcp-session-id") or headers.get("Mcp-Session-Id")
        session_data = await get_or_create_session_func(headers, session_id=mcp_session_id)

        tenant_id = UUID(str(session_data["tenant_id"]))
        user_id = UUID(str(session_data["user_id"]))
        notebook_id = UUID(str(session_data["notebook_id"])) if session_data.get("notebook_id") else None
        session_id = session_data["session_id"]

        notebook_id = await ensure_notebook_exists(tenant_id, user_id, notebook_id, session_id)

        return {
            "session_id": session_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "notebook_id": notebook_id,
        }


def register_all_tools(mcp: FastMCP, get_or_create_session_func):
    """Register all Byaan tools with the MCP server."""

    # Data Discovery Tools
    @mcp.tool()
    async def search_datasets(query: str, context: Context = None) -> str:
        """
        Search for databases and datasets by name or type.

        Use this to find available data sources before querying them.

        Args:
            query: Search term (e.g., "sales", "customers", "postgres")
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await search_datasets_wrapper(query, session["tenant_id"], session["user_id"], session["notebook_id"])

    @mcp.tool()
    async def get_database_schema(context: Context = None) -> str:
        """
        Get the full schema of the currently selected database.

        Returns tables, columns, data types, and relationships.
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await get_database_schema_wrapper(session["tenant_id"], session["user_id"], session["notebook_id"])

    @mcp.tool()
    async def get_dataset_schema_by_id(dataset_id: str, context: Context = None) -> str:
        """
        Get schema for a specific dataset by its ID.

        Returns a flattened schema optimized for MCP performance. Field information
        is presented as "field_name:type" format to reduce token usage by 80-90%.

        Example formats by database type:

        MongoDB:
          {"customers": ["_id:objectId", "name:string", "email:string", "createdAt:date"]}

        SQL databases:
          {"users": ["id:integer", "username:varchar", "created_at:timestamp"]}

        File type datasets (DuckDB):
          {"sales_data": ["date:date", "product:string", "revenue:double"]}

        Args:
            dataset_id: UUID of the dataset
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await get_dataset_schema_by_id_wrapper(
            dataset_id, session["tenant_id"], session["user_id"], session["notebook_id"], session["session_id"]
        )

    # Query Execution Tools
    @mcp.tool()
    async def execute_sql_query(
        connection_id: str, query: str, limit: int = 5, timeout: int = 30, context: Context = None
    ) -> str:
        """
        Execute a SQL query on PostgreSQL, MySQL, or SQLite database.

        Args:
            connection_id: Database connection UUID
            query: SQL SELECT query (read-only)
            limit: Maximum rows to return (default 5, max 50)
            timeout: Query timeout in seconds (default 30)
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await execute_sql_query_wrapper(
            connection_id, query, limit, timeout, session["tenant_id"], session["user_id"], session["notebook_id"]
        )

    @mcp.tool()
    async def execute_mongo_query(
        connection_id: str, query: str, limit: int = 5, timeout: int = 30, context: Context = None
    ) -> str:
        """
        Execute a MongoDB query using standard MongoDB shell syntax.

        IMPORTANT: Use MongoDB shell syntax with the collection name embedded in the query.

        Args:
            connection_id: MongoDB connection UUID
            query: MongoDB query in shell syntax. Examples:
                   - db.inventory.find({})
                   - db.users.findOne({_id: ObjectId("507f1f77bcf86cd799439011")})
                   - db.products.find({category: "electronics"})
                   - db.orders.count({status: "completed"})
                   - db.faqs.aggregate([{$match: {category: "Account"}}])
                   Note: Always use ObjectId("...") for _id fields and reference fields.
                   Use new Date("2025-01-01T00:00:00.000Z") for date queries.
            limit: Maximum documents to return (default 5, max 50)
            timeout: Query timeout in seconds (default 30)
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await execute_mongo_query_wrapper(
            connection_id,
            query,
            limit,
            timeout,
            session["tenant_id"],
            session["user_id"],
            session["notebook_id"],
        )

    @mcp.tool()
    async def execute_duckdb_query(
        dataset_id: str, query: str, limit: int = 5, timeout: int = 30, context: Context = None
    ) -> str:
        """
        Execute a SQL query on file-based datasets (CSV, Excel, Parquet).

        Args:
            dataset_id: Dataset UUID
            query: SQL SELECT query
            limit: Maximum rows to return (default 5, max 50)
            timeout: Query timeout in seconds (default 30)
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await execute_duckdb_query_wrapper(
            dataset_id, query, limit, timeout, session["tenant_id"], session["user_id"], session["notebook_id"]
        )

    # Dashboard Creation Tools
    @mcp.tool()
    async def start_html_generation(context: Context = None) -> str:
        """
        Start generating a new dashboard HTML.

        Call this before creating visualizations.
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        if not session["notebook_id"]:
            return '{"success": false, "error": "No notebook context available"}'
        return await start_html_generation_wrapper(session["tenant_id"], session["user_id"], session["notebook_id"])

    @mcp.tool()
    async def get_existing_html(context: Context = None) -> str:
        """
        Get the current dashboard HTML content.

        Use this to see what's already in the dashboard before editing.
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        if not session["notebook_id"]:
            return '{"success": false, "error": "No notebook context available"}'
        return await get_existing_html_wrapper(session["tenant_id"], session["user_id"], session["notebook_id"])

    @mcp.tool()
    async def apply_html_patch(patch_text: str, context: Context = None) -> str:
        """
        Apply a unified diff patch to modify the dashboard HTML.

        Args:
            patch_text: Unified diff format patch
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        if not session["notebook_id"]:
            return '{"success": false, "error": "No notebook context available"}'
        return await apply_html_patch_wrapper(
            patch_text, session["tenant_id"], session["user_id"], session["notebook_id"]
        )

    @mcp.tool()
    async def dashboard_search_replace(diff_content: str, context: Context = None) -> str:
        """
        Search and replace content in the dashboard.

        Args:
            diff_content: Search/replace instructions in diff format
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        if not session["notebook_id"]:
            return '{"success": false, "error": "No notebook context available"}'
        return await dashboard_search_replace_wrapper(
            diff_content, session["tenant_id"], session["user_id"], session["notebook_id"]
        )

    # Configuration Tools
    @mcp.tool()
    async def get_chart_styling(chart_types: list[str] | None = None, context: Context = None) -> str:
        """
        Get chart styling guidelines and best practices.

        Args:
            chart_types: Optional list of chart types (e.g., ["bar", "line"])
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await get_chart_styling_wrapper(
            chart_types, session["tenant_id"], session["user_id"], session["notebook_id"]
        )

    @mcp.tool()
    async def get_user_instructions(context: Context = None) -> str:
        """Get user's custom instructions and preferences."""
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await get_user_instructions_wrapper(session["tenant_id"], session["user_id"], session["notebook_id"])

    @mcp.tool()
    async def get_user_style_guidelines(context: Context = None) -> str:
        """Get user's style guidelines for dashboards."""
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await get_user_style_guidelines_wrapper(session["tenant_id"], session["user_id"], session["notebook_id"])

    # Query Management Tools
    @mcp.tool()
    async def saved_query_schema(context: Context = None) -> str:
        """Get schema and list of saved queries."""
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await saved_query_schema_wrapper(session["tenant_id"], session["user_id"], session["notebook_id"])

    @mcp.tool()
    async def save_query(
        query: str, name: str, connection_id: str, is_dashboard: bool = False, context: Context = None
    ) -> str:
        """
        Save a query for reuse.

        Args:
            query: SQL or MongoDB query text
            name: Name for the saved query
            connection_id: Connection UUID
            is_dashboard: Whether this is a dashboard query
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await save_query_wrapper(
            query, name, connection_id, is_dashboard, session["tenant_id"], session["user_id"], session["notebook_id"]
        )

    # Filter Tools
    @mcp.tool()
    async def get_filter_options(context: Context = None) -> str:
        """Get available filter options for the current dashboard."""
        session = await extract_session_from_context(get_or_create_session_func, context)
        if not session["notebook_id"]:
            return '{"success": false, "error": "No notebook context available"}'
        return await get_filter_options_wrapper(session["tenant_id"], session["user_id"], session["notebook_id"])

    @mcp.tool()
    async def define_dashboard_filters(filters_config: str, context: Context = None) -> str:
        """
        Define filters for the dashboard.

        Args:
            filters_config: JSON string with filter configuration
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        if not session["notebook_id"]:
            return '{"success": false, "error": "No notebook context available"}'
        return await define_dashboard_filters_wrapper(
            filters_config, session["tenant_id"], session["user_id"], session["notebook_id"]
        )

    @mcp.tool()
    async def update_dashboard_filter(filter_id: str, updates: str, context: Context = None) -> str:
        """
        Update an existing dashboard filter.

        Args:
            filter_id: Filter ID
            updates: JSON string with filter updates
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        if not session["notebook_id"]:
            return '{"success": false, "error": "No notebook context available"}'
        return await update_dashboard_filter_wrapper(
            filter_id, updates, session["tenant_id"], session["user_id"], session["notebook_id"]
        )

    @mcp.tool()
    async def remove_dashboard_filter(filter_id: str, context: Context = None) -> str:
        """
        Remove a dashboard filter.

        Args:
            filter_id: Filter ID to remove
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        if not session["notebook_id"]:
            return '{"success": false, "error": "No notebook context available"}'
        return await remove_dashboard_filter_wrapper(
            filter_id, session["tenant_id"], session["user_id"], session["notebook_id"]
        )

    @mcp.tool()
    async def get_dashboard_filter_config(context: Context = None) -> str:
        """Get current dashboard filter configuration."""
        session = await extract_session_from_context(get_or_create_session_func, context)
        if not session["notebook_id"]:
            return '{"success": false, "error": "No notebook context available"}'
        return await get_dashboard_filter_config_wrapper(
            session["tenant_id"], session["user_id"], session["notebook_id"]
        )

    # Instruction Tools
    @mcp.tool()
    async def search_instructions(query: str, context: Context = None) -> str:
        """
        Search workspace instructions for specific keywords.

        Args:
            query: Keywords to search for in saved instructions
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await search_instructions_wrapper(
            query, session["tenant_id"], session["user_id"], session["notebook_id"]
        )

    # Learning Tools
    @mcp.tool()
    async def add_learning(title: str, learning: str, dataset_id: str = "", context: Context = None) -> str:
        """
        Save a NEW learning. MUST call search_learnings first — if a learning for this
        datasource/table/collection already exists, use update_learning instead to compound
        new insights. Do NOT create duplicates — one learning per datasource, not per query.

        Args:
            title: Datasource-level title referencing the table/collection name (max 500 chars)
            learning: Datasource structure, gotchas, query patterns, error fixes
            dataset_id: Optional UUID of the dataset (from get_database_schema or get_dataset_schema_by_id)
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await add_learning_wrapper(
            title, learning, session["tenant_id"], session["user_id"], session["notebook_id"], dataset_id
        )

    @mcp.tool()
    async def update_learning(
        learning_id: str, learning: str, title: str = "", dataset_id: str = "", context: Context = None
    ) -> str:
        """
        Update an existing learning by ID. Use to compound new insights or refine the title.

        Args:
            learning_id: UUID of the learning to update
            learning: The updated content (replaces existing content entirely)
            title: New title (optional — only if refining the category key)
            dataset_id: Optional UUID of the dataset to link (use when learning wasn't linked before)
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await update_learning_wrapper(
            learning_id, learning, title, session["tenant_id"], session["user_id"], session["notebook_id"], dataset_id
        )

    @mcp.tool()
    async def search_learnings(query: str, dataset_id: str = "", context: Context = None) -> str:
        """
        Search the workspace knowledge base for previously discovered insights.
        Call this EARLY — before writing queries, exploring repos, or using skills — to check if
        a past conversation already figured out where data lives, what went wrong, or how something works.
        Use broad keywords from the user's question. If first search misses, try synonyms or related terms.

        Args:
            query: Keywords to search (e.g. "oil prices", "customer churn", "auth flow")
            dataset_id: Optional UUID of a dataset to find learnings linked to it
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await search_learnings_wrapper(
            query, session["tenant_id"], session["user_id"], session["notebook_id"], dataset_id
        )

    @mcp.tool()
    async def get_learning(learning_id: str, context: Context = None) -> str:
        """
        Fetch full content of a learning by its ID.

        Args:
            learning_id: UUID of the learning (from search_learnings results)
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await get_learning_wrapper(learning_id, session["tenant_id"], session["user_id"], session["notebook_id"])

    @mcp.tool()
    async def remove_learning(learning_id: str, context: Context = None) -> str:
        """
        Remove a learning from the workspace knowledge base by its ID.

        Args:
            learning_id: The UUID of the learning to remove
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await remove_learning_wrapper(
            learning_id, session["tenant_id"], session["user_id"], session["notebook_id"]
        )

    # Sharing Read Tools
    @mcp.tool()
    async def list_sharing_grants(
        object_type: str | None = None,
        object_id: str | None = None,
        legacy_surface: str | None = None,
        status: str | None = None,
        limit: int = 50,
        context: Context = None,
    ) -> str:
        """
        List canonical sharing grants visible in the current workspace.

        Values are redacted before they are returned. Use legacy_surface to inspect
        compatibility-backed shares such as folder_notebook, folder_dashboard,
        html_notebook_share, or json_notebook_share.
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await list_sharing_grants_wrapper(
            session["tenant_id"],
            session["user_id"],
            object_type=object_type,
            object_id=object_id,
            legacy_surface=legacy_surface,
            status=status,
            limit=limit,
        )

    @mcp.tool()
    async def describe_sharing_grant(grant_id: str, context: Context = None) -> str:
        """
        Describe one canonical sharing grant, including redacted compatibility,
        secret-count, viewer-session, and audit evidence.
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await describe_sharing_grant_wrapper(grant_id, session["tenant_id"], session["user_id"])

    # Evaluation Governance Tools
    @mcp.tool()
    async def search_evaluation_suites(
        query: str = "",
        target_kind: str = "",
        status: str = "",
        limit: int = 20,
        context: Context = None,
    ) -> str:
        """
        Search Evaluation suites by text, target kind, or lifecycle status.
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await search_evaluation_suites_wrapper(
            query,
            target_kind,
            status,
            session["tenant_id"],
            session["user_id"],
            limit,
        )

    @mcp.tool()
    async def describe_evaluation_suite(
        suite_id: str,
        include_manifests: bool = False,
        context: Context = None,
    ) -> str:
        """
        Describe an Evaluation suite, versions, gate policy, and optional manifests.
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await describe_evaluation_suite_wrapper(
            suite_id,
            session["tenant_id"],
            session["user_id"],
            include_manifests,
        )

    @mcp.tool()
    async def list_evaluation_cases(
        suite_version_id: str,
        include_expected_contract: bool = False,
        limit: int = 20,
        context: Context = None,
    ) -> str:
        """
        List Evaluation cases for a suite version. Expected contracts are omitted unless requested.
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await list_evaluation_cases_wrapper(
            suite_version_id,
            session["tenant_id"],
            session["user_id"],
            include_expected_contract,
            limit,
        )

    @mcp.tool()
    async def create_evaluation_case_draft(
        suite_version_id: str,
        case_json: str,
        context: Context = None,
    ) -> str:
        """
        Create an Evaluation case draft from a JSON case payload. Published suite versions remain immutable.
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await create_evaluation_case_draft_wrapper(
            suite_version_id,
            case_json,
            session["tenant_id"],
            session["user_id"],
        )

    @mcp.tool()
    async def preview_evaluation_ground_truth(expected_contract_json: str, context: Context = None) -> str:
        """
        Validate ground-truth SQL as read-only and return redacted preview metadata.
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await preview_evaluation_ground_truth_wrapper(
            expected_contract_json,
            session["tenant_id"],
            session["user_id"],
        )

    @mcp.tool()
    async def run_evaluation(
        suite_version_id: str,
        target_snapshot_json: str,
        idempotency_key: str = "",
        context: Context = None,
    ) -> str:
        """
        Create a DB-backed Evaluation run preflight for a pinned target snapshot.
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await run_evaluation_wrapper(
            suite_version_id,
            target_snapshot_json,
            idempotency_key,
            session["tenant_id"],
            session["user_id"],
        )

    @mcp.tool()
    async def get_evaluation_run(
        run_id: str,
        include_case_results: bool = True,
        limit: int = 20,
        context: Context = None,
    ) -> str:
        """
        Get an Evaluation run report, including redacted case results and assessments.
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await get_evaluation_run_wrapper(
            run_id,
            session["tenant_id"],
            session["user_id"],
            include_case_results,
            limit,
        )

    @mcp.tool()
    async def compare_evaluation_runs(
        baseline_run_id: str,
        candidate_run_id: str,
        context: Context = None,
    ) -> str:
        """
        Compare baseline and candidate Evaluation runs and surface regressions first.
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await compare_evaluation_runs_wrapper(
            baseline_run_id,
            candidate_run_id,
            session["tenant_id"],
            session["user_id"],
        )

    @mcp.tool()
    async def describe_evaluation_failure(run_id: str, limit: int = 20, context: Context = None) -> str:
        """
        Return failed case runs and hard-fail assessments for an Evaluation run.
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await describe_evaluation_failure_wrapper(
            run_id,
            session["tenant_id"],
            session["user_id"],
            limit,
        )

    @mcp.tool()
    async def create_advisor_change_set(
        skill_suggestion_id: str,
        suite_version_id: str = "",
        affected_case_ids: list[str] | None = None,
        context: Context = None,
    ) -> str:
        """
        Convert a skill suggestion into a draft Evaluation advisor change set.
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await create_advisor_change_set_wrapper(
            skill_suggestion_id,
            suite_version_id,
            affected_case_ids,
            session["tenant_id"],
            session["user_id"],
        )

    @mcp.tool()
    async def run_advisor_gate(
        change_set_id: str,
        target_snapshot_json: str,
        gate_kind: str,
        idempotency_key: str = "",
        context: Context = None,
    ) -> str:
        """
        Queue an advisor verification or regression Evaluation run.
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await run_advisor_gate_wrapper(
            change_set_id,
            target_snapshot_json,
            gate_kind,
            idempotency_key,
            session["tenant_id"],
            session["user_id"],
        )

    @mcp.tool()
    async def submit_evaluation_feedback(
        suite_version_id: str,
        feedback_json: str,
        context: Context = None,
    ) -> str:
        """
        Submit redacted feedback into Evaluation as a reviewed draft case.
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await submit_evaluation_feedback_wrapper(
            suite_version_id,
            feedback_json,
            session["tenant_id"],
            session["user_id"],
        )

    # Plan Tools
    @mcp.tool()
    async def emit_plan_status(action: str, steps_json: str = "", step_number: int = 0, context: Context = None) -> str:
        """
        Emit plan status for complex multi-step tasks.

        Args:
            action: One of "start_plan", "start_step", "complete_step", "fail_step", "complete_plan"
            steps_json: JSON array of plan steps (required for start_plan)
            step_number: Current step number (required for step actions)
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await emit_plan_status_wrapper(
            action, steps_json, step_number, session["tenant_id"], session["user_id"], session["notebook_id"]
        )

    # Skill Tools
    @mcp.tool()
    async def search_enabled_skills(query: str, context: Context = None) -> str:
        """
        Search for enabled external skills (Linear, Notion, etc.) by keyword.

        Use this when the user mentions external services or asks about tickets,
        issues, pages, tasks, or projects.

        Args:
            query: Search keyword (e.g., "tickets", "issues", "notion")
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await search_enabled_skills_wrapper(
            query, session["tenant_id"], session["user_id"], session["notebook_id"]
        )

    @mcp.tool()
    async def get_skill_definition(skill_name: str, context: Context = None) -> str:
        """
        Get full documentation for a specific skill.

        Call this after search_enabled_skills to load complete documentation.

        Args:
            skill_name: Exact skill name (e.g., "linear", "notion")
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await get_skill_definition_wrapper(
            skill_name, session["tenant_id"], session["user_id"], session["notebook_id"]
        )

    @mcp.tool()
    async def execute_skill_api(
        skill_name: str,
        endpoint_path: str,
        method: str = "GET",
        body: str = "",
        headers: str = "",
        is_graphql: bool = False,
        graphql_query: str = "",
        graphql_variables: str = "",
        scope: str = "",
        context: Context = None,
    ) -> str:
        """
        Execute an API request for an enabled skill.

        Use endpoint_path with just the path (e.g., "/graphql"), not the full URL.

        Args:
            skill_name: Name of the skill (e.g., "notion", "linear")
            endpoint_path: API endpoint path
            method: HTTP method (GET, POST, PATCH, DELETE)
            body: JSON string for request body
            headers: Optional JSON string of additional headers
            is_graphql: Set to True for GraphQL requests
            graphql_query: GraphQL query string
            graphql_variables: JSON string of GraphQL variables
            scope: Credential scope ("user" or "org")
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await execute_skill_api_wrapper(
            skill_name,
            endpoint_path,
            method,
            body,
            headers,
            is_graphql,
            graphql_query,
            graphql_variables,
            scope,
            session["tenant_id"],
            session["user_id"],
            session["notebook_id"],
        )

    @mcp.tool()
    async def save_skill_query(
        skill_name: str,
        name: str,
        endpoint_path: str,
        method: str = "GET",
        body: str = "",
        is_graphql: bool = False,
        graphql_query: str = "",
        graphql_variables: str = "",
        scope: str = "",
        context: Context = None,
    ) -> str:
        """
        Save a skill API query for dashboard use.

        Executes the API call and saves it as a reusable query.

        Args:
            skill_name: Name of the skill
            name: Human-readable name for the saved query
            endpoint_path: API endpoint path
            method: HTTP method
            body: JSON string for request body
            is_graphql: Set to True for GraphQL requests
            graphql_query: GraphQL query string
            graphql_variables: JSON string of variables
            scope: Credential scope
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await save_skill_query_wrapper(
            skill_name,
            name,
            endpoint_path,
            method,
            body,
            is_graphql,
            graphql_query,
            graphql_variables,
            scope,
            session["tenant_id"],
            session["user_id"],
            session["notebook_id"],
        )

    @mcp.tool()
    async def update_custom_skill(
        skill_name: str, instructions: str | None = None, description: str | None = None, context: Context = None
    ) -> str:
        """
        Update a custom skill's instructions or description.

        Args:
            skill_name: Name of the custom skill
            instructions: New instructions (optional)
            description: New description (optional)
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await update_custom_skill_wrapper(
            skill_name, instructions, description, session["tenant_id"], session["user_id"], session["notebook_id"]
        )

    @mcp.tool()
    async def create_custom_skill(name: str, description: str, instructions: str, context: Context = None) -> str:
        """
        Create a new custom skill for reuse across conversations.

        Args:
            name: Short name for the skill (e.g., "weekly-status-report")
            description: Brief summary of what the skill does
            instructions: Full instructions for the skill
        """
        session = await extract_session_from_context(get_or_create_session_func, context)
        return await create_custom_skill_wrapper(
            name, description, instructions, session["tenant_id"], session["user_id"], session["notebook_id"]
        )
