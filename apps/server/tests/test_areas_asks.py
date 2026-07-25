from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from arden.areas.asks import AskStore, nominate_focus
from arden.areas.models import Ask


def ask(
    id: str, area_key: str | None, kind: str, created: str = "2026-07-06T10:00:00", source: str = "open_loop"
) -> Ask:
    return Ask(
        id=id, area_key=area_key, text=id, kind=kind, source=source, actions=[], state="active", created_at=created
    )


def test_store_upsert_resolve_roundtrip(tmp_path: Path):
    store = AskStore(tmp_path / "state.json")
    store.upsert(ask("a1", "o-1a", "review"))
    store.resolve("a1", "dismissed", resolution="rejected")
    assert store.list("o-1a") == []
    assert store.list("o-1a", include_resolved=True)[0].state == "dismissed"
    assert store.list("o-1a", include_resolved=True)[0].resolution == "rejected"
    assert store.list("o-1a", include_resolved=True)[0].resolved_at is not None


def test_store_reopen_restores_an_active_ask_without_a_stale_resolution(tmp_path: Path):
    store = AskStore(tmp_path / "state.json")
    store.upsert(ask("a1", "o-1a", "review"))
    store.resolve("a1", "dismissed", resolution="rejected")

    reopened = store.resolve("a1", "active")

    assert reopened.state == "active"
    assert reopened.snoozed_until is None
    assert reopened.resolution is None
    assert reopened.resolved_at is None
    assert [row.id for row in store.list("o-1a")] == ["a1"]


def test_snoozed_asks_hidden_until_deadline(tmp_path: Path):
    store = AskStore(tmp_path / "state.json")
    store.upsert(ask("a1", "o-1a", "review"))
    store.resolve("a1", "snoozed", snoozed_until="2099-01-01T00:00:00")
    assert store.list("o-1a") == []
    store.resolve("a1", "snoozed", snoozed_until="2000-01-01T00:00:00")
    assert [a.id for a in store.list("o-1a")] == ["a1"]  # snooze expired → active again


def test_nominate_focus_one_per_area_kind_priority():
    asks = [
        ask("r", "dex", "notify"),
        ask("d", "dex", "question"),
        ask("x", "aside", "review"),
        ask("y", "health", "notify"),
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
    past_utc_with_positive_offset = (now - timedelta(hours=1)).astimezone(timezone(timedelta(hours=5))).isoformat()
    store.resolve("a1", "snoozed", snoozed_until=past_utc_with_positive_offset)

    # naive snooze in the past (treated as UTC) must re-admit too
    store.resolve("a2", "snoozed", snoozed_until="2000-01-01T00:00:00")

    assert sorted(a.id for a in store.list("o-1a")) == ["a1", "a2"]


def test_renomination_after_quiet_expiry_surfaces_again(tmp_path: Path):
    store = AskStore(tmp_path / "state.json")
    first = ask("agent:o-1a:refill", "o-1a", "notify", source="agent")
    first.expires_at = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    assert store.upsert_agent_nomination(first) is True
    assert store.list("o-1a") == []  # lazy expiry flipped it to dismissed

    again = ask("agent:o-1a:refill", "o-1a", "notify", source="agent")
    assert store.upsert_agent_nomination(again) is True  # never user-resolved → new surface
    assert [a.id for a in store.list("o-1a")] == ["agent:o-1a:refill"]


def test_renomination_after_user_resolution_stays_silent(tmp_path: Path):
    store = AskStore(tmp_path / "state.json")
    assert store.upsert_agent_nomination(ask("agent:o-1a:dose", "o-1a", "question", source="agent")) is True
    store.resolve("agent:o-1a:dose", "dismissed", resolution="rejected")

    again = ask("agent:o-1a:dose", "o-1a", "question", source="agent")
    assert store.upsert_agent_nomination(again) is False  # user decided — don't re-nag
    assert store.list("o-1a") == []


def test_get_finds_snoozed_ask(tmp_path: Path):
    store = AskStore(tmp_path / "state.json")
    store.upsert(ask("a1", "o-1a", "review"))
    store.resolve("a1", "snoozed", snoozed_until="2099-01-01T00:00:00")
    assert store.get("a1") is not None  # list() hides it; direct lookup must not


def test_reconcile_retires_snoozed_mechanical_asks(tmp_path: Path):
    store = AskStore(tmp_path / "state.json")
    store.upsert(ask("approval-1", "o-1a", "review", source="approval"))
    store.resolve("approval-1", "snoozed", snoozed_until="2099-01-01T00:00:00")

    store.reconcile("approval", [])  # underlying approval disappeared

    assert store.list("o-1a", include_resolved=True)[0].state == "done"  # no ghost after the snooze


def test_nominate_focus_gives_unfiled_asks_their_own_lane():
    """`None` is a dict key like any other: unfiled asks compete among
    themselves, not against an area's."""
    unfiled_new = ask("u2", None, "notify", created="2026-07-06T12:00:00")
    ranked = nominate_focus(
        [
            ask("a1", "o-1a", "question"),
            ask("u1", None, "notify"),
            unfiled_new,
        ]
    )

    assert [a.id for a in ranked] == ["a1", "u2"]
