"""Query result cache.

Keys are ``byaan:query:{query_id}``, plus a digest of the normalized filters when filters are
present. ``between`` bounds are order-sensitive and keyed as ordered bounds; every other list
value (``in``/multiselect) stays order-insensitive.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from server.schemas.query import QueryFilter
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", 3600))  # 1 hour default
CACHE_PREFIX = "byaan:query"

# Marks the ordered-bounds encoding of a between filter. Bumping it separates new range
# keys from any older entry whose bounds were flattened into an order-insensitive list.
ORDERED_RANGE_FORMAT = "range.v1"


async def _safe_rollback(session: AsyncSession | None) -> None:
    if not session:
        return
    try:
        await session.rollback()
    except Exception:
        # Keep cache failures non-fatal.
        pass


@dataclass
class InMemoryCacheEntry:
    value: Any
    expires_at: float
    ttl: int
    created_at: float


def normalize_ordered_range_value(value: Any, ui_type: str) -> dict[str, Any]:
    """Normalize between bounds, keeping their order because [low, high] and [high, low] differ in SQL."""
    if isinstance(value, list):
        bounds = [str(v) for v in value]
    else:
        bounds = [str(normalize_filter_value(value, ui_type))]
    return {"fmt": ORDERED_RANGE_FORMAT, "b": bounds}


def normalize_filter_value(value: Any, ui_type: str, operator: str | None = None) -> Any:
    """Normalize filter values for deterministic cache keys."""
    if operator and operator.lower() == "between":
        return normalize_ordered_range_value(value, ui_type)
    if ui_type in ("date", "date_range"):
        if isinstance(value, str) and len(value) >= 10:
            return value[:10]
        if hasattr(value, "date"):
            return value.date().isoformat()
    if isinstance(value, list):
        return sorted([str(v) for v in value])
    return value


def generate_cache_key(query_id: str, filters: list[QueryFilter] | None = None) -> str:
    """Generate deterministic cache key from query_id and filters."""
    if not filters:
        return f"{CACHE_PREFIX}:{query_id}"

    normalized = []
    for f in sorted(filters, key=lambda x: x.field):
        operator = f.operator.lower()
        normalized.append(
            {
                "f": f.field,
                "o": operator,
                "v": normalize_filter_value(f.value, f.ui_type, operator),
            }
        )

    filter_hash = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()[:16]
    return f"{CACHE_PREFIX}:{query_id}:f:{filter_hash}"


class QueryResultCache:
    """PostgreSQL-backed cache with in-memory fallback for desktop mode."""

    def __init__(self) -> None:
        self._in_memory: dict[str, InMemoryCacheEntry] = {}
        self._dashboard_metadata: dict[str, dict[str, Any]] = {}

    def _prune_in_memory(self) -> None:
        now = time.time()
        expired = [k for k, v in self._in_memory.items() if v.expires_at <= now]
        for k in expired:
            self._in_memory.pop(k, None)

    async def get(self, key: str, session: AsyncSession | None = None) -> dict[str, Any] | None:
        """Get from PostgreSQL, fallback to in-memory."""
        if session:
            try:
                from server.repositories.query_cache import QueryCacheRepository

                repo = QueryCacheRepository(session)
                entry = await repo.get_valid(key)
                if entry:
                    return entry.result_data
            except Exception as e:
                logger.warning(f"PostgreSQL cache get failed for {key}: {e}")
                await _safe_rollback(session)

        entry = self._in_memory.get(key)
        if entry and entry.expires_at > time.time():
            return entry.value
        return None

    async def get_with_stale(self, key: str, session: AsyncSession | None = None) -> tuple[dict[str, Any] | None, bool]:
        """Get with staleness indicator for SWR pattern."""
        if session:
            try:
                from server.repositories.query_cache import QueryCacheRepository

                repo = QueryCacheRepository(session)
                return await repo.get_with_staleness(key)
            except Exception as e:
                logger.warning(f"PostgreSQL cache get_with_stale failed for {key}: {e}")
                await _safe_rollback(session)

        entry = self._in_memory.get(key)
        if not entry or entry.expires_at <= time.time():
            return None, False

        age = time.time() - entry.created_at
        is_stale = age > entry.ttl
        return entry.value, is_stale

    async def set(
        self,
        key: str,
        value: dict[str, Any],
        query_id: str | None = None,
        ttl: int | None = None,
        has_filters: bool = False,
        session: AsyncSession | None = None,
    ) -> None:
        """Set in PostgreSQL, always also store in-memory."""
        if ttl is None:
            ttl = CACHE_TTL_SECONDS

        if session and query_id:
            try:
                from server.repositories.query_cache import QueryCacheRepository

                repo = QueryCacheRepository(session)
                await repo.set_cache(
                    cache_key=key,
                    query_id=query_id,
                    result_data=value,
                    ttl_seconds=ttl,
                    has_filters=has_filters,
                )
            except Exception as e:
                logger.warning(f"PostgreSQL cache set failed for {key}: {e}")
                await _safe_rollback(session)

        now = time.time()
        self._in_memory[key] = InMemoryCacheEntry(
            value=value,
            expires_at=now + ttl,
            ttl=ttl,
            created_at=now,
        )
        self._prune_in_memory()

    async def invalidate(self, key: str, session: AsyncSession | None = None) -> None:
        """Invalidate a single cache key."""
        if session:
            try:
                from server.repositories.query_cache import QueryCacheRepository

                repo = QueryCacheRepository(session)
                await repo.invalidate_by_cache_key(key)
            except Exception as e:
                logger.warning(f"PostgreSQL cache invalidate failed for {key}: {e}")
                await _safe_rollback(session)

        self._in_memory.pop(key, None)

    async def invalidate_query(self, query_id: str, session: AsyncSession | None = None) -> int:
        """Invalidate all cache entries for a query."""
        count = 0

        if session:
            try:
                from server.repositories.query_cache import QueryCacheRepository

                repo = QueryCacheRepository(session)
                count = await repo.invalidate_by_query_id(query_id)
            except Exception as e:
                logger.warning(f"PostgreSQL cache invalidate_query failed for {query_id}: {e}")
                await _safe_rollback(session)

        prefix = f"{CACHE_PREFIX}:{query_id}"
        keys_to_remove = [k for k in self._in_memory if k.startswith(prefix)]
        for k in keys_to_remove:
            self._in_memory.pop(k, None)
        count += len(keys_to_remove)

        return count

    async def get_stats(self, session: AsyncSession | None = None) -> dict[str, Any]:
        """Get cache statistics."""
        stats = {
            "in_memory_entries": len(self._in_memory),
        }

        if session:
            try:
                from server.repositories.query_cache import QueryCacheRepository

                repo = QueryCacheRepository(session)
                pg_stats = await repo.get_stats()
                stats.update(pg_stats)
            except Exception as e:
                stats["postgresql_error"] = str(e)
                await _safe_rollback(session)

        return stats

    def generate_key(self, query_id: str, filters: list[QueryFilter] | None = None) -> str:
        """Generate cache key for query with optional filters."""
        return generate_cache_key(str(query_id), filters)

    async def get_dashboard_metadata(self, dashboard_id: str) -> dict[str, Any] | None:
        """Get dashboard cache metadata (in-memory only for simplicity)."""
        return self._dashboard_metadata.get(dashboard_id)

    async def set_dashboard_metadata(self, dashboard_id: str, metadata: dict[str, Any]) -> None:
        """Set dashboard cache metadata (in-memory only for simplicity)."""
        self._dashboard_metadata[dashboard_id] = metadata


query_result_cache = QueryResultCache()
