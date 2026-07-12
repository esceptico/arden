from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest

from ntrp.memory.daily import (
    DailyGroup,
    DailyGroupingDecision,
    DailyProjector,
    daily_base_rel,
    daily_candidate_rel,
)
from ntrp.memory.ledger import LedgerEntry, LedgerMeta
from ntrp.memory.models import Kind, SourceRef
from ntrp.memory.page_events import AppliedPageOperation, PageEditEvent, page_revision, unified_patch
from ntrp.server.runtime.knowledge import DailyProjectionCoordinator

if TYPE_CHECKING:
    from pathlib import Path


def _entry(
    event_id: str,
    occurred_at: str,
    *,
    sequence: int,
    text: str | None = None,
    precision: str = "millisecond",
    sources: tuple[SourceRef, ...] = (),
    operation: str = "record",
    supersedes: tuple[str, ...] = (),
) -> LedgerEntry:
    return LedgerEntry(
        id=event_id,
        text=text or event_id,
        kind=Kind.FACT,
        occurred_at=occurred_at,
        meta=LedgerMeta(
            recorded_at="2026-07-12T20:00:00.000+04:00",
            sequence=sequence,
            time_precision=precision,
            scope_kind="user",
            scope_key=None,
            sources=sources,
            operation=operation,
            supersedes=supersedes,
        ),
    )


def _set_revision(root: Path, digit: str) -> str:
    revision = digit * 64
    (root / ".ntrp").mkdir(parents=True, exist_ok=True)
    (root / ".ntrp/canonical-revision").write_text(revision, encoding="ascii")
    return revision


def _edit_event(*, operations: tuple[AppliedPageOperation, ...]) -> PageEditEvent:
    before = b"Old.\n"
    after = b"New.\n"
    return PageEditEvent(
        id="page-edit",
        occurred_at="2026-07-12T18:45:00.125+04:00",
        sequence=11,
        actor="user",
        origin="desktop",
        path="topics/a.md",
        base_revision=page_revision(before),
        result_revision=page_revision(after),
        patch=unified_patch(before, after),
        operations=operations,
        reconciliation="applied",
    )


def test_orders_by_utc_then_sequence_while_preserving_source_timestamp_text(tmp_path: Path):
    entries = (
        _entry("later-sequence", "2026-07-12T10:00:00.123+04:00", sequence=8),
        _entry("same-instant", "2026-07-12T06:00:00.123Z", sequence=7),
        _entry("later-instant", "2026-07-12T10:00:00.124+04:00", sequence=1),
    )
    revision = _set_revision(tmp_path, "1")
    projection = DailyProjector(tmp_path, timezone="Asia/Yerevan", entries=lambda: entries).render(
        date(2026, 7, 12), revision
    )

    assert [event.id for event in projection.events] == ["same-instant", "later-sequence", "later-instant"]
    assert [event.occurred_at for event in projection.events] == [
        "2026-07-12T06:00:00.123Z",
        "2026-07-12T10:00:00.123+04:00",
        "2026-07-12T10:00:00.124+04:00",
    ]
    assert b"2026-07-12T10:00:00.123+04:00" in projection.content
    assert b"[millisecond]" in projection.content


def test_date_only_legacy_uses_local_date_and_explicit_day_precision(tmp_path: Path):
    entry = _entry("legacy", "2026-07-12", sequence=0, precision="day")

    revision = _set_revision(tmp_path, "1")
    local = DailyProjector(tmp_path, timezone="Asia/Yerevan", entries=lambda: (entry,)).render(
        date(2026, 7, 12), revision
    )

    assert len(local.events) == 1
    assert local.events[0].occurred_at == "2026-07-12"
    assert local.events[0].time_precision == "day"
    assert local.events[0].utc_instant is None
    assert DailyProjector(tmp_path, timezone="America/Los_Angeles", entries=lambda: (entry,)).events_for(
        date(2026, 7, 11)
    ) == ()


def test_local_midnight_and_dst_fallback_use_configured_zone(tmp_path: Path):
    entries = (
        _entry("before-midnight", "2026-03-08T04:59:59.999Z", sequence=1),
        _entry("at-midnight", "2026-03-08T05:00:00.000Z", sequence=2),
        _entry("first-one-thirty", "2026-11-01T01:30:00.000-04:00", sequence=3),
        _entry("second-one-thirty", "2026-11-01T01:30:00.000-05:00", sequence=4),
    )
    projector = DailyProjector(tmp_path, timezone="America/New_York", entries=lambda: entries)

    assert [e.id for e in projector.events_for(date(2026, 3, 7))] == ["before-midnight"]
    assert [e.id for e in projector.events_for(date(2026, 3, 8))] == ["at-midnight"]
    assert [e.id for e in projector.events_for(date(2026, 11, 1))] == [
        "first-one-thirty",
        "second-one-thirty",
    ]


def test_one_action_per_event_and_all_evidence_refs_are_retained(tmp_path: Path):
    sources = (
        SourceRef(
            "gmail",
            "message-1",
            captured_at="2026-07-12T09:01:00.000+04:00",
            scope_kind="user",
            occurred_at="2026-07-12T09:00:00+04:00",
            time_precision="second",
            role="sender",
            excerpt_hash="hash-1",
            extra={"thread": "thread-1"},
        ),
        SourceRef("calendar", "event-2", occurred_at="2026-07-12T09:00:00+04:00", time_precision="second"),
    )
    ledger = _entry("record", "2026-07-12T09:00:00+04:00", sequence=10, sources=sources)
    page = _edit_event(
        operations=(
            AppliedPageOperation(op="ADD", text="Added", kind=Kind.FACT, sources=sources),
            AppliedPageOperation(op="RETRACT", target_ids=("old",), sources=sources),
        )
    )

    events = DailyProjector(
        tmp_path,
        timezone="Asia/Yerevan",
        entries=lambda: (ledger,),
        page_events=lambda: (page,),
    ).events_for(date(2026, 7, 12))

    assert [event.id for event in events] == ["record", "page-edit"]
    assert events[0].sources == sources
    assert events[1].sources == sources
    assert [event.action for event in events] == ["record", "page_edit"]

    revision = _set_revision(tmp_path, "1")
    content = DailyProjector(
        tmp_path, timezone="Asia/Yerevan", entries=lambda: (ledger,)
    ).render(date(2026, 7, 12), revision).content
    assert b"gmail:message-1 @ 2026-07-12T09:00:00+04:00 [second]" in content
    assert b"calendar:event-2 @ 2026-07-12T09:00:00+04:00 [second]" in content
    for exact_metadata in (b'"captured_at":"2026-07-12T09:01:00.000+04:00"', b'"role":"sender"', b'"excerpt_hash":"hash-1"', b'"thread":"thread-1"'):
        assert exact_metadata in content


def test_daily_synthesis_event_does_not_feed_the_next_daily_projection(tmp_path: Path):
    event = PageEditEvent(
        event_type="SYNTHESIS_MERGE",
        id="daily-self-write",
        occurred_at="2026-07-12T18:45:00.125+04:00",
        sequence=12,
        actor="synthesis",
        origin="synthesis",
        path="daily/2026-07-12.md",
        base_revision=page_revision(b"before"),
        result_revision=page_revision(b"after"),
        patch=unified_patch(b"before", b"after"),
        operations=(),
        reconciliation="applied",
    )

    events = DailyProjector(
        tmp_path,
        timezone="Asia/Yerevan",
        page_events=lambda: (event,),
    ).events_for(date(2026, 7, 12))

    assert events == ()
    assert DailyProjector(
        tmp_path,
        timezone="Asia/Yerevan",
        page_events=lambda: (event,),
    ).local_dates() == ()


def test_legacy_llm_synthesis_does_not_claim_daily_projection_pages(tmp_path: Path):
    from ntrp.memory.synthesize import _page_kind

    assert _page_kind(tmp_path, tmp_path / "daily/2026-07-12.md") is None


@pytest.mark.parametrize("invalid", ["duplicate", "missing", "unknown", "empty", "blank"])
def test_invalid_grouping_falls_back_to_ungrouped_without_heuristics(tmp_path: Path, invalid: str):
    entries = (
        _entry("a", "2026-07-12T09:00:00+04:00", sequence=1, text="same keyword"),
        _entry("b", "2026-07-12T10:00:00+04:00", sequence=2, text="same keyword"),
    )

    def group(_events):
        if invalid == "empty":
            return DailyGroupingDecision(groups=(DailyGroup(event_ids=(), summary="Grouped"), DailyGroup(("a", "b"), "Rest")))
        ids = {"duplicate": ("a", "a"), "missing": ("a",), "unknown": ("a", "nope"), "blank": ("a", "b")}[invalid]
        summary = "" if invalid == "blank" else "Grouped"
        return DailyGroupingDecision(groups=(DailyGroup(event_ids=ids, summary=summary),))

    revision = _set_revision(tmp_path, "1")
    projection = DailyProjector(
        tmp_path, timezone="Asia/Yerevan", entries=lambda: entries, grouping=group
    ).render(date(2026, 7, 12), revision)

    assert projection.grouped is False
    assert [group.event_ids for group in projection.groups] == [("a",), ("b",)]
    assert b"Grouped" not in projection.content


def test_valid_grouping_uses_each_explicit_event_id_once(tmp_path: Path):
    entries = (
        _entry("a", "2026-07-12T09:00:00+04:00", sequence=1),
        _entry("b", "2026-07-12T10:00:00+04:00", sequence=2),
    )
    decision = DailyGroupingDecision(groups=(DailyGroup(event_ids=("a", "b"), summary="Morning"),))

    revision = _set_revision(tmp_path, "1")
    projection = DailyProjector(
        tmp_path, timezone="Asia/Yerevan", entries=lambda: entries, grouping=lambda _: decision
    ).render(date(2026, 7, 12), revision)

    assert projection.grouped is True
    assert projection.groups == decision.groups
    assert b"Morning" in projection.content


def test_same_day_regeneration_preserves_user_edits_and_keys_revision_and_timezone(tmp_path: Path):
    entries = [_entry("a", "2026-07-12T09:00:00+04:00", sequence=1)]
    projector = DailyProjector(tmp_path, timezone="Asia/Yerevan", entries=lambda: tuple(entries))
    first_revision = _set_revision(tmp_path, "1")
    first = projector.render(date(2026, 7, 12), first_revision)
    edited = first.content.replace(b"# 2026-07-12\n", b"# 2026-07-12\n\nUser note.\n")
    first.path.write_bytes(edited)
    entries.append(_entry("b", "2026-07-12T10:00:00+04:00", sequence=2))

    second_revision = _set_revision(tmp_path, "2")
    second = projector.render(date(2026, 7, 12), second_revision)

    assert second.review_required is False
    assert b"User note." in second.content
    assert f"generated_from_revision: '{second_revision}'".encode() in second.content
    assert b'timezone: "Asia/Yerevan"' in second.content
    assert (tmp_path / daily_base_rel(date(2026, 7, 12), second_revision, "Asia/Yerevan")).read_bytes() == second.generated


def test_conflict_keeps_visible_bytes_and_writes_review_candidate(tmp_path: Path):
    entries = [_entry("a", "2026-07-12T09:00:00+04:00", sequence=1)]
    projector = DailyProjector(tmp_path, timezone="Asia/Yerevan", entries=lambda: tuple(entries))
    first_revision = _set_revision(tmp_path, "1")
    first = projector.render(date(2026, 7, 12), first_revision)
    current = first.content.replace(b"**record** a", b"**record** User rewrote this")
    first.path.write_bytes(current)
    entries[0] = _entry("a", "2026-07-12T09:00:00+04:00", sequence=1, text="Changed canonical text")

    second_revision = _set_revision(tmp_path, "2")
    conflict = projector.render(date(2026, 7, 12), second_revision)

    assert conflict.review_required is True
    assert conflict.content == current
    assert first.path.read_bytes() == current
    assert (tmp_path / daily_candidate_rel(date(2026, 7, 12), second_revision, "Asia/Yerevan")).read_bytes() == conflict.generated


def test_base_write_failure_does_not_publish_page_and_retry_remains_clean(tmp_path: Path, monkeypatch):
    entries = [_entry("a", "2026-07-12T09:00:00+04:00", sequence=1)]
    projector = DailyProjector(tmp_path, timezone="Asia/Yerevan", entries=lambda: tuple(entries))
    first = projector.render(date(2026, 7, 12), _set_revision(tmp_path, "1"))
    first.path.write_bytes(first.content.replace(b"# 2026-07-12\n", b"# 2026-07-12\n\nUser note.\n"))
    entries.append(_entry("b", "2026-07-12T10:00:00+04:00", sequence=2))
    revision = _set_revision(tmp_path, "2")
    write_base = projector._resources.write_daily_maintenance
    monkeypatch.setattr(
        projector._resources,
        "write_daily_maintenance",
        lambda *_: (_ for _ in ()).throw(OSError("base unavailable")),
    )

    before_retry = first.path.read_bytes()
    with pytest.raises(OSError, match="base unavailable"):
        projector.render(date(2026, 7, 12), revision)
    assert first.path.read_bytes() == before_retry

    monkeypatch.setattr(projector._resources, "write_daily_maintenance", write_base)
    retried = projector.render(date(2026, 7, 12), revision)
    assert retried.review_required is False
    assert b"User note." in retried.content and b"^b " in retried.content
    assert (tmp_path / daily_base_rel(date(2026, 7, 12), revision, "Asia/Yerevan")).read_bytes() == retried.generated


def test_daily_projection_write_rejects_symlinked_parent(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "daily").symlink_to(outside, target_is_directory=True)
    entry = _entry("a", "2026-07-12T09:00:00+04:00", sequence=1)
    revision = _set_revision(tmp_path, "1")

    with pytest.raises((FileNotFoundError, ValueError)):
        DailyProjector(tmp_path, timezone="Asia/Yerevan", entries=lambda: (entry,)).render(date(2026, 7, 12), revision)

    assert tuple(outside.iterdir()) == ()


@pytest.mark.asyncio
async def test_runtime_coordinator_retries_without_touching_canonical_revision():
    day = date(2026, 7, 12)

    class Projector:
        def __init__(self):
            self.attempts = 0

        def local_dates(self):
            return (day,)

        def render(self, local_date, revision):
            assert local_date == day
            assert revision == "canonical-r1"
            self.attempts += 1
            if self.attempts == 1:
                raise OSError("projection unavailable")

    projector = Projector()
    coordinator = DailyProjectionCoordinator(
        projector,
        revision=lambda: "canonical-r1",
        retry_delay=60,
    )

    coordinator.schedule()
    await coordinator.wait_idle()
    assert projector.attempts == 1
    assert coordinator.stale is True
    assert coordinator.retry_scheduled is True

    coordinator.retry_now()
    await coordinator.wait_idle()
    assert projector.attempts == 2
    assert coordinator.stale is False
    assert coordinator.retry_scheduled is False
    await coordinator.close()


@pytest.mark.asyncio
async def test_runtime_coordinator_does_not_starve_later_dates_after_one_failure():
    first = date(2026, 7, 11)
    second = date(2026, 7, 12)

    class Projector:
        def __init__(self):
            self.attempts = []

        def local_dates(self):
            return (first, second)

        def render(self, local_date, _revision):
            self.attempts.append(local_date)
            if local_date == first:
                raise OSError("first day unavailable")

    projector = Projector()
    coordinator = DailyProjectionCoordinator(projector, revision=lambda: "r", retry_delay=60)
    coordinator.schedule()
    await coordinator.wait_idle()

    assert projector.attempts == [first, second]
    assert coordinator.stale is True
    assert coordinator.retry_scheduled is True
    await coordinator.close()


@pytest.mark.asyncio
async def test_runtime_projection_writer_registers_crash_verifiable_engine_receipt(tmp_path: Path):
    from ntrp.memory.file_store import FilePageStore

    vault = tmp_path / "memory"
    (vault / "raw").mkdir(parents=True)
    (vault / "raw/me.md").write_text("<!-- ntrp:records schema=2 page=me.md -->\n", encoding="utf-8")
    store = FilePageStore(vault)
    await store.open()
    record = await store.add(
        "Canonical action",
        source_ref=SourceRef(
            "user",
            "chat-1",
            occurred_at="2026-07-12T09:00:00.000+04:00",
            time_precision="millisecond",
        ),
    )
    revision = store.canonical_revision
    projector = DailyProjector(
        vault,
        timezone="Asia/Yerevan",
        entries=store._ledger_entries,
        projection_writer=store.commit_generated_projection,
    )

    projector.render(date(2026, 7, 12), revision)

    state = store._read_observed_state()
    marker = next(marker for marker in state["engine_writes"] if marker["path"] == "daily/2026-07-12.md")
    assert marker["origin"] == "synthesis"
    assert marker["receipt_id"]
    assert (vault / "raw/write-receipts" / f"{marker['receipt_id']}.json").is_file()
    assert record.id in (vault / "daily/2026-07-12.md").read_text(encoding="utf-8")
    await store.close()
