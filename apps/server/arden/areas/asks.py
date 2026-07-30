import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from arden.areas.models import Ask, AskState

_KIND_PRIORITY = {"question": 0, "review": 1, "notify": 2}


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


class AskStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._asks: dict[str, Ask] = {}
        if path.exists():
            data = json.loads(path.read_text())
            self._asks = {a["id"]: Ask(**a) for a in data["asks"]}

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"asks": [asdict(a) for a in self._asks.values()]}, indent=2))

    def upsert(self, ask: Ask) -> None:
        self._asks[ask.id] = ask
        self._flush()

    def resolve(
        self,
        ask_id: str,
        state: AskState,
        snoozed_until: str | None = None,
        resolution: str | None = None,
    ) -> Ask:
        if ask_id not in self._asks:
            raise KeyError(f"unknown ask '{ask_id}'; valid: {list(self._asks)}")
        ask = self._asks[ask_id]
        ask.state = state
        ask.snoozed_until = snoozed_until
        if state in {"done", "dismissed"}:
            ask.resolution = resolution or state
            ask.resolved_at = datetime.now(UTC).isoformat()
        else:
            # A snooze or explicit reopen returns the ask to its unresolved
            # lifecycle. Do not leave an old decision receipt on a live ask.
            ask.resolution = None
            ask.resolved_at = None
        self._flush()
        return ask

    def get(self, ask_id: str) -> Ask | None:
        return self._asks.get(ask_id)

    def upsert_agent_nomination(self, ask: Ask) -> bool:
        """Create or refresh one stable nomination without re-nagging.

        A user decision (resolution set) is durable — re-nominations of that
        key stay silent. A quiet TTL expiry is not a decision: the user never
        saw it, so a fresh nomination surfaces (and notifies) as new.

        Returns True only when the ask should notify.
        """
        existing = self._asks.get(ask.id)
        if existing is None or (existing.state != "active" and existing.resolution is None):
            self._asks[ask.id] = ask
            self._flush()
            return True
        if existing.state != "active":
            return False
        ask.created_at = existing.created_at
        self._asks[ask.id] = ask
        self._flush()
        return False

    def reconcile(self, source: str, desired: list[Ask]) -> None:
        """Make one mechanically-derived source match canonical runtime state.

        Resolved asks stay resolved while the same condition persists; active
        asks disappear when their underlying approval/failure disappears.
        """
        desired_by_id = {ask.id: ask for ask in desired}
        changed = False
        for ask in self._asks.values():
            if ask.source == source and ask.state in ("active", "snoozed") and ask.id not in desired_by_id:
                ask.state = "done"
                changed = True
        for ask_id, desired_ask in desired_by_id.items():
            existing = self._asks.get(ask_id)
            if existing is None:
                self._asks[ask_id] = desired_ask
                changed = True
            elif existing.state == "active" and existing != desired_ask:
                desired_ask.state = existing.state
                desired_ask.created_at = existing.created_at
                self._asks[ask_id] = desired_ask
                changed = True
        if changed:
            self._flush()

    def list(self, area_key: str | None = None, include_resolved: bool = False) -> list[Ask]:
        now = datetime.now(UTC)
        self._expired = False
        out = []
        for a in self._asks.values():
            if area_key and a.area_key != area_key:
                continue
            if a.state == "active" and a.expires_at is not None and _parse(a.expires_at) <= now:
                a.state = "dismissed"  # quiet expiry; lazily persisted below
                self._expired = True
            active = a.state == "active" or (
                a.state == "snoozed" and a.snoozed_until is not None and _parse(a.snoozed_until) <= now
            )
            if include_resolved or active:
                out.append(a)
        if self._expired:
            self._flush()
        return sorted(out, key=lambda a: a.created_at, reverse=True)


def _is_better(a: Ask, cur: Ask) -> bool:
    """a outranks cur: lower kind priority wins; ties prefer the newer created_at."""
    a_pri, cur_pri = _KIND_PRIORITY[a.kind], _KIND_PRIORITY[cur.kind]
    if a_pri != cur_pri:
        return a_pri < cur_pri
    return a.created_at > cur.created_at


def nominate_focus(asks: list[Ask], cap: int = 4) -> list[Ask]:
    best: dict[str, Ask] = {}
    for a in asks:
        cur = best.get(a.area_key)
        if cur is None or _is_better(a, cur):
            best[a.area_key] = a
    # stable sort ascending by created_at, then reverse-stable by priority, yields
    # priority asc / created_at desc without a mixed-direction tuple key.
    ranked = sorted(best.values(), key=lambda a: a.created_at, reverse=True)
    ranked.sort(key=lambda a: _KIND_PRIORITY[a.kind])
    return ranked[:cap]
