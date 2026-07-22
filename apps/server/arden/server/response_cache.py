from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any


@dataclass
class CachedResponse:
    expires_at: float
    payload: dict[str, Any]


class AsyncResponseCache:
    def __init__(self, *, ttl_seconds: float, max_entries: int = 64) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max(1, max_entries)
        self._items: dict[tuple[object, ...], CachedResponse] = {}

    async def get_or_load(
        self,
        *,
        key: tuple[object, ...],
        refresh: bool,
        loader: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        now = monotonic()
        if not refresh:
            cached = self._items.get(key)
            if cached is not None and cached.expires_at > now:
                return self._with_cache_metadata(cached.payload, hit=True)

        payload = await loader()
        self._prune(now=monotonic())
        if len(self._items) >= self.max_entries:
            oldest_key = min(self._items, key=lambda item_key: self._items[item_key].expires_at)
            self._items.pop(oldest_key, None)
        self._items[key] = CachedResponse(expires_at=monotonic() + self.ttl_seconds, payload=dict(payload))
        return self._with_cache_metadata(payload, hit=False)

    def invalidate(self, *, prefix: str, scope: object) -> None:
        stale_keys = [key for key in self._items if len(key) >= 2 and key[0] == prefix and key[1] == scope]
        for key in stale_keys:
            self._items.pop(key, None)

    def clear_key(self, key: tuple[object, ...]) -> None:
        self._items.pop(key, None)

    def _prune(self, *, now: float) -> None:
        expired_keys = [key for key, item in self._items.items() if item.expires_at <= now]
        for key in expired_keys:
            self._items.pop(key, None)

    def _with_cache_metadata(self, payload: dict[str, Any], *, hit: bool) -> dict[str, Any]:
        response = dict(payload)
        response["cache"] = {"hit": hit, "ttl_seconds": self.ttl_seconds}
        return response
