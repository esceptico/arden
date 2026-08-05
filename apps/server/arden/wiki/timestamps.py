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
            cached = self._by_head.get(head, {})
            missing = page_ids - cached.keys()
            if not missing:
                self._by_head.move_to_end(head)
                return {page_id: cached.get(page_id, (None, None)) for page_id in page_ids}

        derived = self._derive(head, missing)
        with self._lock:
            cached = self._by_head.setdefault(head, {})
            cached.update(derived)
            self._by_head.move_to_end(head)
            while len(self._by_head) > self._max_heads:
                self._by_head.popitem(last=False)
            return {page_id: cached.get(page_id, (None, None)) for page_id in page_ids}

    def _derive(self, head: str, page_ids: set[str]) -> dict[str, PageTimestamps]:
        mutable: dict[str, list[datetime | None]] = {page_id: [None, None] for page_id in page_ids}
        for commit in self._repository.history(start=head):
            for change in commit.changes:
                version = change.after or change.before
                if version is None or version.resource_id not in mutable:
                    continue
                times = mutable[version.resource_id]
                if times[1] is None:
                    times[1] = commit.timestamp
                if change.action == "create":
                    times[0] = commit.timestamp
            if all(created is not None and updated is not None for created, updated in mutable.values()):
                break
        return {page_id: (times[0], times[1]) for page_id, times in mutable.items()}
