"""Abstract cache interface with an in-process LRU implementation.

The interface is intentionally thin so that another solution (e.g. Redis)
can be swapped in for multi-pod Kubernetes deployments while keeping
enricher.py unaffected.
"""

from __future__ import annotations

import abc
from collections import OrderedDict
from typing import Any, Optional

from app.config import CACHE_MAX_SIZE


class AbstractCache(abc.ABC):
    @abc.abstractmethod
    def get(self, key: str) -> Optional[Any]:
        ...

    @abc.abstractmethod
    def set(self, key: str, value: Any) -> None:
        ...


class InProcessCache(AbstractCache):
    """Least recently used cache."""

    def __init__(self, max_size: int = CACHE_MAX_SIZE) -> None:
        self._max_size = max_size
        self._store: OrderedDict[str, Any] = OrderedDict()

    def get(self, key: str) -> Optional[Any]:
        if key not in self._store:
            return None
        self._store.move_to_end(key)
        return self._store[key]

    def set(self, key: str, value: Any) -> None:
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = value
        if len(self._store) > self._max_size:
            self._store.popitem(last=False)


# Module-level singleton — swap out for Redis at startup if needed
_cache: AbstractCache = InProcessCache()


def get_cache() -> AbstractCache:
    return _cache


def set_cache(cache: AbstractCache) -> None:
    """Replace the global cache instance - e.g. inject Redis cache at start"""
    global _cache
    _cache = cache
