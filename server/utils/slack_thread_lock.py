"""Per-thread asyncio locks so concurrent Slack events serialize per thread."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

_locks: dict[str, asyncio.Lock] = {}
_waiters: dict[str, int] = {}
_registry_lock = asyncio.Lock()


def _key(team_id: str, channel_id: str, thread_ts: str) -> str:
    return f"{team_id}:{channel_id}:{thread_ts}"


@asynccontextmanager
async def acquire_thread_lock(team_id: str, channel_id: str, thread_ts: str):
    key = _key(team_id, channel_id, thread_ts)

    async with _registry_lock:
        lock = _locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _locks[key] = lock
        _waiters[key] = _waiters.get(key, 0) + 1

    try:
        async with lock:
            yield
    finally:
        async with _registry_lock:
            _waiters[key] -= 1
            if _waiters[key] <= 0:
                _waiters.pop(key, None)
                _locks.pop(key, None)
