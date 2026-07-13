"""Granular, deterministic daily timeline projections."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ntrp.memory.artifacts import ArtifactMemoryStore
from ntrp.memory.frontmatter import QuotedStr, dump_frontmatter, parse_frontmatter
from ntrp.memory.journal import VaultJournal
from ntrp.memory.ledger import LedgerEntry
from ntrp.memory.merge import three_way_merge
from ntrp.memory.models import SourceRef, TimePrecision
from ntrp.memory.page_events import PageEditEvent


@dataclass(frozen=True)
class DailyTimelineEvent:
    id: str
    occurred_at: str
    time_precision: TimePrecision
    sequence: int
    action: str
    text: str
    sources: tuple[SourceRef, ...]
    utc_instant: datetime | None


@dataclass(frozen=True)
class DailyGroup:
    event_ids: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class DailyGroupingDecision:
    groups: tuple[DailyGroup, ...]


@dataclass(frozen=True)
class DailyProjection:
    local_date: date
    revision: str
    timezone: str
    path: Path
    events: tuple[DailyTimelineEvent, ...]
    groups: tuple[DailyGroup, ...]
    grouped: bool
    generated: bytes
    content: bytes
    review_required: bool


EntrySource = Callable[[], Iterable[LedgerEntry]]
PageEventSource = Callable[[], Iterable[PageEditEvent]]
GroupingFunction = Callable[[tuple[DailyTimelineEvent, ...]], DailyGroupingDecision | None]
ProjectionWriter = Callable[[Path, bytes, bytes | None, str], None]


def _projection_key(revision: str, timezone: str) -> str:
    return hashlib.sha256(f"{revision}\0{timezone}".encode()).hexdigest()


def daily_base_rel(local_date: date, revision: str, timezone: str) -> Path:
    return Path(".ntrp/maintenance/daily-bases") / local_date.isoformat() / f"{_projection_key(revision, timezone)}.md"


def daily_candidate_rel(local_date: date, revision: str, timezone: str) -> Path:
    return Path(".ntrp/maintenance/daily-candidates") / local_date.isoformat() / f"{_projection_key(revision, timezone)}.md"


def _instant(value: str, precision: TimePrecision, zone: ZoneInfo) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"daily event timestamp requires an explicit offset: {value!r}")
    return parsed.astimezone(UTC)


def _entry_action(entry: LedgerEntry) -> str:
    if entry.meta.operation == "retract":
        return "retract"
    if entry.meta.supersedes:
        return "update"
    return "record"


class DailyProjector:
    """Project canonical ledger and page events into one local calendar day."""

    def __init__(
        self,
        root: Path,
        *,
        timezone: str,
        entries: EntrySource = lambda: (),
        page_events: PageEventSource = lambda: (),
        grouping: GroupingFunction | None = None,
        projection_writer: ProjectionWriter | None = None,
    ) -> None:
        self.root = Path(root)
        self.timezone = timezone
        self._zone = ZoneInfo(timezone)
        self._entries = entries
        self._page_events = page_events
        self._grouping = grouping
        self._resources = ArtifactMemoryStore(self.root)
        self._journal = VaultJournal(self.root)
        self._projection_writer = projection_writer or self._write_projection

    def events_for(self, local_date: date) -> tuple[DailyTimelineEvent, ...]:
        events: list[DailyTimelineEvent] = []
        for entry in self._entries():
            occurred_at = entry.occurred_at or entry.meta.recorded_at
            instant = None if entry.meta.time_precision == "day" else _instant(occurred_at, entry.meta.time_precision, self._zone)
            event_date = date.fromisoformat(occurred_at) if instant is None else instant.astimezone(self._zone).date()
            if event_date != local_date:
                continue
            events.append(
                DailyTimelineEvent(
                    id=entry.id,
                    occurred_at=occurred_at,
                    time_precision=entry.meta.time_precision,
                    sequence=entry.meta.sequence,
                    action=_entry_action(entry),
                    text=entry.text,
                    sources=entry.meta.sources,
                    utc_instant=instant,
                )
            )
        for page_event in self._page_events():
            if page_event.event_type == "SYNTHESIS_MERGE" and Path(page_event.path).parts[:1] == ("daily",):
                continue
            instant = _instant(page_event.occurred_at, "millisecond", self._zone)
            if instant.astimezone(self._zone).date() != local_date:
                continue
            sources: list[SourceRef] = []
            for operation in page_event.operations:
                for source in operation.sources:
                    if source not in sources:
                        sources.append(source)
            events.append(
                DailyTimelineEvent(
                    id=page_event.id,
                    occurred_at=page_event.occurred_at,
                    time_precision="millisecond",
                    sequence=page_event.sequence,
                    action="synthesis_merge" if page_event.event_type == "SYNTHESIS_MERGE" else "page_edit",
                    text=page_event.path,
                    sources=tuple(sources),
                    utc_instant=instant,
                )
            )
        return tuple(sorted(events, key=self._sort_key))

    def local_dates(self) -> tuple[date, ...]:
        dates: set[date] = set()
        for entry in self._entries():
            occurred_at = entry.occurred_at or entry.meta.recorded_at
            if entry.meta.time_precision == "day":
                dates.add(date.fromisoformat(occurred_at))
            else:
                dates.add(_instant(occurred_at, entry.meta.time_precision, self._zone).astimezone(self._zone).date())
        for page_event in self._page_events():
            if page_event.event_type == "SYNTHESIS_MERGE" and Path(page_event.path).parts[:1] == ("daily",):
                continue
            dates.add(_instant(page_event.occurred_at, "millisecond", self._zone).astimezone(self._zone).date())
        return tuple(sorted(dates))

    def render(self, local_date: date, revision: str) -> DailyProjection:
        self._assert_revision(revision)
        events = self.events_for(local_date)
        groups, grouped = self._groups(events)
        generated = self._render_bytes(local_date, revision, events, groups, grouped)
        rel = Path("daily") / f"{local_date.isoformat()}.md"
        path = self.root / rel
        try:
            current = self._resources.read_resource_bytes(rel.as_posix())
        except FileNotFoundError:
            current = None

        review_required = False
        content = generated
        if current is not None and current != generated:
            prior_revision, prior_timezone, prior_key = self._prior_projection(current)
            base = self._read_base(local_date, prior_revision, prior_timezone)
            repairing_checkpoint = (
                base is None
                and prior_revision == revision
                and prior_timezone == self.timezone
                and prior_key == _projection_key(revision, self.timezone)
            )
            if repairing_checkpoint:
                content = current
            else:
                merge = three_way_merge(base, current, generated)
                if merge.review_required:
                    self._assert_revision(revision)
                    self._resources.write_daily_maintenance(
                        daily_candidate_rel(local_date, revision, self.timezone), generated
                    )
                    review_required = True
                    content = current
                else:
                    assert merge.merged is not None
                    content = merge.merged

        if not review_required:
            self._assert_revision(revision)
            self._resources.write_daily_maintenance(
                daily_base_rel(local_date, revision, self.timezone), generated
            )
            if current != content:
                self._projection_writer(rel, content, current, revision)
        return DailyProjection(
            local_date=local_date,
            revision=revision,
            timezone=self.timezone,
            path=path,
            events=events,
            groups=groups,
            grouped=grouped,
            generated=generated,
            content=content,
            review_required=review_required,
        )

    def _groups(
        self, events: tuple[DailyTimelineEvent, ...]
    ) -> tuple[tuple[DailyGroup, ...], bool]:
        fallback = tuple(DailyGroup((event.id,), "") for event in events)
        if self._grouping is None or not events:
            return fallback, False
        try:
            decision = self._grouping(events)
        except Exception:
            return fallback, False
        if decision is None:
            return fallback, False
        if not decision.groups or any(not group.event_ids or not group.summary.strip() for group in decision.groups):
            return fallback, False
        expected = [event.id for event in events]
        actual = [event_id for group in decision.groups for event_id in group.event_ids]
        if len(actual) != len(set(actual)) or set(actual) != set(expected) or len(actual) != len(expected):
            return fallback, False
        return decision.groups, True

    @staticmethod
    def _sort_key(event: DailyTimelineEvent) -> tuple:
        if event.utc_instant is None:
            return (0, event.sequence, event.id)
        return (1, event.utc_instant, event.sequence, event.id)

    def _render_bytes(
        self,
        local_date: date,
        revision: str,
        events: tuple[DailyTimelineEvent, ...],
        groups: tuple[DailyGroup, ...],
        grouped: bool,
    ) -> bytes:
        frontmatter = dump_frontmatter(
            {
                "kind": "source",
                "title": QuotedStr(local_date.isoformat()),
                "generated": True,
                "editable": True,
                "generated_from_revision": revision,
                "timezone": QuotedStr(self.timezone),
                "projection_key": _projection_key(revision, self.timezone),
            }
        )
        lines = [frontmatter.rstrip(), "", f"# {local_date.isoformat()}", ""]
        by_id = {event.id: event for event in events}
        if not events:
            lines.append("_No memory events._")
        elif grouped:
            for group in groups:
                lines.extend((f"## {group.summary}", ""))
                lines.extend(self._event_line(by_id[event_id]) for event_id in group.event_ids)
                lines.append("")
        else:
            lines.extend(("## Timeline", ""))
            lines.extend(self._event_line(event) for event in events)
            lines.append("")
        return ("\n".join(lines).rstrip() + "\n").encode("utf-8")

    @staticmethod
    def _event_line(event: DailyTimelineEvent) -> str:
        evidence = ""
        if event.sources:
            refs = ", ".join(DailyProjector._source_text(source) for source in event.sources)
            evidence = f" _(sources: {refs})_"
        return (
            f"- `{event.occurred_at}` [{event.time_precision}] "
            f"^{event.id} **{event.action}** {event.text}{evidence}"
        )

    @staticmethod
    def _source_text(source: SourceRef) -> str:
        timestamp = source.occurred_at or source.captured_at
        suffix = f" @ {timestamp} [{source.time_precision}]" if timestamp else f" [{source.time_precision}]"
        return f"{source.kind}:{source.ref}{suffix}"

    def _write_projection(
        self,
        rel: Path,
        content: bytes,
        expected: bytes | None,
        revision: str,
    ) -> None:
        self._journal.commit_projection(
            {rel: content},
            expected_files={rel: expected},
            expected_revision=revision,
        )

    def _assert_revision(self, revision: str) -> None:
        if self._journal.canonical_revision != revision:
            raise ValueError("daily projection source revision is stale")

    @staticmethod
    def _prior_projection(current: bytes) -> tuple[str | None, str | None, str | None]:
        try:
            frontmatter, _ = parse_frontmatter(current.decode("utf-8"))
        except UnicodeDecodeError:
            return None, None, None
        revision = frontmatter.get("generated_from_revision")
        timezone = frontmatter.get("timezone")
        projection_key = frontmatter.get("projection_key")
        return (
            revision if isinstance(revision, str) else None,
            timezone if isinstance(timezone, str) else None,
            projection_key if isinstance(projection_key, str) else None,
        )

    def _read_base(
        self, local_date: date, revision: str | None, timezone: str | None
    ) -> bytes | None:
        if revision is None or timezone is None:
            return None
        try:
            return self._resources.read_daily_maintenance(
                daily_base_rel(local_date, revision, timezone)
            )
        except FileNotFoundError:
            return None


__all__ = [
    "DailyGroup",
    "DailyGroupingDecision",
    "DailyProjection",
    "DailyProjector",
    "DailyTimelineEvent",
    "daily_base_rel",
    "daily_candidate_rel",
]
