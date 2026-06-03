import math
import re
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from server.services.database_operations import (
    AsyncDatabaseService,
    MongoConnector,
)
from server.services.file_operations import DataFrameFileService
from server.tools.dynamodb import is_native_write, is_partiql_write
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)


class AsyncRawQueryService:
    @staticmethod
    def _duckdb_sql_literal(value: Any) -> str:
        """Render a Python value as a DuckDB-safe SQL literal."""
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, Decimal)):
            return str(value)
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("Non-finite float values are not supported for DuckDB filters")
            return str(value)
        if isinstance(value, (datetime, date, time)):
            escaped = value.isoformat().replace("'", "''")
            return f"'{escaped}'"
        escaped = str(value).replace("'", "''")
        return f"'{escaped}'"

    @staticmethod
    def _inline_duckdb_params(query: str, params: dict[str, Any] | None) -> str:
        """Inline named :param placeholders for DuckDB-backed file queries."""
        if not params:
            return query

        inlined = query
        for name in sorted(params.keys(), key=len, reverse=True):
            literal = AsyncRawQueryService._duckdb_sql_literal(params[name])
            pattern = rf":{re.escape(name)}\b"
            inlined = re.sub(pattern, literal, inlined)
        return inlined

    @staticmethod
    async def execute_raw_query(
        query: str,
        db_type: str,
        connection_id: str,
        connection_obj: dict[str, Any] | None = None,
        limit: int = None,
        params: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """Execute raw query using async database operations."""
        if not connection_obj:
            return {"error": "Missing connection object"}

        try:
            if db_type == "mongo":
                # Parse the MongoDB query without connecting (parsing-only)
                temp_connector = MongoConnector(connection_obj)
                # Parse query using existing logic (no connection needed for parsing)
                parsed = temp_connector._parse_query(query.strip())
                if not parsed:
                    return {"error": "Failed to parse MongoDB query", "success": False}

                if parsed.get("error"):
                    return {"error": parsed["error"], "success": False}

                if parsed.get("is_write_operation"):
                    blocked_stage = parsed.get("blocked_stage")
                    if blocked_stage:
                        message = f"Write operations are not allowed (blocked stage: {blocked_stage})"
                    else:
                        message = f"Write operations are not allowed: {parsed['operation']}"
                    return {"error": message, "success": False}

                # Use async connector for execution
                async_connector = await AsyncDatabaseService.get_or_create_mongo_connector(
                    connection_id, connection_obj
                )

                collection_name = parsed["collection"]
                operation = parsed["operation"]
                args = parsed.get("args", [])
                modifiers = parsed.get("modifiers") or []

                result = await async_connector.execute_query(
                    collection_name,
                    operation,
                    args,
                    limit=limit,
                    modifiers=modifiers,
                    timeout=timeout,
                )
                return result

            elif db_type in ("duckdb", "csv", "excel", "parquet", "json", "file"):
                # Handle DuckDB-backed file dataset queries
                query_to_execute = AsyncRawQueryService._inline_duckdb_params(query, params)
                result = await DataFrameFileService.execute_duckdb_query(
                    connection_obj=connection_obj,
                    query=query_to_execute,
                    limit=limit or 500,
                    timeout=timeout,
                )
                return result

            elif db_type == "dynamodb":
                connector = await AsyncDatabaseService.get_or_create_dynamodb_connector(connection_id, connection_obj)
                query_mode = connection_obj.get("query_mode", "partiql")

                if query_mode == "partiql":
                    is_write, reason = is_partiql_write(query)
                    if is_write:
                        return {"error": reason, "success": False}
                    if params:
                        query = AsyncRawQueryService._inline_duckdb_params(query, params)
                    result = await connector.execute_partiql_query(query, limit=limit or 500, timeout=timeout)
                else:
                    import json as _json

                    try:
                        query_spec = _json.loads(query)
                    except _json.JSONDecodeError:
                        return {
                            "error": "Invalid JSON query for DynamoDB native mode. Expected JSON with 'operation' and 'table' fields.",
                            "success": False,
                        }
                    is_write, reason = is_native_write(query_spec)
                    if is_write:
                        return {"error": reason, "success": False}
                    result = await connector.execute_native_query(query_spec, limit=limit or 500, timeout=timeout)

                return result

            elif db_type == "databricks":
                from server.tools.databricks import validate_databricks_query

                try:
                    validated = validate_databricks_query(query)
                except ValueError as e:
                    return {"success": False, "error": str(e)}

                async_connector = await AsyncDatabaseService.get_or_create_databricks_connector(
                    connection_id, connection_obj
                )
                result = await async_connector.execute_query(validated, limit, timeout=timeout, params=params)
                return result

            elif db_type in ["pg", "mysql", "sqlite", "mssql"]:
                async_connector = await AsyncDatabaseService.get_or_create_sql_connector(
                    connection_id, connection_obj, db_type=db_type
                )
                result = await async_connector.execute_query(query, limit, timeout=timeout, params=params)
                return result

            else:
                return {"error": f"Unsupported db_type: {db_type}", "success": False}

        except ConnectionError as e:
            logger.error(
                f"Connection error in execute_raw_query: {str(e)}",
                exc_info=True,
                posthog_context={
                    "function": "AsyncRawQueryService.execute_raw_query",
                    "db_type": db_type,
                    "connection_id": connection_id,
                    "error_type": "connection_error",
                },
            )
            return {
                "error": str(e),
                "hint": "Check your connection settings",
                "success": False,
            }
        except Exception as e:
            logger.error(
                f"Failed to execute raw query: {str(e)}",
                exc_info=True,
                posthog_context={
                    "function": "AsyncRawQueryService.execute_raw_query",
                    "db_type": db_type,
                    "connection_id": connection_id,
                    "has_limit": limit is not None,
                },
            )
            return {
                "error": str(e),
                "hint": "Check your connection settings and query syntax",
                "success": False,
            }
