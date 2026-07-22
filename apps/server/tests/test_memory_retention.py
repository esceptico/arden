from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ntrp.memory.file_store import FilePageStore
from ntrp.memory.ledger import LedgerEntry, LedgerMeta, render_ledger_entry
from ntrp.memory.models import Kind, SourceRef
from ntrp.memory.retention import run_retention

pytestmark = pytest.mark.asyncio


def _write_v2(vault: Path, entries: list[LedgerEntry]) -> None:
    visible = vault / "topics" / "a.md"
    raw = vault / "raw" / "topics" / "a.md"
    visible.parent.mkdir(parents=True)
    raw.parent.mkdir(parents=True)
    visible.write_text("# A\n", encoding="utf-8")
    raw.write_text(
        "<!-- ntrp:records schema=2 page=topics/a.md -->\n"
        + "\n".join(render_ledger_entry(entry) for entry in entries)
        + "\n",
        encoding="utf-8",
    )


def _entry(record_id: str, text: str, *, days_old: int, pinned: bool = False) -> LedgerEntry:
    recorded = (datetime.now(UTC) - timedelta(days=days_old)).replace(microsecond=0).isoformat()
    source = SourceRef("chat_message", f"s:{record_id}", captured_at=recorded)
    return LedgerEntry(
        id=record_id,
        text=text,
        kind=Kind.FACT,
        occurred_at=recorded,
        pinned=pinned,
        meta=LedgerMeta(
            recorded_at=recorded,
            sequence=days_old,
            time_precision="second",
            scope_kind="user",
            scope_key=None,
            sources=(source,),
        ),
    )


async def test_v2_retention_appends_sourced_retract_and_is_idempotent(tmp_path: Path):
    vault = tmp_path / "memory"
    old = _entry("old", "Old fact", days_old=800)
    pinned = _entry("pinned", "Pinned old fact", days_old=800, pinned=True)
    recent = _entry("recent", "Recent fact", days_old=1)
    _write_v2(vault, [old, pinned, recent])
    store = FilePageStore(vault)
    await store.open()

    report = await run_retention(store)

    assert report.examined == 3
    assert report.superseded == 1
    assert await store.get(old.id) is None
    assert await store.get(pinned.id) is not None
    assert await store.get(recent.id) is not None
    history = store.history(old.id)
    assert [entry.meta.operation for entry in history] == ["record", "retract"]
    assert history[-1].meta.sources[-1].kind == "retention"
    assert history[-1].meta.sources[-1].ref == f"ttl:{old.id}"
    assert (await run_retention(store)).superseded == 0
    assert "Old fact" in (vault / "raw" / "topics" / "a.md").read_text(encoding="utf-8")
    await store.close()

    reopened = FilePageStore(vault)
    await reopened.open()
    assert await reopened.get(old.id) is None
    await reopened.close()
