"""Custodian mechanics: self-paced heartbeat clamping + quiet decay, event
coalescing with budget/pause gating, and the ignored-asks attention signal."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from ntrp.areas.custodian import EVENT_WAKE_DEBOUNCE_MINUTES, CustodianStore
from ntrp.constants import AREA_ATTENTION_PRESETS

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> CustodianStore:
    return CustodianStore(tmp_path / "agent-state.json")


def _nom(asks, hours, reason="r", report="did things", **extra):
    return {
        "asks": asks,
        "report": report,
        "next_check_hours": hours,
        "next_check_reason": reason,
        **extra,
    }


def test_next_check_clamped_to_attention_bounds(tmp_path):
    c = _store(tmp_path)
    active = AREA_ATTENTION_PRESETS["active"]
    # Too eager → floor.
    nxt = c.record_run("a1", _nom([{"k": 1}], hours=0.1), attention="active", now=NOW)
    assert nxt == NOW + timedelta(hours=active["min_hours"])
    # Too lazy → ceiling.
    nxt = c.record_run("a1", _nom([{"k": 1}], hours=10_000), attention="active", now=NOW)
    assert nxt == NOW + timedelta(hours=active["max_hours"])


def test_missing_nomination_falls_back_to_ceiling_never_a_tight_loop(tmp_path):
    c = _store(tmp_path)
    nxt = c.record_run("a1", None, attention="ambient", now=NOW)
    assert nxt == NOW + timedelta(hours=AREA_ATTENTION_PRESETS["ambient"]["max_hours"])


def test_quiet_streak_decays_cadence_and_activity_resets_it(tmp_path):
    c = _store(tmp_path)
    # 1st quiet run: agent's choice honored (24h within ambient bounds).
    first = c.record_run("a1", _nom([], hours=24), attention="ambient", now=NOW)
    assert first == NOW + timedelta(hours=24)
    # 2nd consecutive quiet run: stretched ×1.5.
    second = c.record_run("a1", _nom([], hours=24), attention="ambient", now=NOW)
    assert second == NOW + timedelta(hours=36)
    # Activity (an ask) resets the streak; choice honored again.
    third = c.record_run("a1", _nom([{"k": 1}], hours=24), attention="ambient", now=NOW)
    assert third == NOW + timedelta(hours=24)
    assert c.state("a1")["last_report"] == "did things"
    assert c.state("a1")["next_check_reason"] == "r"


def test_material_progress_resets_decay_without_an_ask(tmp_path):
    c = _store(tmp_path)
    c.record_run("a1", _nom([], hours=24), attention="ambient", now=NOW)
    c.record_run("a1", _nom([], hours=24), attention="ambient", now=NOW)

    nxt = c.record_run(
        "a1",
        _nom([], hours=24, made_progress=True, work_remaining=True),
        attention="ambient",
        now=NOW,
    )

    assert nxt == NOW + timedelta(hours=24)
    assert c.state("a1")["quiet_streak"] == 0


def test_active_work_can_request_short_bounded_continuation(tmp_path):
    c = _store(tmp_path)
    c.begin_run("a1", attention="ambient", manual=False, now=NOW)

    nxt = c.record_run(
        "a1",
        _nom(
            [],
            hours=24,
            made_progress=True,
            work_remaining=True,
            continuation_minutes=5,
            continuation_reason="finish comparing labs",
        ),
        attention="ambient",
        now=NOW,
    )

    assert nxt == NOW + timedelta(minutes=5)
    assert c.state("a1")["next_check_reason"] == "finish comparing labs"


def test_continuation_falls_back_when_daily_cap_is_spent(tmp_path):
    c = _store(tmp_path)
    for _ in range(AREA_ATTENTION_PRESETS["ambient"]["runs_per_day"]):
        c.begin_run("a1", attention="ambient", manual=False, now=NOW)

    nxt = c.record_run(
        "a1",
        _nom([], hours=24, made_progress=True, work_remaining=True, continuation_minutes=5),
        attention="ambient",
        now=NOW,
    )

    assert nxt == NOW + timedelta(hours=24)


def test_paused_area_never_schedules_short_continuation(tmp_path):
    c = _store(tmp_path)
    nxt = c.record_run(
        "a1",
        _nom([], hours=24, made_progress=True, work_remaining=True, continuation_minutes=5),
        attention="ambient",
        paused=True,
        now=NOW,
    )

    assert nxt == NOW + timedelta(hours=24)


def test_events_coalesce_and_respect_budget_and_pause(tmp_path):
    c = _store(tmp_path)
    deadline = c.note_event("a1", "chat filed", attention="ambient", paused=False, now=NOW)
    assert deadline == NOW + timedelta(minutes=EVENT_WAKE_DEBOUNCE_MINUTES)
    # Duplicate events dedupe; pending list carries into the next run.
    c.note_event("a1", "chat filed", attention="ambient", paused=False, now=NOW)
    c.note_event("a1", "page edited", attention="ambient", paused=False, now=NOW)
    assert c.state("a1")["pending_events"] == ["chat filed", "page edited"]
    woken = c.consume_pending("a1", now=NOW)
    assert woken == ["chat filed", "page edited"]
    assert c.state("a1")["pending_events"] == []
    # Paused: noted, never wakes.
    assert c.note_event("a1", "x", attention="ambient", paused=True, now=NOW) is None
    # Budget: ambient allows 3 runs/day; burn them, then events stop waking.
    c.consume_pending("a1", now=NOW)
    c.consume_pending("a1", now=NOW)
    assert c.runs_today("a1", now=NOW) == 3
    assert c.note_event("a1", "y", attention="ambient", paused=False, now=NOW) is None
    # ...but the event is still noted for the heartbeat run.
    assert "y" in c.state("a1")["pending_events"]
    # A new day resets the budget.
    tomorrow = NOW + timedelta(days=1)
    assert c.note_event("a1", "z", attention="ambient", paused=False, now=tomorrow) is not None


def test_ignored_asks_three_strikes_steps_attention_down(tmp_path):
    c = _store(tmp_path)
    assert c.note_ignored_asks("a1", True) is False
    assert c.note_ignored_asks("a1", True) is False
    assert c.note_ignored_asks("a1", True) is True  # 3rd strike
    assert c.state("a1")["unanswered_streak"] == 0  # reset after the signal
    c.note_ignored_asks("a1", True)
    assert c.note_ignored_asks("a1", False) is False  # answering resets
    assert c.state("a1")["unanswered_streak"] == 0


def test_state_persists_across_reload(tmp_path):
    c = _store(tmp_path)
    c.note_event("a1", "chat filed", attention="active", paused=False, now=NOW)
    reloaded = CustodianStore(tmp_path / "agent-state.json")
    assert reloaded.state("a1")["pending_events"] == ["chat filed"]


def test_all_autonomous_runs_share_cap_while_manual_runs_bypass(tmp_path):
    c = _store(tmp_path)
    cap = AREA_ATTENTION_PRESETS["ambient"]["runs_per_day"]

    for _ in range(cap):
        allowed, _ = c.begin_run("a1", attention="ambient", manual=False, now=NOW)
        assert allowed

    allowed, _ = c.begin_run("a1", attention="ambient", manual=False, now=NOW)
    assert not allowed
    manual, _ = c.begin_run("a1", attention="ambient", manual=True, now=NOW)
    assert manual
    assert c.runs_today("a1", now=NOW) == cap


def test_exact_page_write_digest_is_consumed_once(tmp_path):
    c = _store(tmp_path)
    c.record_page_write("a1", "digest-1")

    assert not c.consume_self_write("a1", "different")
    assert c.consume_self_write("a1", "digest-1")
    assert not c.consume_self_write("a1", "digest-1")


def test_partial_legacy_state_recovers_without_blocking_startup(tmp_path):
    path = tmp_path / "agent-state.json"
    path.write_text('{"a1":')

    recovered = CustodianStore(path)

    assert recovered.state("a1")["runs_today"] == 0
