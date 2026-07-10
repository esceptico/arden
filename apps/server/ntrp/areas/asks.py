import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from ntrp.areas.models import Ask, AskState

_KIND_PRIORITY = {"decide": 0, "drift": 1, "review": 2, "act": 3}


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

    def resolve(self, ask_id: str, state: AskState, snoozed_until: str | None = None) -> Ask:
        if ask_id not in self._asks:
            raise KeyError(f"unknown ask '{ask_id}'; valid: {list(self._asks)}")
        ask = self._asks[ask_id]
        ask.state = state
        ask.snoozed_until = snoozed_until
        self._flush()
        return ask

    def retire_active_agent_asks(self, area_key: str) -> None:
        """Mark this area's active source=="agent" asks "done" — called
        before upserting a fresh nomination so a new run's ask supersedes,
        rather than piles on top of, its predecessor."""
        changed = False
        for ask in self._asks.values():
            if ask.area_key == area_key and ask.source == "agent" and ask.state == "active":
                ask.state = "done"
                changed = True
        if changed:
            self._flush()

    def list(self, area_key: str | None = None, include_resolved: bool = False) -> list[Ask]:
        now = datetime.now(UTC)
        out = []
        for a in self._asks.values():
            if area_key and a.area_key != area_key:
                continue
            active = a.state == "active" or (
                a.state == "snoozed" and a.snoozed_until is not None and _parse(a.snoozed_until) <= now
            )
            if include_resolved or active:
                out.append(a)
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
