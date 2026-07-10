from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from ntrp.areas.asks import AskStore, nominate_focus
from ntrp.areas.models import Ask


def ask(id: str, area_key: str, kind: str, created: str = "2026-07-06T10:00:00", source: str = "open_loop") -> Ask:
    return Ask(id=id, area_key=area_key, text=id, kind=kind, source=source,
               actions=[], state="active", created_at=created)


def test_store_upsert_resolve_roundtrip(tmp_path: Path):
    store = AskStore(tmp_path / "state.json")
    store.upsert(ask("a1", "o-1a", "review"))
    store.resolve("a1", "dismissed", resolution="rejected")
    assert store.list("o-1a") == []
    assert store.list("o-1a", include_resolved=True)[0].state == "dismissed"
    assert store.list("o-1a", include_resolved=True)[0].resolution == "rejected"
    assert store.list("o-1a", include_resolved=True)[0].resolved_at is not None


def test_snoozed_asks_hidden_until_deadline(tmp_path: Path):
    store = AskStore(tmp_path / "state.json")
    store.upsert(ask("a1", "o-1a", "review"))
    store.resolve("a1", "snoozed", snoozed_until="2099-01-01T00:00:00")
    assert store.list("o-1a") == []
    store.resolve("a1", "snoozed", snoozed_until="2000-01-01T00:00:00")
    assert [a.id for a in store.list("o-1a")] == ["a1"]  # snooze expired → active again


def test_nominate_focus_one_per_area_kind_priority():
    asks = [
        ask("r", "dex", "notify"), ask("d", "dex", "question"),
        ask("x", "aside", "review"), ask("y", "health", "notify"),
    ]
    focus = nominate_focus(asks, cap=2)
    assert [a.id for a in focus] == ["d", "x"]  # question beats notify; review beats notify; cap 2


def test_nominate_focus_same_kind_prefers_newer():
    asks = [
        ask("old", "dex", "review", created="2026-07-01T10:00:00"),
        ask("new", "dex", "review", created="2026-07-06T10:00:00"),
    ]
    assert [a.id for a in nominate_focus(asks)] == ["new"]


def test_snooze_comparison_handles_aware_and_naive(tmp_path: Path):
    store = AskStore(tmp_path / "state.json")
    store.upsert(ask("a1", "o-1a", "review"))
    store.upsert(ask("a2", "o-1a", "review"))

    # snoozed_until carries a +05:00 offset; the wall-clock digits read as "later today" but
    # the actual UTC instant is already in the past. Lexicographic string comparison against
    # now.isoformat() (which is UTC, "+00:00") gets this backwards; real datetime math doesn't.
    now = datetime.now(UTC)
    past_utc_with_positive_offset = (now - timedelta(hours=1)).astimezone(
        timezone(timedelta(hours=5))
    ).isoformat()
    store.resolve("a1", "snoozed", snoozed_until=past_utc_with_positive_offset)

    # naive snooze in the past (treated as UTC) must re-admit too
    store.resolve("a2", "snoozed", snoozed_until="2000-01-01T00:00:00")

    assert sorted(a.id for a in store.list("o-1a")) == ["a1", "a2"]


def test_retire_active_agent_asks_marks_only_active_source_agent_asks_done(tmp_path: Path):
    store = AskStore(tmp_path / "state.json")
    store.upsert(ask("agent-1", "o-1a", "review", source="agent"))
    store.upsert(ask("approval-1", "o-1a", "review", source="approval"))
    store.upsert(ask("agent-2", "dex", "review", source="agent"))

    store.retire_active_agent_asks("o-1a")

    all_asks = {a.id: a for a in store.list(include_resolved=True)}
    assert all_asks["agent-1"].state == "done"
    assert all_asks["approval-1"].state == "active"  # not source=="agent" — untouched
    assert all_asks["agent-2"].state == "active"  # different area — untouched


def test_retire_active_agent_asks_leaves_already_resolved_agent_asks_alone(tmp_path: Path):
    store = AskStore(tmp_path / "state.json")
    store.upsert(ask("agent-1", "o-1a", "review", source="agent"))
    store.resolve("agent-1", "dismissed")

    store.retire_active_agent_asks("o-1a")

    assert store.list("o-1a", include_resolved=True)[0].state == "dismissed"  # not clobbered to "done"
