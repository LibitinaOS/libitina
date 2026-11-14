from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


class TTLCache:
    """
    Tiny TTL cache. Enough to avoid hammering /points for repeated lookups.
    """
    def __init__(self, default_ttl_s: float = 900.0, max_items: int = 1024) -> None:
        self.default_ttl_s = float(default_ttl_s)
        self.max_items = int(max_items)
        self._store: Dict[Any, _CacheEntry] = {}

    def get(self, key: Any) -> Optional[Any]:
        ent = self._store.get(key)
        if not ent:
            return None
        if ent.expires_at < time.time():
            self._store.pop(key, None)
            return None
        return ent.value

    def set(self, key: Any, value: Any, ttl_s: Optional[float] = None) -> None:
        ttl = self.default_ttl_s if ttl_s is None else float(ttl_s)
        if len(self._store) >= self.max_items:
            # lazy eviction: drop an arbitrary expired item, else drop first key
            now = time.time()
            expired = [k for k, v in self._store.items() if v.expires_at < now]
            if expired:
                for k in expired[: max(1, len(expired) // 2)]:
                    self._store.pop(k, None)
            else:
                first_key = next(iter(self._store.keys()), None)
                if first_key is not None:
                    self._store.pop(first_key, None)

        self._store[key] = _CacheEntry(value=value, expires_at=time.time() + ttl)

    def clear(self) -> None:
        self._store.clear()
