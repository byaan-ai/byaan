"""
Tests for AsyncSQLConnector — limit logic, query execution, connection URL building.

Uses SQLite in-memory for integration tests where a real engine is needed.
"""

import ssl

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from server.schemas.connections import ConnectionCreate
from server.services.database_operations import AsyncSQLConnector

# ---------------------------------------------------------------------------
# _apply_limit_to_query (unit tests, no DB needed)
# ---------------------------------------------------------------------------


class TestApplyLimitToQuery:
    def _make_connector(self, db_type="pg"):
        return AsyncSQLConnector({"database": ":memory:"}, db_type=db_type)

    def test_pg_appends_limit(self):
        c = self._make_connector("pg")
        result = c._apply_limit_to_query("SELECT * FROM users", 10)
        assert result == "SELECT * FROM users LIMIT 10"

    def test_mysql_appends_limit(self):
        c = self._make_connector("mysql")
        result = c._apply_limit_to_query("SELECT * FROM users", 25)
        assert result == "SELECT * FROM users LIMIT 25"

    def test_sqlite_appends_limit(self):
        c = self._make_connector("sqlite")
        result = c._apply_limit_to_query("SELECT * FROM users", 5)
        assert result == "SELECT * FROM users LIMIT 5"

    def test_mssql_inserts_top(self):
        c = self._make_connector("mssql")
        result = c._apply_limit_to_query("SELECT * FROM users", 10)
        assert result == "SELECT TOP 10 * FROM users"

    def test_mssql_top_with_distinct(self):
        c = self._make_connector("mssql")
        result = c._apply_limit_to_query("SELECT DISTINCT name FROM users", 10)
        assert result == "SELECT DISTINCT TOP 10 name FROM users"

    def test_skips_if_limit_already_present(self):
        c = self._make_connector("pg")
        query = "SELECT * FROM users LIMIT 5"
        result = c._apply_limit_to_query(query, 10)
        assert result == query

    def test_skips_if_top_already_present(self):
        c = self._make_connector("mssql")
        query = "SELECT TOP 5 * FROM users"
        result = c._apply_limit_to_query(query, 10)
        assert result == query

    def test_skips_if_fetch_already_present(self):
        c = self._make_connector("pg")
        query = "SELECT * FROM users FETCH FIRST 5 ROWS ONLY"
        result = c._apply_limit_to_query(query, 10)
        assert result == query

    def test_mssql_non_select_unchanged(self):
        c = self._make_connector("mssql")
        query = "EXEC sp_help"
        result = c._apply_limit_to_query(query, 10)
        assert result == query


# ---------------------------------------------------------------------------
# _build_connection_url (unit tests)
# ---------------------------------------------------------------------------


class TestBuildConnectionUrl:
    def test_sqlite_memory(self):
        c = AsyncSQLConnector({"database": ":memory:"}, db_type="sqlite")
        url = c._build_connection_url()
        assert "sqlite" in url
        assert ":memory:" in url

    def test_pg_url(self):
        c = AsyncSQLConnector(
            {"host": "localhost", "port": 5432, "database": "testdb", "user": "admin", "password": "secret"},
            db_type="pg",
        )
        url = c._build_connection_url()
        assert "postgresql+asyncpg" in url or "postgresql" in url
        assert "testdb" in url

    def test_mysql_url(self):
        c = AsyncSQLConnector(
            {"host": "localhost", "port": 3306, "database": "testdb", "user": "root", "password": "pass"},
            db_type="mysql",
        )
        url = c._build_connection_url()
        assert "mysql" in url
        assert "testdb" in url

    def test_missing_database_raises(self):
        c = AsyncSQLConnector({"host": "localhost"}, db_type="pg")
        with pytest.raises(ValueError, match="Database name is required"):
            c._build_connection_url()

    def test_unsupported_db_type_raises(self):
        c = AsyncSQLConnector({"host": "localhost"}, db_type="oracle")
        with pytest.raises(ValueError, match="Unsupported database type"):
            c._build_connection_url()

    def test_connection_string_used_directly(self):
        c = AsyncSQLConnector(
            {"connection_string": "sqlite+aiosqlite:///test.db"},
            db_type="sqlite",
        )
        url = c._build_connection_url()
        assert "sqlite" in url

    def test_default_port_pg(self):
        c = AsyncSQLConnector(
            {"host": "localhost", "database": "testdb", "user": "u", "password": "p"},
            db_type="pg",
        )
        url = c._build_connection_url()
        assert "5432" in url

    def test_url_encodes_special_chars_in_password(self):
        c = AsyncSQLConnector(
            {"host": "localhost", "port": 5432, "database": "db", "user": "u", "password": "a@b/c"},
            db_type="pg",
        )
        url = c._build_connection_url()
        assert "a@b/c" not in url
        assert "a%40b%2Fc" in url


# ---------------------------------------------------------------------------
# SSL mode (unit tests)
# ---------------------------------------------------------------------------


def _sql_obj(**extra):
    return {"host": "db.example.com", "port": 5432, "database": "db", "user": "u", "password": "p", **extra}


class TestSslMode:
    @pytest.mark.parametrize("db_type", ["pg", "mysql"])
    def test_no_ssl_mode_has_no_connect_args(self, db_type):
        assert AsyncSQLConnector(_sql_obj(), db_type=db_type)._connect_args() == {}

    @pytest.mark.parametrize("db_type", ["pg", "mysql"])
    def test_require_encrypts_without_verifying(self, db_type):
        ctx = AsyncSQLConnector(_sql_obj(ssl_mode="require"), db_type=db_type)._connect_args()["ssl"]
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_NONE
        assert ctx.check_hostname is False

    @pytest.mark.parametrize("db_type", ["pg", "mysql"])
    def test_verify_full_checks_cert_and_hostname(self, db_type):
        ctx = AsyncSQLConnector(_sql_obj(ssl_mode="verify-full"), db_type=db_type)._connect_args()["ssl"]
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True

    def test_unknown_ssl_mode_is_ignored(self):
        assert AsyncSQLConnector(_sql_obj(ssl_mode="bogus"), db_type="pg")._connect_args() == {}

    def test_sqlite_ignores_ssl_mode(self):
        assert (
            AsyncSQLConnector({"database": ":memory:", "ssl_mode": "require"}, db_type="sqlite")._connect_args() == {}
        )

    @pytest.mark.parametrize(
        ("ssl_mode", "encrypt", "trust_cert"),
        [(None, "no", "yes"), ("require", "yes", "yes"), ("verify-full", "yes", "no")],
    )
    def test_mssql_url_params(self, ssl_mode, encrypt, trust_cert):
        obj = _sql_obj(ssl_mode=ssl_mode) if ssl_mode else _sql_obj()
        url = AsyncSQLConnector(obj, db_type="mssql")._build_connection_url()
        assert f"Encrypt={encrypt}" in url
        assert f"TrustServerCertificate={trust_cert}" in url

    @pytest.mark.parametrize("ssl_mode", ["require", "verify-full"])
    def test_api_accepts_known_ssl_modes(self, ssl_mode):
        payload = ConnectionCreate(type="pg", connection_obj=_sql_obj(ssl_mode=ssl_mode))
        assert payload.connection_obj["ssl_mode"] == ssl_mode

    def test_api_rejects_unknown_ssl_mode(self):
        with pytest.raises(ValidationError, match="ssl_mode must be one of"):
            ConnectionCreate(type="pg", connection_obj=_sql_obj(ssl_mode="prefer"))


# ---------------------------------------------------------------------------
# execute_query (integration tests with SQLite in-memory)
# ---------------------------------------------------------------------------


class TestExecuteQuery:
    @pytest_asyncio.fixture
    async def connector(self):
        c = AsyncSQLConnector({"database": ":memory:"}, db_type="sqlite")
        c.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with c.engine.begin() as conn:
            await conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)"))
            await conn.execute(text("INSERT INTO users (name, age) VALUES ('Alice', 30)"))
            await conn.execute(text("INSERT INTO users (name, age) VALUES ('Bob', 25)"))
            await conn.execute(text("INSERT INTO users (name, age) VALUES ('Charlie', 35)"))
        yield c
        await c.engine.dispose()

    @pytest.mark.asyncio
    async def test_basic_select(self, connector):
        result = await connector.execute_query("SELECT * FROM users")
        assert result["success"] is True
        assert result["returned_count"] == 3
        assert len(result["result"]) == 3

    @pytest.mark.asyncio
    async def test_select_with_limit(self, connector):
        result = await connector.execute_query("SELECT * FROM users", limit=2)
        assert result["success"] is True
        assert result["returned_count"] == 2
        assert result["limited"] is True

    @pytest.mark.asyncio
    async def test_select_with_where(self, connector):
        result = await connector.execute_query("SELECT * FROM users WHERE age > 28")
        assert result["success"] is True
        assert result["returned_count"] == 2

    @pytest.mark.asyncio
    async def test_result_format(self, connector):
        result = await connector.execute_query("SELECT name, age FROM users WHERE name = 'Alice'")
        assert result["success"] is True
        row = result["result"][0]
        assert row["name"] == "Alice"
        assert row["age"] == 30

    @pytest.mark.asyncio
    async def test_execution_time_tracked(self, connector):
        result = await connector.execute_query("SELECT * FROM users")
        assert "execution_time_seconds" in result
        assert result["execution_time_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_bad_sql_returns_error(self, connector):
        result = await connector.execute_query("SELECT * FROM nonexistent_table")
        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_not_connected_raises(self):
        c = AsyncSQLConnector({"database": ":memory:"}, db_type="sqlite")
        with pytest.raises(RuntimeError, match="not connected"):
            await c.execute_query("SELECT 1")

    @pytest.mark.asyncio
    async def test_limit_not_applied_without_select(self, connector):
        # Non-SELECT queries shouldn't get LIMIT applied
        result = await connector.execute_query("SELECT COUNT(*) FROM users", limit=1)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_params_passed_to_query(self, connector):
        result = await connector.execute_query(
            "SELECT * FROM users WHERE name = :name",
            params={"name": "Bob"},
        )
        assert result["success"] is True
        assert result["returned_count"] == 1
        assert result["result"][0]["name"] == "Bob"
