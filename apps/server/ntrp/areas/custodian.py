"""Custodian mechanics for area agents: self-paced heartbeat with decay,
event-wake gating (coalescing + runs/day budget), and the per-area agent
state the room displays (last report, next-check reason, woken-by).

State lives in one JSON file (~/.ntrp/areas-agent-state.json) keyed by
area_id — small, human-readable, and owned by the areas domain the same way
asks are."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ntrp.constants import (
    AREA_ATTENTION_PRESETS,
    AREA_QUIET_DECAY_FACTOR,
)
from ntrp.logging import get_logger

_logger = get_logger(__name__)

# An event wake fires this soon — long enough to coalesce a burst (several
# emails, a page edit storm) into one run, short enough to feel reactive.
EVENT_WAKE_DEBOUNCE_MINUTES = 10


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


class CustodianStore:
    """Per-area agent state: quiet streak (decay), pending wake events,
    daily run counter (budget), and the display strings for the room."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._state: dict[str, dict] = {}
        if path.exists():
            self._state = json.loads(path.read_text())

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._state, indent=2))

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

    def consume_pending(self, area_id: str, now: datetime | None = None) -> list[str]:
        """Called when a run starts: the pending events become this run's
        WOKEN BY context and the run counts against the daily budget."""
        now = now or datetime.now(UTC)
        st = self.state(area_id)
        woken_by = st["pending_events"]
        st["pending_events"] = []
        st["last_woken_by"] = woken_by
        self._count_run(area_id, now)
        self._flush()
        return woken_by

    # ── attention decay on ignored asks ─────────────────────

    def note_ignored_asks(self, area_id: str, ignored: bool) -> bool:
        """Track consecutive runs whose previous asks went unanswered.
        Returns True when attention should step down one level (3 strikes)
        — attention is the budget signal: an area the user stops answering
        decays instead of shouting louder."""
        st = self.state(area_id)
        st["unanswered_streak"] = st.get("unanswered_streak", 0) + 1 if ignored else 0
        self._flush()
        if st["unanswered_streak"] >= 3:
            st["unanswered_streak"] = 0
            self._flush()
            return True
        return False

    # ── self-paced heartbeat ────────────────────────────────

    def record_run(
        self,
        area_id: str,
        structured_output: dict | None,
        *,
        attention: str,
        now: datetime | None = None,
    ) -> datetime:
        """Digest a completed run: persist the report + reason for the room,
        update the quiet streak, and return the clamped-and-decayed next
        check time. A missing/failed nomination falls back to the attention
        ceiling (never a tight loop)."""
        now = now or datetime.now(UTC)
        preset = AREA_ATTENTION_PRESETS.get(attention, AREA_ATTENTION_PRESETS["ambient"])
        so = structured_output or {}
        st = self.state(area_id)

        quiet = not so.get("asks")
        st["quiet_streak"] = st["quiet_streak"] + 1 if quiet else 0
        st["last_report"] = so.get("report") or st["last_report"]
        st["next_check_reason"] = so.get("next_check_reason")

        hours = so.get("next_check_hours")
        if not isinstance(hours, (int, float)) or hours <= 0:
            hours = preset["max_hours"]
        # Decay: from the 2nd consecutive quiet run on, stretch the interval
        # regardless of what the agent asked for — cadence is earned by
        # activity, not claimed (self-importance drift is measured at ~1/3).
        if st["quiet_streak"] >= 2:
            hours *= AREA_QUIET_DECAY_FACTOR ** (st["quiet_streak"] - 1)
        hours = max(preset["min_hours"], min(hours, preset["max_hours"]))
        self._flush()
        return now + timedelta(hours=hours)
