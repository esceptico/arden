"""Custodian mechanics: self-paced heartbeat clamping + quiet decay, event
coalescing with budget/pause gating, and the ignored-asks attention signal."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from arden.areas.agent import AreaCustodianReport
from arden.areas.custodian import EVENT_WAKE_DEBOUNCE_MINUTES, CustodianStore
from arden.constants import AREA_ATTENTION_PRESETS

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> CustodianStore:
    return CustodianStore(tmp_path / "agent-state.json")


def _nom(asks, hours, reason="r", report="did things", made_progress=False, **extra):
    # Progress is derived from applied operations, so a test that means
    # "this run moved the work graph" must carry a real work change.
    if made_progress:
        extra.setdefault(
            "work_changes",
            [{"op": "create", "key": "moved", "kind": "action", "text": "moved something", "owner": "custodian"}],
        )
    asks = (
        [
            {
                "key": "active",
                "text": "Needs attention",
                "kind": "question",
                "salience": 4,
                "why_now": "Work is active",
                "what_next": "Continue after the answer",
            }
        ]
        if asks
        else []
    )
    return AreaCustodianReport.model_validate(
        {
            "asks": asks,
            "report": report,
            "next_check_hours": hours,
            "next_check_reason": reason,
            "work_remaining": False,
            **extra,
        }
    )


def test_progress_is_read_from_applied_operations_not_claimed(tmp_path):
    """Self-reported progress let a run rewrite page prose, reset the quiet
    streak and dodge decay while emitting no asks."""
    prose_only = _nom([], hours=24, evidence=[{"target_type": "area", "event_type": "updated", "summary": "tidied"}])
    assert prose_only.made_progress is False

    moved = _nom([], hours=24, made_progress=True)
    assert moved.made_progress is True

    c = _store(tmp_path)
    c.record_run("a1", prose_only, run_ref="run:1", attention="ambient", now=NOW)
    c.record_run("a1", prose_only, run_ref="run:2", attention="ambient", now=NOW)
    assert c.quiet_streak("a1") == 2


def test_next_check_clamped_to_attention_bounds(tmp_path):
    c = _store(tmp_path)
    active = AREA_ATTENTION_PRESETS["active"]
    # Too eager → floor.
    nxt = c.record_run("a1", _nom([{"k": 1}], hours=0.1), run_ref="run:1", attention="active", now=NOW)
    assert nxt == NOW + timedelta(hours=active["min_hours"])
    # Too lazy → ceiling.
    nxt = c.record_run("a1", _nom([{"k": 1}], hours=336), run_ref="run:2", attention="active", now=NOW)
    assert nxt == NOW + timedelta(hours=active["max_hours"])


def test_intake_is_only_needed_before_first_successful_report(tmp_path):
    c = _store(tmp_path)

    assert c.needs_intake("a1")
    c.record_run("a1", _nom([], hours=24), run_ref="run:1", attention="ambient", now=NOW)
    assert not c.needs_intake("a1")


def test_quiet_streak_decays_cadence_and_activity_resets_it(tmp_path):
    c = _store(tmp_path)
    # 1st quiet run: agent's choice honored (24h within ambient bounds).
    first = c.record_run("a1", _nom([], hours=24), run_ref="run:1", attention="ambient", now=NOW)
    assert first == NOW + timedelta(hours=24)
    # 2nd consecutive quiet run: stretched ×1.5.
    second = c.record_run("a1", _nom([], hours=24), run_ref="run:2", attention="ambient", now=NOW)
    assert second == NOW + timedelta(hours=36)
    # Activity (an ask) resets the streak; choice honored again.
    third = c.record_run("a1", _nom([{"k": 1}], hours=24), run_ref="run:3", attention="ambient", now=NOW)
    assert third == NOW + timedelta(hours=24)
    assert c.state("a1")["last_report"] == "did things"
    assert c.state("a1")["next_check_reason"] == "r"


def test_report_projection_replay_is_idempotent(tmp_path):
    c = _store(tmp_path)
    report = _nom([], hours=24)

    first = c.record_run(
        "a1",
        report,
        run_ref="run:1",
        attention="ambient",
        now=NOW,
    )
    replay = c.record_run(
        "a1",
        report,
        run_ref="run:1",
        attention="ambient",
        now=NOW + timedelta(hours=1),
    )

    assert replay == first
    assert c.state("a1")["quiet_streak"] == 1


def test_material_progress_resets_decay_without_an_ask(tmp_path):
    c = _store(tmp_path)
    c.record_run("a1", _nom([], hours=24), run_ref="run:1", attention="ambient", now=NOW)
    c.record_run("a1", _nom([], hours=24), run_ref="run:2", attention="ambient", now=NOW)

    nxt = c.record_run(
        "a1",
        _nom([], hours=24, made_progress=True, work_remaining=True),
        run_ref="run:3",
        attention="ambient",
        now=NOW,
    )

    assert nxt == NOW + timedelta(hours=24)
    assert c.state("a1")["quiet_streak"] == 0


def test_active_work_can_request_short_bounded_continuation(tmp_path):
    c = _store(tmp_path)
    c.begin_or_resume_delivery(
        "a1",
        iteration=1,
        client_id="loop:area:a1:1",
        attention="ambient",
        manual=False,
        skip_approvals=True,
        build_message=lambda _: "run",
        now=NOW,
    )

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
        run_ref="run:1",
        attention="ambient",
        now=NOW,
    )

    assert nxt == NOW + timedelta(minutes=5)
    assert c.state("a1")["next_check_reason"] == "finish comparing labs"
    assert "pending_delivery" not in c.state("a1")


def test_continuation_falls_back_when_daily_cap_is_spent(tmp_path):
    c = _store(tmp_path)
    for iteration in range(1, AREA_ATTENTION_PRESETS["ambient"]["runs_per_day"] + 1):
        c.begin_or_resume_delivery(
            "a1",
            iteration=iteration,
            client_id=f"loop:area:a1:{iteration}",
            attention="ambient",
            manual=False,
            skip_approvals=True,
            build_message=lambda _: "run",
            now=NOW,
        )

    nxt = c.record_run(
        "a1",
        _nom([], hours=24, made_progress=True, work_remaining=True, continuation_minutes=5),
        run_ref="run:1",
        attention="ambient",
        now=NOW,
    )

    assert nxt == NOW + timedelta(hours=24)


def test_paused_area_never_schedules_short_continuation(tmp_path):
    c = _store(tmp_path)
    nxt = c.record_run(
        "a1",
        _nom([], hours=24, made_progress=True, work_remaining=True, continuation_minutes=5),
        run_ref="run:1",
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
    allowed, delivery = c.begin_or_resume_delivery(
        "a1",
        iteration=1,
        client_id="loop:area:a1:1",
        attention="ambient",
        manual=False,
        skip_approvals=True,
        build_message=lambda events: "\n".join(events),
        now=NOW,
    )
    assert allowed and delivery is not None
    assert delivery.message == "chat filed\npage edited"
    assert c.state("a1")["pending_events"] == []
    # Paused: noted, never wakes.
    assert c.note_event("a1", "x", attention="ambient", paused=True, now=NOW) is None
    # Budget: ambient allows 3 runs/day; burn them, then events stop waking.
    for iteration in (2, 3):
        allowed, _ = c.begin_or_resume_delivery(
            "a1",
            iteration=iteration,
            client_id=f"loop:area:a1:{iteration}",
            attention="ambient",
            manual=False,
            skip_approvals=True,
            build_message=lambda _: "run",
            now=NOW,
        )
        assert allowed
    assert c.runs_today("a1", now=NOW) == 3
    assert c.note_event("a1", "y", attention="ambient", paused=False, now=NOW) is None
    # ...but the event is still noted for the heartbeat run.
    assert "y" in c.state("a1")["pending_events"]
    # A new day resets the budget.
    tomorrow = NOW + timedelta(days=1)
    assert c.note_event("a1", "z", attention="ambient", paused=False, now=tomorrow) is not None


def test_ignored_asks_three_strikes_steps_attention_down(tmp_path):
    c = _store(tmp_path)
    assert c.note_ignored_asks("a1", True, attention="active", run_ref="run:1") is None
    assert c.note_ignored_asks("a1", True, attention="active", run_ref="run:2") is None
    assert c.note_ignored_asks("a1", True, attention="active", run_ref="run:3") == "ambient"
    assert c.state("a1")["unanswered_streak"] == 0  # reset after the signal
    c.note_ignored_asks("a1", True, attention="ambient", run_ref="run:4")
    assert c.note_ignored_asks("a1", False, attention="ambient", run_ref="run:5") is None
    assert c.state("a1")["unanswered_streak"] == 0


def test_state_persists_across_reload(tmp_path):
    c = _store(tmp_path)
    c.note_event("a1", "chat filed", attention="active", paused=False, now=NOW)
    reloaded = CustodianStore(tmp_path / "agent-state.json")
    assert reloaded.state("a1")["pending_events"] == ["chat filed"]


def test_delivery_retries_exact_request_after_restart_without_consuming_new_wakes(tmp_path):
    c = _store(tmp_path)
    c.note_event("a1", "first wake", attention="ambient", paused=False, now=NOW)
    rendered: list[tuple[str, ...]] = []

    def render(woken_by: tuple[str, ...]) -> str:
        rendered.append(woken_by)
        return "run with: " + ", ".join(woken_by)

    allowed, first = c.begin_or_resume_delivery(
        "a1",
        iteration=1,
        client_id="loop:area:a1:1",
        attention="ambient",
        manual=False,
        skip_approvals=True,
        build_message=render,
        now=NOW,
    )
    assert allowed and first is not None
    assert rendered == [("first wake",)]

    restarted = _store(tmp_path)
    restarted.note_event("a1", "later wake", attention="ambient", paused=False, now=NOW)

    def must_not_render(_: tuple[str, ...]) -> str:
        raise AssertionError("retry must reuse the reserved message")

    allowed, retry = restarted.begin_or_resume_delivery(
        "a1",
        iteration=1,
        client_id="loop:area:a1:1:attempt-2",
        attention="ambient",
        manual=False,
        skip_approvals=True,
        build_message=must_not_render,
        now=NOW,
    )
    assert allowed and retry is not None
    assert retry.client_id == "loop:area:a1:1:attempt-2"
    assert retry.message == first.message
    assert retry.skip_approvals == first.skip_approvals
    assert restarted.runs_today("a1", now=NOW) == 1
    assert restarted.state("a1")["pending_events"] == ["later wake"]

    allowed, second = restarted.begin_or_resume_delivery(
        "a1",
        iteration=2,
        client_id="loop:area:a1:2",
        attention="ambient",
        manual=False,
        skip_approvals=True,
        build_message=render,
        now=NOW,
    )
    assert allowed and second is not None
    assert second.message == "run with: later wake"


def test_failed_delivery_refunds_budget_and_restores_wakes(tmp_path):
    c = _store(tmp_path)
    c.note_event("a1", "first wake", attention="ambient", paused=False, now=NOW)
    allowed, delivery = c.begin_or_resume_delivery(
        "a1",
        iteration=1,
        client_id="loop:area:a1:1",
        attention="ambient",
        manual=False,
        skip_approvals=True,
        build_message=lambda events: ", ".join(events),
        now=NOW,
    )
    assert allowed and delivery is not None
    c.note_event("a1", "later wake", attention="ambient", paused=False, now=NOW)
    assert c.runs_today("a1", now=NOW) == 1

    assert c.abandon_delivery("a1", client_id=delivery.client_id)
    assert c.runs_today("a1", now=NOW) == 0
    assert c.state("a1")["pending_events"] == ["first wake", "later wake"]
    assert "pending_delivery" not in c.state("a1")
    assert not c.abandon_delivery("a1", client_id=delivery.client_id)


def test_rollback_across_midnight_still_releases_the_delivery(tmp_path):
    """The refund is only meaningful while the counter belongs to the day the
    delivery was reserved on. Raising past midnight aborted the release of
    pending_delivery from inside the except-block meant to guarantee it, which
    dead-lettered the outbox event and stranded running_since."""
    c = _store(tmp_path)
    allowed, delivery = c.begin_or_resume_delivery(
        "a1",
        iteration=1,
        client_id="loop:area:a1:1",
        attention="ambient",
        manual=False,
        skip_approvals=True,
        build_message=lambda events: "run",
        now=NOW,
    )
    assert allowed and delivery is not None

    tomorrow = NOW + timedelta(days=1)
    c.note_event("a1", "next-day wake", attention="ambient", paused=False, now=tomorrow)
    c.begin_or_resume_delivery(
        "a1",
        iteration=2,
        client_id="loop:area:a1:2",
        attention="ambient",
        manual=False,
        skip_approvals=True,
        build_message=lambda events: "run",
        now=tomorrow,
    )

    assert c.abandon_delivery("a1", client_id="loop:area:a1:2")
    assert "pending_delivery" not in c.state("a1")


def test_failed_bound_delivery_requires_its_exact_chat_run(tmp_path):
    c = _store(tmp_path)
    allowed, delivery = c.begin_or_resume_delivery(
        "a1",
        iteration=1,
        client_id="loop:area:a1:1",
        attention="ambient",
        manual=False,
        skip_approvals=True,
        build_message=lambda _: "run",
        now=NOW,
    )
    assert allowed and delivery is not None
    c.bind_delivery_run("a1", client_id=delivery.client_id, run_id="chat-run-1")

    assert not c.abandon_delivery("a1", run_id="stale-chat-run")
    assert c.runs_today("a1", now=NOW) == 1
    assert c.abandon_delivery("a1", run_id="chat-run-1")
    assert c.runs_today("a1", now=NOW) == 0


def test_failed_manual_delivery_does_not_change_autonomous_budget(tmp_path):
    c = _store(tmp_path)
    allowed, delivery = c.begin_or_resume_delivery(
        "a1",
        iteration=1,
        client_id="manual:area:a1:1",
        attention="ambient",
        manual=True,
        skip_approvals=True,
        build_message=lambda _: "run",
        now=NOW,
    )
    assert allowed and delivery is not None

    assert c.abandon_delivery("a1", client_id=delivery.client_id)
    assert c.runs_today("a1", now=NOW) == 0


def test_repeated_failed_runs_do_not_exhaust_the_daily_cap(tmp_path):
    c = _store(tmp_path)
    cap = AREA_ATTENTION_PRESETS["ambient"]["runs_per_day"]

    for iteration in range(1, cap + 2):
        client_id = f"loop:area:a1:{iteration}"
        run_id = f"chat-run-{iteration}"
        allowed, delivery = c.begin_or_resume_delivery(
            "a1",
            iteration=iteration,
            client_id=client_id,
            attention="ambient",
            manual=False,
            skip_approvals=True,
            build_message=lambda _: "run",
            now=NOW,
        )
        assert allowed and delivery is not None
        c.bind_delivery_run("a1", client_id=client_id, run_id=run_id)
        assert c.abandon_delivery("a1", run_id=run_id)

    assert c.runs_today("a1", now=NOW) == 0


def test_stale_delivery_is_replaced_after_iteration_was_committed(tmp_path):
    c = _store(tmp_path)
    allowed, first = c.begin_or_resume_delivery(
        "a1",
        iteration=1,
        client_id="loop:area:a1:1",
        attention="ambient",
        manual=False,
        skip_approvals=True,
        build_message=lambda _: "first",
        now=NOW,
    )
    assert allowed and first is not None

    # Simulate a crash after the scheduler incremented iteration_count but
    # before it acknowledged the delivery reservation.
    allowed, second = c.begin_or_resume_delivery(
        "a1",
        iteration=2,
        client_id="loop:area:a1:2",
        attention="ambient",
        manual=False,
        skip_approvals=True,
        build_message=lambda _: "second",
        now=NOW,
    )
    assert allowed and second is not None
    assert second.iteration == 2
    assert second.message == "second"


def test_all_autonomous_runs_share_cap_while_manual_runs_bypass(tmp_path):
    c = _store(tmp_path)
    cap = AREA_ATTENTION_PRESETS["ambient"]["runs_per_day"]

    for iteration in range(1, cap + 1):
        allowed, _ = c.begin_or_resume_delivery(
            "a1",
            iteration=iteration,
            client_id=f"loop:area:a1:{iteration}",
            attention="ambient",
            manual=False,
            skip_approvals=True,
            build_message=lambda _: "run",
            now=NOW,
        )
        assert allowed

    allowed, _ = c.begin_or_resume_delivery(
        "a1",
        iteration=cap + 1,
        client_id=f"loop:area:a1:{cap + 1}",
        attention="ambient",
        manual=False,
        skip_approvals=True,
        build_message=lambda _: "run",
        now=NOW,
    )
    assert not allowed
    manual, _ = c.begin_or_resume_delivery(
        "a1",
        iteration=cap + 1,
        client_id=f"loop:area:a1:{cap + 1}",
        attention="ambient",
        manual=True,
        skip_approvals=True,
        build_message=lambda _: "run",
        now=NOW,
    )
    assert manual
    assert c.runs_today("a1", now=NOW) == cap


def test_partial_legacy_state_recovers_without_blocking_startup(tmp_path):
    path = tmp_path / "agent-state.json"
    path.write_text('{"a1":')

    recovered = CustodianStore(path)

    assert recovered.state("a1")["runs_today"] == 0
