"""Databricks tool for agents - read-only SQL queries against SQL warehouses."""

import json
import re
from typing import Any

import sqlglot
from agents import function_tool
from agents.run_context import RunContextWrapper
from sqlglot import exp

from server.auth.tenant_context import set_tenant_id
from server.db.session import get_async_session
from server.repositories.connections import ConnectionRepository
from server.services.database_operations import AsyncDatabaseService
from server.tools.plan_tools import check_plan_gate
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

DISALLOWED_NODES = (
    exp.Delete,
    exp.Insert,
    exp.Update,
    exp.Create,
    exp.Alter,
    exp.Drop,
    exp.Merge,
)

ALLOWED_ROOT_NODES = (
    exp.Select,
    exp.Subquery,
    exp.Union,
    exp.Describe,
    exp.Show,
    exp.Pragma,
)

ALLOWED_COMMAND_KEYWORDS = {"EXPLAIN", "DESC", "DESCRIBE", "SHOW"}

DISALLOWED_COMMAND_PATTERN = re.compile(
    r"\b(TRUNCATE|GRANT|REVOKE|COPY\s+INTO|REFRESH|VACUUM|OPTIMIZE|RESTORE|REPLACE|SET|USE|ANALYZE|CACHE|UNCACHE|MSCK)\b",
    re.IGNORECASE,
)


def validate_databricks_query(query: str) -> str:
    processed = query.replace("\\n", "\n").replace("\\t", "\t").strip()
    if processed.endswith(";"):
        processed = processed[:-1].strip()

    try:
        expressions = sqlglot.parse(processed, dialect="databricks")
    except Exception as e:
        raise ValueError(f"❌ SQL parsing failed: {str(e)}")

    expressions = [e for e in expressions if e is not None]
    if len(expressions) == 0:
        raise ValueError("❌ Empty query.")
    if len(expressions) > 1:
        raise ValueError("🚨 Multiple statements are not allowed. Submit a single SELECT query.")

    tree = expressions[0]

    if isinstance(tree, exp.Command):
        kind = (tree.name or "").upper()
        if kind not in ALLOWED_COMMAND_KEYWORDS:
            raise ValueError(
                f"🚨 Unsafe query detected: {kind or 'UNKNOWN'} is not allowed. "
                f"Only read-only queries (SELECT/WITH/DESCRIBE/SHOW/EXPLAIN) are permitted."
            )
    elif not isinstance(tree, ALLOWED_ROOT_NODES):
        raise ValueError(
            f"🚨 Unsafe query detected: {type(tree).__name__.upper()} is not allowed. "
            f"Only read-only queries (SELECT/WITH/DESCRIBE/SHOW/EXPLAIN) are permitted."
        )

    for disallowed in DISALLOWED_NODES:
        if tree.find(disallowed):
            raise ValueError(
                f"🚨 Unsafe query detected: {disallowed.__name__.upper()} is not allowed. "
                f"Only read-only queries (SELECT) are permitted."
            )

    for cmd in tree.find_all(exp.Command):
        kind = (cmd.name or "").upper()
        if kind not in ALLOWED_COMMAND_KEYWORDS:
            raise ValueError(f"🚨 Unsafe query detected: {kind or 'UNKNOWN'} command is not allowed.")
        if cmd.expression and DISALLOWED_COMMAND_PATTERN.search(str(cmd.expression)):
            raise ValueError("🚨 Unsafe query detected: write-style keyword present in command body.")

    return processed


@function_tool
async def execute_databricks_query(
    ctx: RunContextWrapper[Any], connection_id: str, query: str, limit: int = 5, timeout: int = 120
) -> str:
    """
    Execute a Databricks SQL query (read-only) against a SQL warehouse.

    IMPORTANT: This is a READ-ONLY tool. Write operations (INSERT, UPDATE, DELETE, DROP, MERGE,
    CREATE, ALTER, TRUNCATE, GRANT, REVOKE, COPY INTO, REFRESH, VACUUM, OPTIMIZE) are strictly prohibited.

    Args:
        ctx: Run context wrapper
        connection_id: Databricks connection ID
        query: SELECT statement. Use fully-qualified names: catalog.schema.table
               Examples:
               - SELECT * FROM samples.tpch.customer LIMIT 10
               - SELECT n_name, COUNT(*) FROM samples.tpch.nation GROUP BY n_name
        limit: Max rows to return (max 50, default 5)
        timeout: Query timeout in seconds (default 120 — warehouses may cold-start + Databricks has many tables)

    Returns:
        JSON string with: success, result, execution_time_seconds, connection_id, db_type, error.
    """
    if gate_error := check_plan_gate(ctx):
        return gate_error

    if limit > 50:
        limit = 50

    logger.info(f"Executing Databricks query connection_id={connection_id}: {query[:100]}...")

    async for session in get_async_session():
        try:
            tenant_id = ctx.context.get("tenant_id")
            set_tenant_id(tenant_id)
            conn_repo = ConnectionRepository(session)
            connection = await conn_repo.get(connection_id)

            if not connection:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"Connection '{connection_id}' not found",
                        "connection_id": connection_id,
                    },
                    indent=2,
                )

            if connection.type.lower() != "databricks":
                return json.dumps(
                    {
                        "success": False,
                        "error": f"Connection '{connection_id}' is not Databricks (type: {connection.type})",
                        "connection_id": connection_id,
                    },
                    indent=2,
                )

            connection_obj = await connection.get_decrypted_connection_obj(session)
            if not connection_obj:
                return json.dumps(
                    {
                        "success": False,
                        "error": "Failed to decrypt connection credentials",
                        "connection_id": connection_id,
                    },
                    indent=2,
                )
        finally:
            await session.close()
        break

    try:
        validated = validate_databricks_query(query)
    except ValueError as e:
        return json.dumps(
            {"success": False, "error": str(e), "connection_id": connection_id, "db_type": "databricks"},
            indent=2,
        )

    redaction_rules = ctx.context.get("redaction_rules", {})
    conn_rules = redaction_rules.get(connection_id, {})
    queried_tables: set[str] = set()
    if conn_rules:
        try:
            for tree in sqlglot.parse(validated, dialect="databricks"):
                for tbl in tree.find_all(exp.Table):
                    queried_tables.add(tbl.name)
        except Exception as e:
            logger.warning(
                "Failed to extract queried tables for redaction check; continuing without table-level filtering: %s",
                e,
                exc_info=True,
            )

        redacted_tables = set(conn_rules.get("tables", []))
        for tname in queried_tables:
            if tname in redacted_tables:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"Access denied: table '{tname}' is restricted",
                        "connection_id": connection_id,
                        "db_type": "databricks",
                    },
                    indent=2,
                )

    try:
        connector = await AsyncDatabaseService.get_or_create_databricks_connector(connection_id, connection_obj)
        result = await connector.execute_query(validated, limit=limit, timeout=timeout)
        result["connection_id"] = connection_id
        result["db_type"] = "databricks"
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        logger.error(f"Databricks query failed: {e}", exc_info=True)
        return json.dumps(
            {
                "success": False,
                "error": str(e),
                "connection_id": connection_id,
                "db_type": "databricks",
                "query": query[:100] + "..." if len(query) > 100 else query,
            },
            indent=2,
        )


def get_databricks_tools():
    return [execute_databricks_query]
