"""
Tests for AsyncRawQueryService — DuckDB literal rendering, parameter inlining, routing.
"""

from datetime import date, datetime, time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.services.database_operations import AsyncDatabaseService
from server.services.file_operations import DataFrameFileService
from server.services.raw_query import AsyncRawQueryService

# ---------------------------------------------------------------------------
# _duckdb_sql_literal
# ---------------------------------------------------------------------------


class TestDuckdbSqlLiteral:
    def test_none(self):
        assert AsyncRawQueryService._duckdb_sql_literal(None) == "NULL"

    def test_bool_true(self):
        assert AsyncRawQueryService._duckdb_sql_literal(True) == "TRUE"

    def test_bool_false(self):
        assert AsyncRawQueryService._duckdb_sql_literal(False) == "FALSE"

    def test_integer(self):
        assert AsyncRawQueryService._duckdb_sql_literal(42) == "42"

    def test_decimal(self):
        assert AsyncRawQueryService._duckdb_sql_literal(Decimal("3.14")) == "3.14"

    def test_float(self):
        assert AsyncRawQueryService._duckdb_sql_literal(3.14) == "3.14"

    def test_float_nan_raises(self):
        with pytest.raises(ValueError, match="Non-finite"):
            AsyncRawQueryService._duckdb_sql_literal(float("nan"))

    def test_float_inf_raises(self):
        with pytest.raises(ValueError, match="Non-finite"):
            AsyncRawQueryService._duckdb_sql_literal(float("inf"))

    def test_datetime(self):
        dt = datetime(2025, 1, 15, 10, 30, 0)
        result = AsyncRawQueryService._duckdb_sql_literal(dt)
        assert result == "'2025-01-15T10:30:00'"

    def test_date(self):
        d = date(2025, 1, 15)
        result = AsyncRawQueryService._duckdb_sql_literal(d)
        assert result == "'2025-01-15'"

    def test_time(self):
        t = time(10, 30, 0)
        result = AsyncRawQueryService._duckdb_sql_literal(t)
        assert result == "'10:30:00'"

    def test_string(self):
        assert AsyncRawQueryService._duckdb_sql_literal("hello") == "'hello'"

    def test_string_with_single_quote_escaped(self):
        result = AsyncRawQueryService._duckdb_sql_literal("it's")
        assert result == "'it''s'"

    def test_string_sql_injection_escaped(self):
        result = AsyncRawQueryService._duckdb_sql_literal("'; DROP TABLE users; --")
        assert "''" in result
        assert result.startswith("'")
        assert result.endswith("'")


# ---------------------------------------------------------------------------
# _inline_duckdb_params
# ---------------------------------------------------------------------------


class TestInlineDuckdbParams:
    def test_no_params_returns_query(self):
        query = "SELECT * FROM data"
        result = AsyncRawQueryService._inline_duckdb_params(query, None)
        assert result == query

    def test_empty_params_returns_query(self):
        query = "SELECT * FROM data"
        result = AsyncRawQueryService._inline_duckdb_params(query, {})
        assert result == query

    def test_single_param(self):
        query = "SELECT * FROM data WHERE name = :name"
        result = AsyncRawQueryService._inline_duckdb_params(query, {"name": "Alice"})
        assert result == "SELECT * FROM data WHERE name = 'Alice'"

    def test_multiple_params(self):
        query = "SELECT * FROM data WHERE name = :name AND age > :age"
        result = AsyncRawQueryService._inline_duckdb_params(query, {"name": "Alice", "age": 25})
        assert "'Alice'" in result
        assert "25" in result
        assert ":name" not in result
        assert ":age" not in result

    def test_null_param(self):
        query = "SELECT * FROM data WHERE name = :name"
        result = AsyncRawQueryService._inline_duckdb_params(query, {"name": None})
        assert "NULL" in result

    def test_bool_param(self):
        query = "SELECT * FROM data WHERE active = :active"
        result = AsyncRawQueryService._inline_duckdb_params(query, {"active": True})
        assert "TRUE" in result

    def test_longer_param_name_replaced_first(self):
        # Params sorted by length descending to avoid partial replacement
        query = "SELECT * FROM data WHERE val = :val AND value = :value"
        result = AsyncRawQueryService._inline_duckdb_params(query, {"val": 1, "value": 2})
        assert "value = 2" in result
        assert ":val" not in result
        assert ":value" not in result

    def test_param_with_special_chars_in_value(self):
        query = "SELECT * FROM data WHERE name = :name"
        result = AsyncRawQueryService._inline_duckdb_params(query, {"name": "O'Brien"})
        assert "O''Brien" in result


# ---------------------------------------------------------------------------
# _inline_duckdb_params — values that look like regex replacement templates
# ---------------------------------------------------------------------------


class TestInlineDuckdbParamsBackslashValues:
    def test_windows_path_value_inlined_verbatim(self):
        query = "SELECT * FROM data WHERE source = :source"
        result = AsyncRawQueryService._inline_duckdb_params(query, {"source": "C:\\Users\\analyst\\q1.csv"})
        assert result == "SELECT * FROM data WHERE source = 'C:\\Users\\analyst\\q1.csv'"

    def test_backreference_looking_text_not_expanded(self):
        query = "SELECT * FROM data WHERE pattern = :pattern"
        result = AsyncRawQueryService._inline_duckdb_params(query, {"pattern": "\\1 then \\g<name>"})
        assert result == "SELECT * FROM data WHERE pattern = '\\1 then \\g<name>'"

    def test_escape_spellings_not_unescaped(self):
        query = "SELECT * FROM data WHERE token = :token"
        result = AsyncRawQueryService._inline_duckdb_params(query, {"token": "a\\nb\\tc\\rd"})
        assert result == "SELECT * FROM data WHERE token = 'a\\nb\\tc\\rd'"
        assert "\n" not in result
        assert "\t" not in result

    def test_doubled_backslash_preserved(self):
        query = "SELECT * FROM data WHERE share = :share"
        result = AsyncRawQueryService._inline_duckdb_params(query, {"share": "\\\\nas01\\reports"})
        assert result == "SELECT * FROM data WHERE share = '\\\\nas01\\reports'"

    def test_trailing_backslash_value(self):
        query = "SELECT * FROM data WHERE folder = :folder"
        result = AsyncRawQueryService._inline_duckdb_params(query, {"folder": "D:\\exports\\"})
        assert result == "SELECT * FROM data WHERE folder = 'D:\\exports\\'"

    def test_quote_and_backslash_combination(self):
        query = "SELECT * FROM data WHERE label = :label"
        result = AsyncRawQueryService._inline_duckdb_params(query, {"label": "O'Brien\\2024"})
        assert result == "SELECT * FROM data WHERE label = 'O''Brien\\2024'"

    def test_repeated_placeholder_replaced_everywhere(self):
        query = "SELECT * FROM data WHERE src = :path OR dst = :path"
        result = AsyncRawQueryService._inline_duckdb_params(query, {"path": "C:\\tmp\\a.csv"})
        assert result == "SELECT * FROM data WHERE src = 'C:\\tmp\\a.csv' OR dst = 'C:\\tmp\\a.csv'"

    def test_prefix_param_names_with_backslash_values(self):
        query = "SELECT * FROM data WHERE dir = :dir AND full = :dir_path"
        result = AsyncRawQueryService._inline_duckdb_params(query, {"dir": "C:\\data", "dir_path": "C:\\data\\q1.csv"})
        assert result == "SELECT * FROM data WHERE dir = 'C:\\data' AND full = 'C:\\data\\q1.csv'"

    def test_datetime_and_backslash_params_together(self):
        query = "SELECT * FROM data WHERE at > :since AND source = :source"
        result = AsyncRawQueryService._inline_duckdb_params(
            query, {"since": datetime(2025, 1, 15, 10, 30, 0), "source": "C:\\logs\\app.log"}
        )
        assert result == "SELECT * FROM data WHERE at > '2025-01-15T10:30:00' AND source = 'C:\\logs\\app.log'"


# ---------------------------------------------------------------------------
# execute_raw_query routing
# ---------------------------------------------------------------------------


class TestExecuteRawQueryRouting:
    @pytest.mark.asyncio
    async def test_missing_connection_obj(self):
        result = await AsyncRawQueryService.execute_raw_query(
            query="SELECT 1",
            db_type="pg",
            connection_id="conn-1",
            connection_obj=None,
        )
        assert "error" in result
        assert "Missing connection" in result["error"]

    @pytest.mark.asyncio
    async def test_unsupported_db_type(self):
        result = await AsyncRawQueryService.execute_raw_query(
            query="SELECT 1",
            db_type="oracle",
            connection_id="conn-1",
            connection_obj={"host": "localhost"},
        )
        assert result.get("success") is False
        assert "Unsupported" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_mongo_write_blocked(self):
        result = await AsyncRawQueryService.execute_raw_query(
            query='db.users.insertOne({"name": "test"})',
            db_type="mongo",
            connection_id="conn-1",
            connection_obj={"connection_string": "mongodb://localhost/test"},
        )
        assert result.get("success") is False
        assert "not allowed" in result.get("error", "").lower() or "write" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_dynamodb_partiql_write_blocked(self):
        result = await AsyncRawQueryService.execute_raw_query(
            query="DELETE FROM Users WHERE userId='abc'",
            db_type="dynamodb",
            connection_id="conn-1",
            connection_obj={"region": "us-east-1", "access_key_id": "x", "secret_access_key": "y"},
        )
        assert result.get("success") is False

    @pytest.mark.asyncio
    async def test_dynamodb_native_write_blocked(self):
        result = await AsyncRawQueryService.execute_raw_query(
            query='{"operation": "put_item", "table": "Users"}',
            db_type="dynamodb",
            connection_id="conn-1",
            connection_obj={
                "region": "us-east-1",
                "access_key_id": "x",
                "secret_access_key": "y",
                "query_mode": "native",
            },
        )
        assert result.get("success") is False


# ---------------------------------------------------------------------------
# execute_raw_query parameter handling per backend (connectors mocked)
# ---------------------------------------------------------------------------


class TestExecuteRawQueryParamInlining:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("db_type", ["csv", "duckdb", "parquet", "excel", "json", "file"])
    async def test_file_query_inlines_backslash_value(self, db_type):
        execute = AsyncMock(return_value={"success": True, "result": []})
        with patch.object(DataFrameFileService, "execute_duckdb_query", execute):
            result = await AsyncRawQueryService.execute_raw_query(
                query="SELECT * FROM data WHERE source = :source",
                db_type=db_type,
                connection_id="conn-1",
                connection_obj={"dataset_id": "ds-1"},
                params={"source": "C:\\Users\\analyst\\q1.csv"},
            )

        assert result == {"success": True, "result": []}
        assert execute.await_args.kwargs["query"] == "SELECT * FROM data WHERE source = 'C:\\Users\\analyst\\q1.csv'"

    @pytest.mark.asyncio
    async def test_dynamodb_partiql_inlines_backslash_value(self):
        connector = MagicMock()
        connector.execute_partiql_query = AsyncMock(return_value={"success": True, "result": []})
        with patch.object(AsyncDatabaseService, "get_or_create_dynamodb_connector", AsyncMock(return_value=connector)):
            result = await AsyncRawQueryService.execute_raw_query(
                query="SELECT * FROM Files WHERE path = :path",
                db_type="dynamodb",
                connection_id="conn-1",
                connection_obj={"region": "us-east-1", "query_mode": "partiql"},
                params={"path": "C:\\Users\\analyst\\q1.csv"},
            )

        assert result == {"success": True, "result": []}
        statement = connector.execute_partiql_query.await_args.args[0]
        assert statement == "SELECT * FROM Files WHERE path = 'C:\\Users\\analyst\\q1.csv'"

    @pytest.mark.asyncio
    async def test_sql_path_binds_params_without_inlining(self):
        connector = MagicMock()
        connector.execute_query = AsyncMock(return_value={"success": True, "result": []})
        params = {"source": "C:\\Users\\analyst\\q1.csv"}
        with patch.object(AsyncDatabaseService, "get_or_create_sql_connector", AsyncMock(return_value=connector)):
            result = await AsyncRawQueryService.execute_raw_query(
                query="SELECT * FROM data WHERE source = :source",
                db_type="pg",
                connection_id="conn-1",
                connection_obj={"host": "localhost"},
                params=params,
            )

        assert result == {"success": True, "result": []}
        await_args = connector.execute_query.await_args
        assert await_args.args[0] == "SELECT * FROM data WHERE source = :source"
        assert await_args.kwargs["params"] == params
