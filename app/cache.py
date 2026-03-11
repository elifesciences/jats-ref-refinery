"""Abstract cache interface with an in-process dict implementation.

The interface is intentionally thin so that another solution (e.g. Redis)
can be swapped in for multi-pod Kubernetes deployments while keeping 
enricher.py unaffected.
"""

from __future__ import annotations

import abc
from typing import Any, Optional


class AbstractCache(abc.ABC):
    @abc.abstractmethod
    def get(self, key: str) -> Optional[Any]:
        ...

    @abc.abstractmethod
    def set(self, key: str, value: Any) -> None:
        ...


class InProcessCache(AbstractCache):
    """Thread-safe (GIL) in-process dict cache. Suitable for single-pod
    deployments."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def get(self, key: str) -> Optional[Any]:
        return self._store.get(key)

    def set(self, key: str, value: Any) -> None:
        self._store[key] = value


# Module-level singleton — swap out for Redis at startup if needed
_cache: AbstractCache = InProcessCache()


def get_cache() -> AbstractCache:
    return _cache


def set_cache(cache: AbstractCache) -> None:
    """Replace the global cache instance - e.g. inject Redis cache at start"""
    global _cache
    _cache = cache
