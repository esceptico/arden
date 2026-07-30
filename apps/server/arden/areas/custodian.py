"""Custodian mechanics for area agents: self-paced heartbeat with decay,
event-wake gating (coalescing + runs/day budget), and the per-area agent
state the room displays (last report, next-check reason, woken-by).

State lives in one JSON file (~/.arden/areas-agent-state.json) keyed by
area_id — small, human-readable, and owned by the areas domain the same way
asks are."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from arden.areas.agent import AreaCustodianReport
from arden.areas.paths import atomic_write_text
from arden.constants import (
    AREA_ATTENTION_PRESETS,
    AREA_QUIET_DECAY_FACTOR,
)
from arden.logging import get_logger

_logger = get_logger(__name__)

# An event wake fires this soon — long enough to coalesce a burst (several
# emails, a page edit storm) into one run, short enough to feel reactive.
EVENT_WAKE_DEBOUNCE_MINUTES = 10


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


@dataclass(frozen=True)
class CustodianDelivery:
    """The exact chat request reserved for one custodian iteration."""

    iteration: int
    client_id: str
    message: str
    skip_approvals: bool


class CustodianStore:
    """Per-area agent state: quiet streak (decay), pending wake events,
    daily run counter (budget), and the display strings for the room."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._state: dict[str, dict] = {}
        if path.exists():
            try:
                self._state = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                _logger.exception("Ignoring unreadable Custodian state at %s", path)

    def _flush(self) -> None:
        atomic_write_text(self._path, json.dumps(self._state, indent=2))

    def state(self, area_id: str) -> dict:
        return self._state.setdefault(
            area_id,
            {
                "quiet_streak": 0,
                "last_report": None,
                "next_check_reason": None,
                "pending_events": [],
                "last_woken_by": [],
                "runs_day": None,
                "runs_today": 0,
            },
        )

    # ── budget ──────────────────────────────────────────────

    def runs_today(self, area_id: str, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        st = self.state(area_id)
        return st["runs_today"] if st["runs_day"] == now.date().isoformat() else 0

    def _count_run(self, area_id: str, now: datetime) -> None:
        st = self.state(area_id)
        day = now.date().isoformat()
        if st["runs_day"] != day:
            st["runs_day"] = day
            st["runs_today"] = 0
        st["runs_today"] += 1

    @staticmethod
    def _delivery(st: dict) -> CustodianDelivery | None:
        pending = st.get("pending_delivery")
        if pending is None:
            return None
        return CustodianDelivery(
            iteration=pending["iteration"],
            client_id=pending["client_id"],
            message=pending["message"],
            skip_approvals=pending["skip_approvals"],
        )

    def pending_delivery(self, area_id: str, iteration: int) -> CustodianDelivery | None:
        delivery = self._delivery(self.state(area_id))
        if delivery is None or delivery.iteration < iteration:
            return None
        if delivery.iteration > iteration:
            raise RuntimeError(f"Custodian delivery iteration moved backwards for {area_id}")
        return delivery

    def bind_delivery_run(self, area_id: str, *, client_id: str, run_id: str) -> None:
        pending = self.state(area_id).get("pending_delivery")
        if pending is None or pending["client_id"] != client_id:
            raise RuntimeError(f"Custodian delivery changed before chat binding for {area_id}")
        pending["run_id"] = run_id
        self._flush()

    def abandon_delivery(
        self,
        area_id: str,
        *,
        client_id: str | None = None,
        run_id: str | None = None,
    ) -> bool:
        """Release one exact failed delivery and restore its wake events."""

        if (client_id is None) == (run_id is None):
            raise ValueError("Provide exactly one custodian delivery identifier")
        st = self.state(area_id)
        pending = st.get("pending_delivery")
        if pending is None:
            return False
        matches = pending["client_id"] == client_id if client_id is not None else pending.get("run_id") == run_id
        if not matches:
            return False

        budget_day = pending.get("budget_day")
        if budget_day is not None:
            if st["runs_day"] != budget_day or st["runs_today"] < 1:
                raise RuntimeError(f"Custodian run budget changed before delivery rollback for {area_id}")
            st["runs_today"] -= 1
        restored_events = [*pending.get("woken_by", ()), *st["pending_events"]]
        st["pending_events"] = list(dict.fromkeys(restored_events))[-8:]
        del st["pending_delivery"]
        self._flush()
        return True

    def needs_intake(self, area_id: str) -> bool:
        return self.state(area_id)["last_report"] is None

    # ── event wakes ─────────────────────────────────────────

    def note_event(
        self,
        area_id: str,
        description: str,
        *,
        attention: str,
        paused: bool,
        now: datetime | None = None,
    ) -> datetime | None:
        """Record a domain event. Returns the debounced wake deadline when the
        event should pull the next run earlier, or None when the event is
        noted for the next run but must not wake one (paused, or the day's
        run budget is spent — the heartbeat still carries it)."""
        now = now or datetime.now(UTC)
        st = self.state(area_id)
        if description not in st["pending_events"]:
            st["pending_events"] = (st["pending_events"] + [description])[-8:]
        self._flush()
        if paused:
            return None
        preset = AREA_ATTENTION_PRESETS.get(attention, AREA_ATTENTION_PRESETS["ambient"])
        if self.runs_today(area_id, now) >= preset["runs_per_day"]:
            _logger.info("Area %s at run budget; event noted for next heartbeat", area_id)
            return None
        return now + timedelta(minutes=EVENT_WAKE_DEBOUNCE_MINUTES)

    def begin_or_resume_delivery(
        self,
        area_id: str,
        *,
        iteration: int,
        client_id: str,
        attention: str,
        manual: bool,
        skip_approvals: bool,
        build_message: Callable[[tuple[str, ...]], str],
        now: datetime | None = None,
    ) -> tuple[bool, CustodianDelivery | None]:
        """Reserve one exact delivery, or replay its durable reservation.

        The reservation shares the custodian state write that consumes wake
        events and the daily budget. A retry therefore cannot re-render a
        different request for the same loop iteration.
        """

        now = now or datetime.now(UTC)
        st = self.state(area_id)
        pending = self._delivery(st)
        if pending is not None:
            if pending.iteration == iteration:
                if pending.client_id == client_id:
                    return True, pending
                replacement = CustodianDelivery(
                    iteration=pending.iteration,
                    client_id=client_id,
                    message=pending.message,
                    skip_approvals=pending.skip_approvals,
                )
                st["pending_delivery"]["client_id"] = client_id
                self._flush()
                return True, replacement
            if pending.iteration > iteration:
                raise RuntimeError(f"Custodian delivery iteration moved backwards for {area_id}")
            del st["pending_delivery"]

        preset = AREA_ATTENTION_PRESETS.get(attention, AREA_ATTENTION_PRESETS["ambient"])
        if not manual and self.runs_today(area_id, now) >= preset["runs_per_day"]:
            return False, None
        woken_by = tuple(st["pending_events"])
        message = build_message(woken_by)
        delivery = CustodianDelivery(
            iteration=iteration,
            client_id=client_id,
            message=message,
            skip_approvals=skip_approvals,
        )
        st["pending_events"] = []
        st["last_woken_by"] = list(woken_by)
        if not manual:
            self._count_run(area_id, now)
        st["pending_delivery"] = {
            "iteration": delivery.iteration,
            "client_id": delivery.client_id,
            "message": delivery.message,
            "skip_approvals": delivery.skip_approvals,
            "run_id": None,
            "budget_day": None if manual else now.date().isoformat(),
            "woken_by": list(woken_by),
        }
        self._flush()
        return True, delivery

    # ── attention decay on ignored asks ─────────────────────

    def note_ignored_asks(
        self,
        area_id: str,
        ignored: bool,
        *,
        attention: str,
        run_ref: str,
    ) -> str | None:
        """Track consecutive runs whose previous asks went unanswered.
        Returns the exact lower attention when three strikes are reached
        — attention is the budget signal: an area the user stops answering
        decays instead of shouting louder."""
        st = self.state(area_id)
        if st.get("last_ignored_run_ref") == run_ref:
            return st.get("last_ignored_attention")
        st["unanswered_streak"] = st.get("unanswered_streak", 0) + 1 if ignored else 0
        lowered_attention = None
        if st["unanswered_streak"] >= 3:
            st["unanswered_streak"] = 0
            lowered_attention = {"active": "ambient", "ambient": "dormant"}.get(attention)
        st["last_ignored_run_ref"] = run_ref
        st["last_ignored_attention"] = lowered_attention
        self._flush()
        return lowered_attention

    # ── self-paced heartbeat ────────────────────────────────

    def record_run(
        self,
        area_id: str,
        report: AreaCustodianReport,
        *,
        run_ref: str,
        attention: str,
        paused: bool = False,
        now: datetime | None = None,
    ) -> datetime:
        """Digest one accepted report and return its bounded next check."""
        now = now or datetime.now(UTC)
        preset = AREA_ATTENTION_PRESETS[attention]
        st = self.state(area_id)
        if st.get("last_processed_run_ref") == run_ref:
            return datetime.fromisoformat(st["last_next_run_at"])
        st.pop("pending_delivery", None)

        made_progress = report.made_progress
        quiet = not report.asks and not made_progress
        st["quiet_streak"] = st["quiet_streak"] + 1 if quiet else 0
        st["last_report"] = report.report
        continuation = report.continuation_minutes
        may_continue = (
            made_progress
            and report.work_remaining
            and continuation is not None
            and not paused
            and self.runs_today(area_id, now) < preset["runs_per_day"]
        )
        st["next_check_reason"] = (
            report.continuation_reason if may_continue and report.continuation_reason else report.next_check_reason
        )

        hours = report.next_check_hours
        # Decay: from the 2nd consecutive quiet run on, stretch the interval
        # regardless of what the agent asked for — cadence is earned by
        # activity, not claimed (self-importance drift is measured at ~1/3).
        if st["quiet_streak"] >= 2:
            hours *= AREA_QUIET_DECAY_FACTOR ** (st["quiet_streak"] - 1)
        hours = max(preset["min_hours"], min(hours, preset["max_hours"]))
        next_run = now + timedelta(minutes=continuation) if may_continue else now + timedelta(hours=hours)
        st["last_processed_run_ref"] = run_ref
        st["last_next_run_at"] = next_run.isoformat()
        self._flush()
        return next_run
