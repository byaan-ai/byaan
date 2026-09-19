"""
Nested DuckDB values (LIST / STRUCT / MAP) must survive DuckDBService._json_safe
so that query results and inferred schema samples stay JSON-serialisable for the
agent tool layer.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from server.services.duckdb_service import DuckDBFileDescriptor, DuckDBService

NESTED_SELECT = (
    "SELECT "
    "[DATE '2025-01-15', DATE '2025-02-20'] AS event_dates, "
    "{'day': DATE '2025-03-01', 'amount': 19.95, 'payload': 'hi'::BLOB} AS record, "
    "MAP {'opened': DATE '2025-04-01'} AS text_keyed, "
    "MAP {DATE '2025-05-01': 7} AS date_keyed, "
    "[{'at': TIMESTAMP '2025-06-01 08:30:00'}] AS list_of_structs "
    "FROM rows"
)


@pytest.fixture
def csv_descriptor(tmp_path: Path) -> DuckDBFileDescriptor:
    path = tmp_path / "rows.csv"
    path.write_text("id\n1\n")
    return DuckDBFileDescriptor(alias="rows", path=path, file_type="csv", filename="rows.csv")


def write_parquet(tmp_path: Path, select_sql: str) -> DuckDBFileDescriptor:
    """Materialise a tiny Parquet file inside tmp_path, bypassing DuckDBService."""
    path = tmp_path / "nested.parquet"
    conn = duckdb.connect(database=":memory:", read_only=False)
    try:
        conn.execute(f"COPY ({select_sql}) TO '{path}' (FORMAT PARQUET)")
    finally:
        conn.close()
    return DuckDBFileDescriptor(alias="nested", path=path, file_type="parquet", filename="nested.parquet")


async def _first_record(descriptor: DuckDBFileDescriptor, query: str) -> dict:
    result = await DuckDBService.execute_sql([descriptor], query, limit=5)
    assert result["success"] is True
    return result["result"][0]


# ---------------------------------------------------------------------------
# Real DuckDB query path
# ---------------------------------------------------------------------------


class TestNestedQueryResults:
    async def test_list_of_dates_is_converted(self, csv_descriptor):
        record = await _first_record(csv_descriptor, NESTED_SELECT)
        assert record["event_dates"] == ["2025-01-15", "2025-02-20"]

    async def test_struct_leaves_are_converted(self, csv_descriptor):
        record = await _first_record(csv_descriptor, NESTED_SELECT)
        assert record["record"] == {"day": "2025-03-01", "amount": 19.95, "payload": "hi"}

    async def test_map_values_are_converted(self, csv_descriptor):
        record = await _first_record(csv_descriptor, NESTED_SELECT)
        assert record["text_keyed"] == {"opened": "2025-04-01"}

    async def test_map_keys_are_converted(self, csv_descriptor):
        record = await _first_record(csv_descriptor, NESTED_SELECT)
        assert record["date_keyed"] == {"2025-05-01": 7}

    async def test_struct_inside_list_is_converted(self, csv_descriptor):
        record = await _first_record(csv_descriptor, NESTED_SELECT)
        assert record["list_of_structs"] == [{"at": "2025-06-01T08:30:00"}]

    async def test_result_survives_tool_layer_json_dumps(self, csv_descriptor):
        """server/tools/dataframe.py serialises the payload with a plain json.dumps."""
        result = await DuckDBService.execute_sql([csv_descriptor], NESTED_SELECT, limit=5)
        payload = json.dumps(result, indent=2)
        assert json.loads(payload)["result"][0]["event_dates"] == ["2025-01-15", "2025-02-20"]

    async def test_primitive_columns_are_untouched(self, csv_descriptor):
        query = (
            "SELECT 42 AS n, 'text' AS s, 1.5 AS f, TRUE AS b, NULL AS nothing, "
            "DATE '2025-07-04' AS d, 9.99::DECIMAL(5,2) AS money FROM rows"
        )
        record = await _first_record(csv_descriptor, query)
        assert record == {
            "n": 42,
            "s": "text",
            "f": 1.5,
            "b": True,
            "nothing": None,
            "d": "2025-07-04",
            "money": 9.99,
        }

    async def test_colliding_map_keys_are_rejected(self, csv_descriptor):
        query = "SELECT MAP {'\\xFF'::BLOB: 1, 'ff'::BLOB: 2} AS blob_keyed FROM rows"
        with pytest.raises(ValueError, match="collides"):
            await DuckDBService.execute_sql([csv_descriptor], query, limit=5)


# ---------------------------------------------------------------------------
# _json_safe recursion
# ---------------------------------------------------------------------------


class TestJsonSafeRecursion:
    def test_list_leaves(self):
        assert DuckDBService._json_safe([date(2025, 1, 1), Decimal("2.50"), b"ok"]) == ["2025-01-01", 2.5, "ok"]

    def test_tuple_becomes_list(self):
        assert DuckDBService._json_safe((date(2025, 1, 1), 1)) == ["2025-01-01", 1]

    def test_dict_values(self):
        assert DuckDBService._json_safe({"d": date(2025, 1, 1)}) == {"d": "2025-01-01"}

    def test_deeply_nested(self):
        value = {"rows": [{"stamps": [datetime(2025, 1, 1, 12, 0)]}]}
        assert DuckDBService._json_safe(value) == {"rows": [{"stamps": ["2025-01-01T12:00:00"]}]}

    def test_nan_inside_list(self):
        assert DuckDBService._json_safe([float("nan"), float("inf")]) == [None, "Infinity"]

    def test_empty_containers(self):
        assert DuckDBService._json_safe([]) == []
        assert DuckDBService._json_safe({}) == {}

    def test_scalar_map_keys_keep_json_native_types(self):
        assert DuckDBService._json_safe({1: "a", 2: "b"}) == {1: "a", 2: "b"}

    def test_non_string_keys_are_converted(self):
        assert DuckDBService._json_safe({date(2025, 5, 1): 7}) == {"2025-05-01": 7}

    def test_colliding_keys_raise(self):
        with pytest.raises(ValueError, match="collides"):
            DuckDBService._json_safe({b"\xff": 1, b"ff": 2})


# ---------------------------------------------------------------------------
# Real DuckDB schema-inference path (collect_schema over a local Parquet file)
# ---------------------------------------------------------------------------


NESTED_PARQUET_SELECT = (
    "SELECT "
    "{'day': DATE '2025-03-01', 'amount': 19.95::DECIMAL(6,2)} AS record, "
    "[DATE '2025-01-15', DATE '2025-02-20'] AS event_dates, "
    "MAP {'fee': 1.25::DECIMAL(6,2)} AS fees, "
    "[{'at': DATE '2025-06-01', 'total': 3.50::DECIMAL(6,2)}] AS list_of_structs"
)


class TestCollectSchemaNestedParquet:
    """Parquet is the only supported reader that carries DATE/DECIMAL inside nested types."""

    async def test_nested_sample_data_is_converted(self, tmp_path):
        descriptor = write_parquet(tmp_path, NESTED_PARQUET_SELECT)
        result = await DuckDBService.collect_schema([descriptor], sample_rows=5)
        record = result["sample_data"]["nested"][0]
        assert record["record"] == {"day": "2025-03-01", "amount": 19.95}
        assert record["event_dates"] == ["2025-01-15", "2025-02-20"]
        assert record["fees"] == {"fee": 1.25}
        assert record["list_of_structs"] == [{"at": "2025-06-01", "total": 3.5}]

    async def test_sample_data_survives_plain_json_dumps(self, tmp_path):
        """Callers serialise the schema payload without a json.dumps default= hook."""
        descriptor = write_parquet(tmp_path, NESTED_PARQUET_SELECT)
        result = await DuckDBService.collect_schema([descriptor], sample_rows=5)
        payload = json.dumps(result["sample_data"])
        assert json.loads(payload)["nested"][0]["record"]["day"] == "2025-03-01"

    async def test_column_sample_values_survive_plain_json_dumps(self, tmp_path):
        descriptor = write_parquet(tmp_path, NESTED_PARQUET_SELECT)
        result = await DuckDBService.collect_schema([descriptor], sample_rows=5)
        columns = result["schema"]["nested"]["columns"]
        json.dumps(columns)
        by_name = {column["name"]: column for column in columns}
        assert by_name["record"]["sample_values"] == [{"day": "2025-03-01", "amount": 19.95}]

    async def test_flat_columns_are_unaffected(self, tmp_path):
        descriptor = write_parquet(
            tmp_path,
            "SELECT 42 AS n, 'text' AS s, DATE '2025-07-04' AS d, 9.99::DECIMAL(5,2) AS money",
        )
        result = await DuckDBService.collect_schema([descriptor], sample_rows=5)
        assert result["sample_data"]["nested"][0] == {
            "n": 42,
            "s": "text",
            "d": "2025-07-04",
            "money": 9.99,
        }

    async def test_colliding_binary_map_keys_are_rejected(self, tmp_path):
        """
        b'\\xff' hex-encodes to "ff" and b'ff' UTF8-decodes to "ff", so the two distinct
        BLOB keys converge. Schema inference rejects this rather than dropping an entry.
        """
        descriptor = write_parquet(tmp_path, "SELECT MAP {'\\xFF'::BLOB: 1, 'ff'::BLOB: 2} AS blob_keyed")
        with pytest.raises(ValueError, match="collides"):
            await DuckDBService.collect_schema([descriptor], sample_rows=5)

    async def test_collision_error_names_the_remedy(self, tmp_path):
        descriptor = write_parquet(tmp_path, "SELECT MAP {'\\xFF'::BLOB: 1, 'ff'::BLOB: 2} AS blob_keyed")
        with pytest.raises(ValueError, match="Cast the map keys"):
            await DuckDBService.collect_schema([descriptor], sample_rows=5)

    async def test_explicitly_cast_keys_are_accepted(self, tmp_path):
        """The documented remedy: cast the keys to a distinct text representation."""
        descriptor = write_parquet(tmp_path, "SELECT MAP {hex('\\xFF'::BLOB): 1, hex('ff'::BLOB): 2} AS blob_keyed")
        result = await DuckDBService.collect_schema([descriptor], sample_rows=5)
        assert result["sample_data"]["nested"][0]["blob_keyed"] == {"FF": 1, "6666": 2}
        json.dumps(result["sample_data"])
