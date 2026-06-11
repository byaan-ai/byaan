from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from server.services.databricks_connector import AsyncDatabricksConnector


@pytest.fixture
def conn_obj():
    return {
        "server_hostname": "adb-1234.azuredatabricks.net",
        "http_path": "/sql/1.0/warehouses/abc123",
        "catalog": "main",
        "schema": "default",
        "oauth": {
            "access_token": "dapi-oauth-fake",
            "refresh_token": "rt-fake",
            "expires_at": 2**31 - 1,
            "server_hostname": "adb-1234.azuredatabricks.net",
        },
    }


def _fake_cursor(rows, description):
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor.description = description
    cursor.__enter__ = lambda s: s
    cursor.__exit__ = lambda *a: None
    return cursor


def _fake_connection(cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda s: s
    conn.__exit__ = lambda *a: None
    return conn


@pytest.mark.asyncio
async def test_connect_validates_required_fields():
    connector = AsyncDatabricksConnector({"server_hostname": "x"})
    with pytest.raises(ValueError, match="http_path"):
        await connector.connect()


@pytest.mark.asyncio
async def test_connect_requires_oauth_access_token():
    connector = AsyncDatabricksConnector({"server_hostname": "x", "http_path": "/y"})
    with pytest.raises(ValueError, match="OAuth access_token"):
        await connector.connect()


@pytest.mark.asyncio
async def test_execute_query_returns_rows(conn_obj):
    cursor = _fake_cursor(
        rows=[(1, "alice"), (2, "bob")],
        description=[
            ("id", "INT", None, None, None, None, None),
            ("name", "STRING", None, None, None, None, None),
        ],
    )
    fake_conn = _fake_connection(cursor)
    connector = AsyncDatabricksConnector(conn_obj)

    with patch("server.services.databricks_connector.sql.connect", return_value=fake_conn):
        await connector.connect()
        result = await connector.execute_query("SELECT id, name FROM users", limit=10)

    assert result["success"] is True
    assert result["result"] == [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]
    assert "execution_time_seconds" in result


@pytest.mark.asyncio
async def test_execute_query_applies_limit(conn_obj):
    cursor = _fake_cursor(rows=[], description=[])
    fake_conn = _fake_connection(cursor)
    connector = AsyncDatabricksConnector(conn_obj)

    with patch("server.services.databricks_connector.sql.connect", return_value=fake_conn):
        await connector.connect()
        await connector.execute_query("SELECT * FROM users", limit=5)

    executed_sql = cursor.execute.call_args[0][0]
    assert "LIMIT 5" in executed_sql.upper()


@pytest.mark.asyncio
async def test_execute_query_error_returns_failure(conn_obj):
    cursor = MagicMock()
    cursor.execute.side_effect = RuntimeError("syntax error")
    cursor.__enter__ = lambda s: s
    cursor.__exit__ = lambda *a: None
    fake_conn = _fake_connection(cursor)
    connector = AsyncDatabricksConnector(conn_obj)

    with patch("server.services.databricks_connector.sql.connect", return_value=fake_conn):
        result = await connector.execute_query("SELECT bad", limit=10)

    assert result["success"] is False
    assert "syntax error" in result["error"]


@pytest.mark.asyncio
async def test_get_schema_with_catalog_and_schema_returns_tables(conn_obj):
    show_tables_cursor = _fake_cursor(
        rows=[("default", "users", False), ("default", "orders", False)],
        description=[
            ("database", "STRING", None, None, None, None, None),
            ("tableName", "STRING", None, None, None, None, None),
            ("isTemporary", "BOOLEAN", None, None, None, None, None),
        ],
    )
    describe_users = _fake_cursor(
        rows=[("id", "INT", None), ("name", "STRING", None)],
        description=[
            ("col_name", "STRING", None, None, None, None, None),
            ("data_type", "STRING", None, None, None, None, None),
            ("comment", "STRING", None, None, None, None, None),
        ],
    )
    describe_orders = _fake_cursor(
        rows=[("id", "INT", None), ("amount", "DECIMAL", None)],
        description=describe_users.description,
    )

    cursors = [show_tables_cursor, describe_users, describe_orders]
    fake_conn = MagicMock()
    fake_conn.cursor.side_effect = cursors
    fake_conn.__enter__ = lambda s: s
    fake_conn.__exit__ = lambda *a: None

    connector = AsyncDatabricksConnector(conn_obj)
    with patch("server.services.databricks_connector.sql.connect", return_value=fake_conn):
        schema = await connector.get_schema()

    table_names = [t["name"] for t in schema["tables"]]
    assert "users" in table_names
    assert "orders" in table_names


@pytest.mark.asyncio
async def test_get_schema_with_catalog_only_iterates_schemas():
    obj = {
        "server_hostname": "x",
        "http_path": "/y",
        "catalog": "samples",
        "oauth": {
            "access_token": "z",
            "refresh_token": "rt",
            "expires_at": 2**31 - 1,
            "server_hostname": "x",
        },
    }
    schemas_cursor = _fake_cursor(
        rows=[("tpch",), ("information_schema",)],
        description=[("schema", "STRING", None, None, None, None, None)],
    )
    tables_cursor = _fake_cursor(
        rows=[("tpch", "customer", False)],
        description=[
            ("database", "STRING", None, None, None, None, None),
            ("tableName", "STRING", None, None, None, None, None),
            ("isTemporary", "BOOLEAN", None, None, None, None, None),
        ],
    )
    describe_cursor = _fake_cursor(
        rows=[("c_custkey", "INT", None)],
        description=[
            ("col_name", "STRING", None, None, None, None, None),
            ("data_type", "STRING", None, None, None, None, None),
            ("comment", "STRING", None, None, None, None, None),
        ],
    )

    fake_conn = MagicMock()
    fake_conn.cursor.side_effect = [schemas_cursor, tables_cursor, describe_cursor]
    fake_conn.__enter__ = lambda s: s
    fake_conn.__exit__ = lambda *a: None

    connector = AsyncDatabricksConnector(obj)
    with patch("server.services.databricks_connector.sql.connect", return_value=fake_conn):
        schema = await connector.get_schema()

    assert "tpch" in schema["schemas"]
    assert "information_schema" not in schema["schemas"]
    assert any(t["qualified_name"] == "samples.tpch.customer" for t in schema["tables"])


@pytest.mark.asyncio
async def test_list_catalog_tree_returns_tree_excluding_system():
    obj = {
        "server_hostname": "x",
        "http_path": "/y",
        "oauth": {
            "access_token": "z",
            "refresh_token": "rt",
            "expires_at": 2**31 - 1,
            "server_hostname": "x",
        },
    }

    catalogs_cursor = _fake_cursor(
        rows=[("main",), ("analytics",), ("system",)],
        description=[("catalog", "STRING", None, None, None, None, None)],
    )
    main_schemas_cursor = _fake_cursor(
        rows=[("default",), ("gold",), ("information_schema",)],
        description=[("schema", "STRING", None, None, None, None, None)],
    )
    analytics_schemas_cursor = _fake_cursor(
        rows=[("events",)],
        description=[("schema", "STRING", None, None, None, None, None)],
    )

    fake_conn = MagicMock()
    fake_conn.cursor.side_effect = [catalogs_cursor, main_schemas_cursor, analytics_schemas_cursor]
    fake_conn.__enter__ = lambda s: s
    fake_conn.__exit__ = lambda *a: None

    connector = AsyncDatabricksConnector(obj)
    with patch("server.services.databricks_connector.sql.connect", return_value=fake_conn):
        tree = await connector.list_catalog_tree()

    names = [c["name"] for c in tree]
    assert "main" in names and "analytics" in names
    assert "system" not in names
    main = next(c for c in tree if c["name"] == "main")
    assert "default" in main["schemas"]
    assert "gold" in main["schemas"]
    assert "information_schema" not in main["schemas"]


@pytest.mark.asyncio
async def test_expired_oauth_triggers_refresh_and_callback(conn_obj):
    """When access_token is near expiry, connector calls the OAuth refresh helper
    and invokes the on_token_refresh callback with the new oauth block."""
    conn_obj["oauth"]["expires_at"] = 0  # forces refresh

    cursor = _fake_cursor(rows=[(1,)], description=[("c", "INT", None, None, None, None, None)])
    fake_conn = _fake_connection(cursor)

    captured: dict = {}

    async def cb(new_oauth):
        captured.update(new_oauth)

    refreshed = {
        "access_token": "fresh-token",
        "refresh_token": "rotated-rt",
        "expires_at": 2**31 - 1,
        "scope": "sql offline_access",
        "server_hostname": conn_obj["server_hostname"],
    }

    from unittest.mock import AsyncMock

    connector = AsyncDatabricksConnector(conn_obj, on_token_refresh=cb)

    with (
        patch("server.services.databricks_connector.sql.connect", return_value=fake_conn),
        patch(
            "server.services.databricks_oauth_service.refresh_databricks_token",
            new=AsyncMock(return_value=refreshed),
        ),
        patch(
            "server.services.databricks_oauth_service.get_oauth_credentials",
            new=AsyncMock(return_value=("client-id", "client-secret")),
        ),
        patch("server.db.session.AsyncSessionFactory") as factory_mock,
    ):
        factory_mock.return_value.__aenter__.return_value = MagicMock()
        factory_mock.return_value.__aexit__.return_value = False
        await connector.connect()

    assert captured["access_token"] == "fresh-token"
    assert captured["refresh_token"] == "rotated-rt"
    assert connector.connection_obj["oauth"]["access_token"] == "fresh-token"


@pytest.mark.asyncio
async def test_async_database_service_caches_databricks_connector(conn_obj):
    from server.services.database_operations import AsyncDatabaseService

    cursor = _fake_cursor(
        rows=[(1,)],
        description=[("c", "INT", None, None, None, None, None)],
    )
    fake_conn = _fake_connection(cursor)

    with patch("server.services.databricks_connector.sql.connect", return_value=fake_conn):
        c1 = await AsyncDatabaseService.get_or_create_databricks_connector("test-conn-id", conn_obj)
        c2 = await AsyncDatabaseService.get_or_create_databricks_connector("test-conn-id", conn_obj)

    assert c1 is c2
    await AsyncDatabaseService.close_connection("test-conn-id")
