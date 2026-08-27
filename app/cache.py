"""In-memory TTL cache keyed by public_id — cuts repeat calls to LinkedIn."""

from __future__ import annotations

from cachetools import TTLCache

from .schemas import Profile


class ProfileCache:
    def __init__(self, ttl: int, maxsize: int = 1024) -> None:
        self._cache: TTLCache[str, Profile] = TTLCache(maxsize=maxsize, ttl=ttl)

    def get(self, public_id: str) -> Profile | None:
        return self._cache.get(public_id)

    def set(self, public_id: str, profile: Profile) -> None:
        self._cache[public_id] = profile
