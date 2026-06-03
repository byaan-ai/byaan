from __future__ import annotations

import pytest

from server.tools.databricks import validate_databricks_query


@pytest.mark.parametrize(
    "stmt",
    [
        "INSERT INTO users VALUES (1, 'a')",
        "UPDATE users SET name='b' WHERE id=1",
        "DELETE FROM users WHERE id=1",
        "DROP TABLE users",
        "CREATE TABLE x (id INT)",
        "ALTER TABLE users ADD COLUMN c STRING",
        "MERGE INTO target USING source ON target.id = source.id WHEN MATCHED THEN DELETE",
        "TRUNCATE TABLE users",
        "GRANT SELECT ON users TO bob",
        "REVOKE SELECT ON users FROM bob",
        "COPY INTO users FROM '/tmp/x'",
        "REFRESH TABLE users",
        "VACUUM users",
        "OPTIMIZE users",
    ],
)
def test_write_statements_blocked(stmt):
    with pytest.raises(ValueError):
        validate_databricks_query(stmt)


@pytest.mark.parametrize(
    "stmt",
    [
        "SELECT * FROM samples.tpch.customer LIMIT 10",
        "SELECT n_name, COUNT(*) FROM samples.tpch.nation GROUP BY n_name",
        "WITH c AS (SELECT * FROM users) SELECT * FROM c",
        "SELECT a.id, b.name FROM users a JOIN orders b ON a.id = b.user_id",
    ],
)
def test_read_statements_allowed(stmt):
    result = validate_databricks_query(stmt)
    assert "SELECT" in result.upper() or "WITH" in result.upper()


def test_trailing_semicolon_stripped():
    result = validate_databricks_query("SELECT 1;")
    assert not result.endswith(";")


def test_malformed_sql_raises():
    with pytest.raises(ValueError):
        validate_databricks_query("NOT_A_QUERY {{{")
