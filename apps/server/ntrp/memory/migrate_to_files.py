"""One-pass migration: frozen memory.db -> two-zone markdown pages.

Field-mechanical, pin-preserving, supersede-not-delete. Filing uses the record's
entity label (label_kind='entity') -> entities/<slug>.md; project scope ->
projects/<slug>.md; directives -> directives.md; sources -> references.md; the
rest -> me.md. Re-runnable from the frozen db.

No git. Safety = plain-file backups: memory.db is copied to *.premigrate.bak and
the existing page dir is moved aside before a fresh tree is written.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from ntrp.logging import get_logger
from ntrp.memory.file_store import FilePageStore
from ntrp.memory.records import RecordStore

_logger = get_logger(__name__)


async def migrate(record_store: RecordStore, root: Path) -> dict:
    """Write every record in `record_store` as a timeline line under `root`.
    `root` must be empty/fresh (the caller backs up + clears). Returns counts."""
    records = await record_store.list(limit=None, include_superseded=True)
    typed = await record_store.labels_for([r.id for r in records], include_kind=True)

    store = FilePageStore(root)
    await store.open()

    # Shorten 32-char hex ids to readable 8-char anchors, extending on collision.
    used: set[str] = set()

    def short(full: str) -> str:
        n = 8
        sid = full[:n]
        while sid in used:
            n += 1
            sid = full[:n]
        used.add(sid)
        return sid

    pinned = superseded = 0
    for rec in records:
        rid = short(rec.id)
        labels = typed.get(rec.id, [])
        entity = [l["label"] for l in labels if l.get("kind") == "entity"]
        meta = [l["label"] for l in labels if l.get("kind") != "entity"]
        await store.add(
            rec.text,
            kind=rec.kind,
            pinned=rec.pinned,
            source_ref=rec.source_ref,
            scope_kind=rec.scope_kind,
            scope_key=rec.scope_key,
            record_id=rid,
            entity_labels=entity or None,
            date=rec.last_confirmed_at,
        )
        if meta or entity:
            await store.set_labels(rid, meta, entity_labels=entity or None)
        if rec.pinned:
            pinned += 1
        if rec.superseded_by:
            found = store._find(rid)
            if found:
                found[1].superseded = True
                store._persist(found[0])
            superseded += 1

    # Backfill heuristic importance so day-1 search has signal with zero LLM calls.
    from ntrp.memory.scorer import heuristic_score

    for path, page in store._pages.items():
        dirty = False
        for line in page.lines:
            if line.imp is None:
                line.imp = heuristic_score(line.kind, line.pinned)
                dirty = True
        if dirty:
            store._persist(path)

    active = await store.count_active()
    result = {"records": len(records), "active": active, "pinned": pinned, "superseded": superseded, "pages": len(store._pages)}
    _logger.info("memory migrated to files", **result, root=str(root))
    return result


async def migrate_live(*, apply: bool) -> dict:
    """Migrate the configured live store. Backs up memory.db and the page dir
    first (filesystem copies). With apply=False, migrates into a *_migrated
    sibling dir for inspection and touches nothing live."""
    from ntrp.config import get_config

    config = get_config()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    db_path = config.memory_db_path
    live_dir = config.memory_artifacts_dir

    store = RecordStore(db_path=db_path)
    await store.open()
    try:
        if not apply:
            target = live_dir.parent / f"{live_dir.name}_migrated"
            if target.exists():
                shutil.rmtree(target)
            return {"mode": "dry-run", "target": str(target), **await migrate(store, target)}

        # apply: back up the db and the existing page dir, then write fresh.
        if db_path.exists():
            shutil.copy2(db_path, db_path.with_suffix(f".db.premigrate-{stamp}.bak"))
        if live_dir.exists():
            backup = live_dir.parent / f"{live_dir.name}.bak-{stamp}"
            shutil.copytree(live_dir, backup)
            shutil.rmtree(live_dir)
        return {"mode": "apply", "db_backup": str(db_path) + f".premigrate-{stamp}.bak", **await migrate(store, live_dir)}
    finally:
        await store.close()


if __name__ == "__main__":
    import asyncio
    import sys

    apply = "--apply" in sys.argv
    print(asyncio.run(migrate_live(apply=apply)))
