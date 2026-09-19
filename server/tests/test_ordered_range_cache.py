from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from server.schemas.query import QueryFilter
from server.services.database_operations import DatabaseOperationsService
from server.services.query_cache import QueryResultCache, generate_cache_key

QUERY_ID = "11111111-1111-1111-1111-111111111111"
BASE_QUERY = "SELECT id FROM orders ORDER BY id"

# Key produced by the pre-fix normalizer for between value [10, 20] (bounds already sorted).
# New between keys must not land on this digest, so legacy ambiguous entries are never reused.
LEGACY_SORTED_BETWEEN_KEY = "byaan:query:11111111-1111-1111-1111-111111111111:f:3dc05605b51dce17"


def _between(low: Any, high: Any) -> QueryFilter:
    return QueryFilter(field="amount", operator="between", value=[low, high], ui_type="input")


@pytest_asyncio.fixture
async def orders_connection():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount INTEGER)"))
        await conn.execute(text("INSERT INTO orders (id, amount) VALUES (1, 5), (2, 15), (3, 25)"))

    async with engine.connect() as conn:
        yield conn

    await engine.dispose()


async def _run_filtered_query(conn, filters: list[QueryFilter]) -> list[int]:
    sql, params = DatabaseOperationsService.apply_filters_to_sql(BASE_QUERY, filters, "sqlite")
    result = await conn.execute(text(sql), params)
    return [row[0] for row in result.fetchall()]


def test_between_key_differs_when_bounds_are_reversed() -> None:
    assert generate_cache_key(QUERY_ID, [_between(10, 20)]) != generate_cache_key(QUERY_ID, [_between(20, 10)])


def test_between_key_does_not_reuse_legacy_ambiguous_digest() -> None:
    assert generate_cache_key(QUERY_ID, [_between(10, 20)]) != LEGACY_SORTED_BETWEEN_KEY
    assert generate_cache_key(QUERY_ID, [_between(20, 10)]) != LEGACY_SORTED_BETWEEN_KEY


def test_between_key_is_deterministic_for_identical_ordered_bounds() -> None:
    assert generate_cache_key(QUERY_ID, [_between("2025-01-01", "2025-03-31")]) == generate_cache_key(
        QUERY_ID, [_between("2025-01-01", "2025-03-31")]
    )


def test_in_filter_key_ignores_value_order() -> None:
    forward = [QueryFilter(field="region", operator="in", value=["APAC", "EMEA"], ui_type="multiselect")]
    reordered = [QueryFilter(field="region", operator="in", value=["EMEA", "APAC"], ui_type="multiselect")]

    assert generate_cache_key(QUERY_ID, forward) == generate_cache_key(QUERY_ID, reordered)


def test_keys_without_filters_are_unchanged() -> None:
    assert generate_cache_key(QUERY_ID) == f"byaan:query:{QUERY_ID}"
    assert generate_cache_key(QUERY_ID, []) == f"byaan:query:{QUERY_ID}"


def test_mixed_filter_key_is_field_order_stable_and_range_sensitive() -> None:
    status = QueryFilter(field="status", operator="eq", value="open", ui_type="select")
    region = QueryFilter(field="region", operator="in", value=["EMEA", "APAC"], ui_type="multiselect")
    amount = _between(10, 20)
    reversed_amount = _between(20, 10)

    baseline = generate_cache_key(QUERY_ID, [status, region, amount])

    assert baseline == generate_cache_key(QUERY_ID, [amount, status, region])
    assert baseline == generate_cache_key(
        QUERY_ID,
        [status, QueryFilter(field="region", operator="in", value=["APAC", "EMEA"], ui_type="multiselect"), amount],
    )
    assert baseline != generate_cache_key(QUERY_ID, [status, region, reversed_amount])


@pytest.mark.asyncio
async def test_reversed_range_does_not_reuse_cached_normal_range_result(orders_connection) -> None:
    normal = [_between(10, 20)]
    reversed_range = [_between(20, 10)]

    normal_rows = await _run_filtered_query(orders_connection, normal)
    reversed_rows = await _run_filtered_query(orders_connection, reversed_range)

    assert normal_rows == [2]
    assert reversed_rows == []
    assert normal_rows != reversed_rows

    cache = QueryResultCache()
    normal_key = cache.generate_key(QUERY_ID, normal)
    reversed_key = cache.generate_key(QUERY_ID, reversed_range)
    await cache.set(normal_key, {"data": normal_rows, "query_name": "orders"})

    assert await cache.get(normal_key) == {"data": normal_rows, "query_name": "orders"}
    assert await cache.get(reversed_key) is None
