"""Small process-wide TTL cache for safe, repeatable query results.

Use Redis instead when running multiple backend replicas. This cache is shared by
all request-scoped SqlChatService instances in one process.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict

import pandas as pd


class QueryResultCache:
    def __init__(self, ttl_seconds: int, max_entries: int):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._values: OrderedDict[str, tuple[float, pd.DataFrame]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> pd.DataFrame | None:
        async with self._lock:
            cached = self._values.get(key)
            if not cached:
                return None
            created_at, dataframe = cached
            if time.monotonic() - created_at >= self.ttl_seconds:
                del self._values[key]
                return None
            self._values.move_to_end(key)
            return dataframe.copy(deep=True)

    async def set(self, key: str, dataframe: pd.DataFrame) -> None:
        async with self._lock:
            self._values[key] = (time.monotonic(), dataframe.copy(deep=True))
            self._values.move_to_end(key)
            while len(self._values) > self.max_entries:
                self._values.popitem(last=False)
