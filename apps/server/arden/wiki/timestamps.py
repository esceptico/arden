"""Head-pinned page timestamps derived from immutable wiki history."""

from collections import OrderedDict
from datetime import datetime
from threading import Lock

from arden.revisions.repository import ManagedFileRepository

PageTimestamps = tuple[datetime | None, datetime | None]


class WikiPageTimestampCache:
    """Bounded derived index; one complete timestamp map per wiki head."""

    def __init__(self, repository: ManagedFileRepository, *, max_heads: int = 4) -> None:
        if max_heads < 1:
            raise ValueError("max_heads must be positive")
        self._repository = repository
        self._max_heads = max_heads
        self._by_head: OrderedDict[str, dict[str, PageTimestamps]] = OrderedDict()
        self._lock = Lock()

    def for_pages(self, head: str | None, page_ids: set[str]) -> dict[str, PageTimestamps]:
        if head is None or not page_ids:
            return dict.fromkeys(page_ids, (None, None))

        with self._lock:
            cached = self._by_head.get(head)
            if cached is None:
                # Derivation stays under the same lock so concurrent requests
                # for one uncached head cannot repeat the commit walk.
                cached = self._derive(head)
                self._by_head[head] = cached
            self._by_head.move_to_end(head)
            while len(self._by_head) > self._max_heads:
                self._by_head.popitem(last=False)
            return {page_id: cached.get(page_id, (None, None)) for page_id in page_ids}

    def _derive(self, head: str) -> dict[str, PageTimestamps]:
        mutable: dict[str, list[datetime | None]] = {}
        cursor: str | None = head
        while cursor is not None:
            commit = self._repository.inspect_commit(cursor)
            for change in commit.changes:
                version = change.after or change.before
                if version is None:
                    continue
                times = mutable.setdefault(version.resource_id, [None, None])
                if times[1] is None:
                    times[1] = commit.timestamp
                if change.action == "create":
                    times[0] = commit.timestamp
            cursor = commit.parent_id
        return {page_id: (times[0], times[1]) for page_id, times in mutable.items()}
