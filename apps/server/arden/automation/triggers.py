import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal

from arden.constants import (
    AUTOMATION_EVENT_APPROACHING_DEFAULT_LEAD_MINUTES,
    DAYS_IN_WEEK,
    MONITOR_EVENT_APPROACHING_HORIZON_MINUTES,
)
from arden.events.triggers import EVENT_APPROACHING

DAY_NAMES: dict[str, int] = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}
WEEKDAY_SET = tuple(range(5))
ALL_DAYS = tuple(range(7))

DAY_KEYWORDS: dict[str, tuple[int, ...]] = {
    "daily": ALL_DAYS,
    "weekdays": WEEKDAY_SET,
}
_INTERVAL_RE = re.compile(r"^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m?)?$")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


@dataclass(frozen=True)
class TimeOfDay:
    hour: int
    minute: int

    def __post_init__(self) -> None:
        if not (0 <= self.hour <= 23 and 0 <= self.minute <= 59):
            raise ValueError(f"Invalid time: {self.hour:02d}:{self.minute:02d}")

    @classmethod
    def parse(cls, raw: str) -> "TimeOfDay":
        match = _TIME_RE.match(raw.strip())
        if not match:
            raise ValueError(f"Invalid time format '{raw}'. Use HH:MM (24h)")
        return cls(hour=int(match.group(1)), minute=int(match.group(2)))

    def to_time(self) -> time:
        return time(self.hour, self.minute)

    def __str__(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


@dataclass(frozen=True)
class Interval:
    delta: timedelta

    def __post_init__(self) -> None:
        if self.delta < timedelta(minutes=1):
            raise ValueError("Interval must be at least 1 minute")

    @classmethod
    def parse(cls, raw: str) -> "Interval":
        match = _INTERVAL_RE.match(raw.strip().lower())
        if not match or not any(match.groups()):
            raise ValueError(
                f"Invalid interval: '{raw}'. Use combinations of days/hours/minutes, "
                "e.g. '30m', '2h', '1h30m', '1d', '2d12h'"
            )
        days = int(match.group(1) or 0)
        hours = int(match.group(2) or 0)
        minutes = int(match.group(3) or 0)
        return cls(delta=timedelta(days=days, hours=hours, minutes=minutes))

    def __str__(self) -> str:
        total_minutes = int(self.delta.total_seconds() / 60)
        # Emit days when the interval is a whole number of days so the round
        # trip is symmetric — `parse('2d')` then `str(_)` returns `'2d'`,
        # not `'48h'`. Mixed intervals keep the existing hh/mm form so we
        # don't have to special-case partial days.
        d, rem = divmod(total_minutes, 60 * 24)
        h, m = divmod(rem, 60)
        parts = []
        if d:
            parts.append(f"{d}d")
        if h:
            parts.append(f"{h}h")
        if m or not parts:
            parts.append(f"{m}m")
        return "".join(parts)


@dataclass(frozen=True)
class DaySpec:
    days: tuple[int, ...]

    @classmethod
    def parse(cls, raw: str) -> "DaySpec":
        days: set[int] = set()
        for part in raw.split(","):
            token = part.strip().lower()
            if not token:
                continue
            if token in DAY_KEYWORDS:
                days.update(DAY_KEYWORDS[token])
            elif token in DAY_NAMES:
                days.add(DAY_NAMES[token])
            else:
                raise ValueError(f"Invalid day: '{token}'. Use: {', '.join(DAY_NAMES)} / daily / weekdays")
        return cls(days=tuple(sorted(days)) if days else ALL_DAYS)

    def __str__(self) -> str:
        if self.days == ALL_DAYS:
            return "daily"
        if self.days == WEEKDAY_SET:
            return "weekdays"
        reverse = {v: k for k, v in DAY_NAMES.items()}
        return ",".join(reverse[d] for d in sorted(self.days))

    def __contains__(self, weekday: int) -> bool:
        return weekday in self.days


def parse_interval(raw: str) -> timedelta:
    return Interval.parse(raw).delta


def normalize_lead_minutes(raw: int | str | None) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        td = parse_interval(raw)
        return int(td.total_seconds() / 60)
    return int(raw)


def _advance_to_days(candidate: datetime, day_spec: DaySpec) -> datetime:
    for _ in range(DAYS_IN_WEEK):
        if candidate.weekday() in day_spec:
            return candidate
        candidate += timedelta(days=1)
    return candidate


def compute_next_schedule(at: TimeOfDay | str, days: DaySpec | str | None, after: datetime) -> datetime:
    if isinstance(at, str):
        at = TimeOfDay.parse(at)
    day_spec = DaySpec.parse(days) if isinstance(days, str) else (days or DaySpec(ALL_DAYS))
    local_now = after.astimezone()

    candidate = local_now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)

    candidate = _advance_to_days(candidate, day_spec)
    return candidate.astimezone(UTC)


def compute_next_interval(
    every: Interval | str,
    days: DaySpec | str | None,
    after: datetime,
    start: TimeOfDay | str | None = None,
    end: TimeOfDay | str | None = None,
) -> datetime:
    if isinstance(every, str):
        every = Interval.parse(every)
    if isinstance(start, str):
        start = TimeOfDay.parse(start)
    if isinstance(end, str):
        end = TimeOfDay.parse(end)

    local_now = after.astimezone()
    candidate = local_now + every.delta

    if start and candidate.time() < start.to_time():
        candidate = candidate.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)

    if end and candidate.time() >= end.to_time():
        candidate = (candidate + timedelta(days=1)).replace(
            hour=start.hour if start else 0,
            minute=start.minute if start else 0,
            second=0,
            microsecond=0,
        )

    if days:
        day_spec = DaySpec.parse(days) if isinstance(days, str) else days
        candidate = _advance_to_days(candidate, day_spec)

    return candidate.astimezone(UTC)


@dataclass
class TimeTrigger:
    type: Literal["time"] = "time"
    at: TimeOfDay | None = None
    days: DaySpec | None = None
    every: Interval | None = None
    start: TimeOfDay | None = None
    end: TimeOfDay | None = None

    def __post_init__(self) -> None:
        if isinstance(self.at, str):
            self.at = TimeOfDay.parse(self.at)
        if isinstance(self.days, str):
            self.days = DaySpec.parse(self.days)
        if isinstance(self.every, str):
            self.every = Interval.parse(self.every)
        if isinstance(self.start, str):
            self.start = TimeOfDay.parse(self.start)
        if isinstance(self.end, str):
            self.end = TimeOfDay.parse(self.end)

        if not self.at and not self.every:
            raise ValueError("Either 'at' (specific time) or 'every' (interval) is required")
        if self.at and self.every:
            raise ValueError("'at' and 'every' are mutually exclusive")
        if (self.start or self.end) and not self.every:
            raise ValueError("'start'/'end' time windows require 'every' (interval mode)")
        if self.start and self.end and self.start.to_time() >= self.end.to_time():
            raise ValueError(f"'start' ({self.start}) must be before 'end' ({self.end})")

    def params(self) -> dict:
        d: dict = {}
        if self.at:
            d["at"] = str(self.at)
        if self.days:
            d["days"] = str(self.days)
        if self.every:
            d["every"] = str(self.every)
        if self.start:
            d["start"] = str(self.start)
        if self.end:
            d["end"] = str(self.end)
        return d

    @property
    def one_shot(self) -> bool:
        return bool(self.at and not self.days)

    @property
    def label(self) -> str:
        if self.every:
            base = f"every {self.every}"
            if self.start and self.end:
                base += f" ({self.start}\u2013{self.end})"
        else:
            base = str(self.at) if self.at else ""
        if self.days:
            base += f"  {self.days}"
        return base

    def next_run(self, after: datetime) -> datetime:
        if self.every:
            return compute_next_interval(self.every, self.days, after, self.start, self.end)
        return compute_next_schedule(self.at, self.days, after)


@dataclass
class EventTrigger:
    event_type: str
    lead_minutes: int | None = None
    type: Literal["event"] = "event"

    def __post_init__(self) -> None:
        self.lead_minutes = normalize_lead_minutes(self.lead_minutes)

        if self.event_type == EVENT_APPROACHING:
            if self.lead_minutes is None:
                self.lead_minutes = AUTOMATION_EVENT_APPROACHING_DEFAULT_LEAD_MINUTES
            max_lead = MONITOR_EVENT_APPROACHING_HORIZON_MINUTES
            if self.lead_minutes > max_lead:
                raise ValueError(
                    f"lead_minutes={self.lead_minutes} exceeds the monitor horizon ({max_lead}m). "
                    f"Use {max_lead} or less."
                )

    def params(self) -> dict:
        d: dict = {"event_type": self.event_type}
        if self.lead_minutes is not None:
            d["lead_minutes"] = self.lead_minutes
        return d

    @property
    def one_shot(self) -> bool:
        return False

    @property
    def label(self) -> str:
        label = f"on:{self.event_type}"
        if self.lead_minutes is not None:
            label += f" ({self.lead_minutes}m)"
        return label

    def next_run(self, after: datetime) -> datetime | None:
        return None


@dataclass
class IdleTrigger:
    idle_minutes: int
    type: Literal["idle"] = "idle"

    def params(self) -> dict:
        return {"idle_minutes": self.idle_minutes}

    @property
    def one_shot(self) -> bool:
        return False

    @property
    def label(self) -> str:
        return f"idle {self.idle_minutes}m"

    def next_run(self, after: datetime) -> datetime | None:
        return None


@dataclass
class CountTrigger:
    every_n: int
    type: Literal["count"] = "count"

    def params(self) -> dict:
        return {"every_n": self.every_n}

    @property
    def one_shot(self) -> bool:
        return False

    @property
    def label(self) -> str:
        return f"every {self.every_n} turns"

    def next_run(self, after: datetime) -> datetime | None:
        return None


@dataclass(frozen=True)
class MessageTrigger:
    type: Literal["message"] = "message"
    source: str = "slack"
    # Each channel is {"id": <slack id>, "name": <display name>}, resolved at
    # save time. The trigger fires for a message in ANY of its channels.
    channels: list[dict] = field(default_factory=list)
    from_user_id: str | None = None
    from_user_name: str | None = None
    contains: list[str] = field(default_factory=list)

    def params(self) -> dict:
        return {
            "source": self.source,
            "channels": self.channels,
            "from_user_id": self.from_user_id,
            "from_user_name": self.from_user_name,
            "contains": self.contains,
        }

    @property
    def channel_ids(self) -> list[str]:
        return [c["id"] for c in self.channels]

    @property
    def label(self) -> str:
        names = ", ".join(f"#{c['name']}" for c in self.channels) or "#?"
        label = f"slack {names}"
        if self.from_user_name:
            label += f" from @{self.from_user_name}"
        if self.contains:
            label += f" ~ {', '.join(self.contains)}"
        return label

    @property
    def one_shot(self) -> bool:
        return False


Trigger = TimeTrigger | EventTrigger | IdleTrigger | CountTrigger | MessageTrigger


def _next_run_for_time(trigger: TimeTrigger, now: datetime) -> datetime:
    if trigger.every:
        return compute_next_interval(trigger.every, trigger.days, now, trigger.start, trigger.end)
    return compute_next_schedule(trigger.at, trigger.days, now)


BuildHandler = Callable[..., tuple[Trigger, datetime | None]]


def _build_time_trigger(
    *,
    at: str | None,
    days: str | None,
    every: str | None,
    start: str | None,
    end: str | None,
    **_kwargs: Any,
) -> tuple[Trigger, datetime | None]:
    trigger = TimeTrigger(at=at, days=days, every=every, start=start, end=end)
    return trigger, _next_run_for_time(trigger, datetime.now(UTC))


def _build_event_trigger(
    *,
    event_type: str | None,
    lead_minutes: int | str | None,
    **_kwargs: Any,
) -> tuple[Trigger, datetime | None]:
    if not event_type:
        raise ValueError("'event_type' is required for event trigger")
    return EventTrigger(event_type=event_type, lead_minutes=lead_minutes), None


def _build_idle_trigger(
    *,
    idle_minutes: int | None = None,
    **_kwargs: Any,
) -> tuple[Trigger, datetime | None]:
    if idle_minutes is None:
        raise ValueError("'idle_minutes' is required for idle trigger")
    return IdleTrigger(idle_minutes=int(idle_minutes)), None


def _build_count_trigger(
    *,
    every_n: int | None = None,
    **_kwargs: Any,
) -> tuple[Trigger, datetime | None]:
    if every_n is None:
        raise ValueError("'every_n' is required for count trigger")
    return CountTrigger(every_n=int(every_n)), None


BUILD_DISPATCH: dict[str, BuildHandler] = {
    "time": _build_time_trigger,
    "event": _build_event_trigger,
    "idle": _build_idle_trigger,
    "count": _build_count_trigger,
}


def build_trigger(
    trigger_type: str,
    at: str | None = None,
    days: str | None = None,
    every: str | None = None,
    event_type: str | None = None,
    lead_minutes: int | str | None = None,
    start: str | None = None,
    end: str | None = None,
    idle_minutes: int | None = None,
    every_n: int | None = None,
) -> tuple[Trigger, datetime | None]:
    if (handler := BUILD_DISPATCH.get(trigger_type)) is None:
        raise ValueError(f"Invalid trigger_type '{trigger_type}'. Use: time, event, idle, count")
    return handler(
        at=at,
        days=days,
        every=every,
        event_type=event_type,
        lead_minutes=lead_minutes,
        start=start,
        end=end,
        idle_minutes=idle_minutes,
        every_n=every_n,
    )


ParseHandler = Callable[[dict], Trigger]


def _parse_time_trigger(payload: dict) -> Trigger:
    return TimeTrigger(
        at=payload.get("at"),
        days=payload.get("days"),
        every=payload.get("every"),
        start=payload.get("start"),
        end=payload.get("end"),
    )


def _parse_event_trigger(payload: dict) -> Trigger:
    return EventTrigger(event_type=payload["event_type"], lead_minutes=payload.get("lead_minutes"))


def _parse_idle_trigger(payload: dict) -> Trigger:
    return IdleTrigger(idle_minutes=payload["idle_minutes"])


def _parse_count_trigger(payload: dict) -> Trigger:
    return CountTrigger(every_n=payload["every_n"])


def _parse_message_trigger(payload: dict) -> Trigger:
    return MessageTrigger(
        source=payload["source"],
        channels=payload.get("channels", []),
        from_user_id=payload.get("from_user_id"),
        from_user_name=payload.get("from_user_name"),
        contains=payload.get("contains", []),
    )


PARSE_DISPATCH: dict[str, ParseHandler] = {
    "time": _parse_time_trigger,
    "event": _parse_event_trigger,
    "idle": _parse_idle_trigger,
    "count": _parse_count_trigger,
    "message": _parse_message_trigger,
}


def parse_one(payload: dict) -> Trigger | None:
    if payload.get("type") == "knowledge_event":
        return None
    if (handler := PARSE_DISPATCH.get(payload["type"])) is None:
        raise ValueError(f"Unknown trigger type: {payload['type']}")
    return handler(payload)


def parse_triggers(raw: str) -> list[Trigger]:
    items = json.loads(raw)
    return [trigger for item in items if (trigger := parse_one(item)) is not None]
