"""RecordStore — the atomic memory unit.

Free-form, self-contained records (typed by function, not subject) in one
simple table. Scope is visibility metadata, not a hierarchy/graph: read paths
can filter to global + current project/session/integration scopes. Lexical
retrieval via local `records_fts`; semantic retrieval via the shared
`SearchIndex` (`source="record"`). Hybrid `search` fuses the two legs with
`rrf_merge`, exactly like transcript search, then post-filters by kinds,
scopes, and supersession.

`scope_kind`/`scope_key` are intentionally boring columns. They prevent global
memory pollution without introducing project partitions or a derivation DAG.

No bitemporal axis. Freshness = `last_confirmed_at`; `superseded_by` closes a
lineage (never hard-delete on supersede). `pinned` survives decay.

Open-vocabulary LABELS (referents AND categories, attached by the curator at
write time) live in `record_labels`. Merge unions labels onto the survivor,
supersede_with passes them to the successor, delete cascades them.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from arden.constants import RRF_K
from arden.database import connect as db_connect
from arden.database import serialize_embedding
from arden.logging import get_logger
from arden.memory.models import Kind, Record, SourceRef, now_iso
from arden.search.fts import build_fts_or_query
from arden.search.retrieval import rrf_merge

_logger = get_logger(__name__)

if TYPE_CHECKING:
    from pathlib import Path

# scope_kind/scope_key remain NULLABLE provenance columns. The 753 seeded rows
# wrote them NOT NULL; new rows write them only when source_ref carries them.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'note',
    scope_kind TEXT,
    scope_key TEXT,
    created_at TEXT NOT NULL,
    last_confirmed_at TEXT NOT NULL,
    superseded_by TEXT,
    pinned INTEGER NOT NULL DEFAULT 0,
    source_ref TEXT
);
CREATE INDEX IF NOT EXISTS idx_records_active ON records(superseded_by);
CREATE INDEX IF NOT EXISTS idx_records_confirmed ON records(last_confirmed_at);

CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
    text,
    content='records',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON records BEGIN
    INSERT INTO records_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON records BEGIN
    INSERT INTO records_fts(records_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS records_au AFTER UPDATE ON records BEGIN
    INSERT INTO records_fts(records_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
    INSERT INTO records_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TABLE IF NOT EXISTS record_labels (
    record_id TEXT NOT NULL,
    label TEXT NOT NULL,
    label_kind TEXT NOT NULL DEFAULT 'meta',
    created_at TEXT NOT NULL,
    PRIMARY KEY (record_id, label)
);
CREATE INDEX IF NOT EXISTS idx_labels_label ON record_labels(label);

"""

# The v2 `records` table declared scope_kind NOT NULL. Flat records write it NULL
# (scope is provenance inside source_ref now). CREATE TABLE IF NOT EXISTS can't
# relax an existing column, so rebuild the table once. rowids are preserved so the
# external-content FTS stays mapped; we rebuild it afterwards regardless.
_MIGRATE_NULLABLE_SCOPE = """
BEGIN;
CREATE TABLE records_new (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'note',
    scope_kind TEXT,
    scope_key TEXT,
    created_at TEXT NOT NULL,
    last_confirmed_at TEXT NOT NULL,
    superseded_by TEXT,
    pinned INTEGER NOT NULL DEFAULT 0,
    source_ref TEXT
);
INSERT INTO records_new
    (rowid, id, text, kind, scope_kind, scope_key, created_at,
     last_confirmed_at, superseded_by, pinned, source_ref)
SELECT rowid, id, text, kind, scope_kind, scope_key, created_at,
       last_confirmed_at, superseded_by, pinned, source_ref FROM records;
DROP TABLE records;
ALTER TABLE records_new RENAME TO records;
CREATE INDEX IF NOT EXISTS idx_records_active ON records(superseded_by);
CREATE INDEX IF NOT EXISTS idx_records_confirmed ON records(last_confirmed_at);
CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON records BEGIN
    INSERT INTO records_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON records BEGIN
    INSERT INTO records_fts(records_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS records_au AFTER UPDATE ON records BEGIN
    INSERT INTO records_fts(records_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
    INSERT INTO records_fts(rowid, text) VALUES (new.rowid, new.text);
END;
COMMIT;
"""


class RecordStore:
    def __init__(self, db_path: Path, search_index: object | None = None) -> None:
        self._db_path = db_path
        self._search_index = search_index  # may be None -> FTS-only
        self._conn = None
        self._conn_lock = asyncio.Lock()
        self._bg: set[asyncio.Task] = set()  # tracked index tasks (no GC, errors logged)

    def attach_search_index(self, search_index: object | None) -> None:
        """Re-wire the vector index after a runtime embedding toggle (mirrors the
        transcript store). Idempotent; None reverts to FTS-only."""
        self._search_index = search_index

    def _track(self, coro) -> None:
        """Fire-and-forget an index op, but keep a ref (no GC) and log failures —
        a swallowed embed error otherwise leaves a record FTS-findable yet
        semantically invisible with zero diagnostics."""
        task = asyncio.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(
            lambda t: (
                self._bg.discard(t),
                None
                if t.cancelled() or not t.exception()
                else _logger.warning("record index task failed", exc_info=t.exception()),
            )
        )

    # -- connection (mirrors curator._ensure_conn) ---------------------------

    async def _ensure_conn(self):
        if self._conn is not None:
            return self._conn
        async with self._conn_lock:
            if self._conn is None:
                conn = await db_connect(self._db_path)
                await conn.executescript(_SCHEMA)
                await conn.commit()
                await self._migrate_nullable_scope(conn)
                # Derivation columns (additive; existing DBs predate them). After
                # the scope rebuild so a rebuilt table gains them too.
                for col in (
                    "provenance TEXT NOT NULL DEFAULT 'recorded'",
                    "standing TEXT NOT NULL DEFAULT 'active'",
                    "depth INTEGER NOT NULL DEFAULT 0",
                ):
                    try:
                        await conn.execute(f"ALTER TABLE records ADD COLUMN {col}")
                    except Exception:
                        pass  # column already present
                # label_kind column (additive; existing DBs predate it)
                try:
                    await conn.execute("ALTER TABLE record_labels ADD COLUMN label_kind TEXT NOT NULL DEFAULT 'meta'")
                except Exception:
                    pass  # column already present
                try:
                    await conn.execute("CREATE INDEX IF NOT EXISTS idx_labels_kind ON record_labels(label_kind)")
                except Exception:
                    pass
                await conn.commit()
                self._conn = conn
        return self._conn

    async def _migrate_nullable_scope(self, conn) -> None:
        cur = await conn.execute("PRAGMA table_info(records)")
        cols = await cur.fetchall()
        scope = next((c for c in cols if c[1] == "scope_kind"), None)
        if scope is None or scope[3] == 0:  # col[3] = notnull flag; 0 = already nullable
            return
        _logger.info("migrating records.scope_kind NOT NULL -> nullable (table rebuild)")
        await conn.executescript(_MIGRATE_NULLABLE_SCOPE)
        await conn.execute("INSERT INTO records_fts(records_fts) VALUES('rebuild')")
        await conn.commit()

    async def open(self) -> None:
        """Eagerly connect + apply the one-time scope_kind rebuild. Called at
        startup so the migration's DROP/ALTER runs strictly serially, before the
        lens/consolidate/curator connections to the same memory.db open — a
        concurrent first writer would otherwise contend with the rebuild's lock."""
        await self._ensure_conn()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # -- writes --------------------------------------------------------------

    async def add(
        self,
        text: str,
        *,
        kind: str = Kind.FACT,
        pinned: bool = False,
        source_ref: SourceRef | None = None,
        scope_kind: str | None = None,
        scope_key: str | None = None,
    ) -> Record:
        from uuid import uuid4

        record = Record(
            id=uuid4().hex,
            text=text,
            kind=kind,
            pinned=pinned,
            scope_kind=scope_kind,
            scope_key=scope_key,
            source_ref=source_ref,
        )
        conn = await self._ensure_conn()
        await conn.execute(
            """
            INSERT INTO records
                (id, text, kind, scope_kind, scope_key, created_at,
                 last_confirmed_at, superseded_by, pinned, source_ref)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.text,
                record.kind,
                record.scope_kind,
                record.scope_key,
                record.created_at,
                record.last_confirmed_at,
                record.superseded_by,
                1 if record.pinned else 0,
                json.dumps(source_ref.to_dict()) if source_ref else None,
            ),
        )
        await conn.commit()
        self._index(record)
        return record

    async def supersede(self, old_id: str, new_id: str) -> bool:
        """Close a lineage: old.superseded_by = new_id. Evicts the old record
        from the vector index (the row stays in SQLite for history but must not
        keep occupying retrieval slots). Returns whether the old id existed."""
        conn = await self._ensure_conn()
        cur = await conn.execute("UPDATE records SET superseded_by = ? WHERE id = ?", (new_id, old_id))
        await conn.commit()
        await self._unindex(old_id)
        return cur.rowcount > 0

    async def supersede_with(
        self,
        old_id: str,
        *,
        text: str,
        kind: str = Kind.FACT,
        source_ref: SourceRef | None = None,
        scope_kind: str | None = None,
        scope_key: str | None = None,
    ) -> Record:
        """Insert a replacement AND close the old lineage in ONE transaction, so a
        mid-op failure can't strand a half-applied SUPERSEDE (orphan duplicate).
        The successor inherits the old record's labels (the caller may then set
        more). If `old_id` doesn't exist this is just an ADD (the correction
        still lands)."""
        from uuid import uuid4

        record = Record(
            id=uuid4().hex, text=text, kind=kind, scope_kind=scope_kind, scope_key=scope_key, source_ref=source_ref
        )
        conn = await self._ensure_conn()
        await conn.execute(
            """
            INSERT INTO records
                (id, text, kind, scope_kind, scope_key, created_at,
                 last_confirmed_at, superseded_by, pinned, source_ref)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 0, ?)
            """,
            (
                record.id,
                record.text,
                record.kind,
                record.scope_kind,
                record.scope_key,
                record.created_at,
                record.last_confirmed_at,
                json.dumps(source_ref.to_dict()) if source_ref else None,
            ),
        )
        cur = await conn.execute("UPDATE records SET superseded_by = ? WHERE id = ?", (record.id, old_id))
        await conn.execute(
            "INSERT OR IGNORE INTO record_labels (record_id, label, label_kind, created_at) "
            "SELECT ?, label, label_kind, created_at FROM record_labels WHERE record_id = ?",
            (record.id, old_id),
        )
        await conn.commit()
        self._index(record)
        await self._unindex(old_id)
        if cur.rowcount == 0:
            _logger.warning("supersede target id not found; landed as ADD", old_id=old_id)
        return record

    async def set_kind(self, record_id: str, kind: str) -> bool:
        """Reclassify a record's function-type (consolidation's retype op)."""
        conn = await self._ensure_conn()
        cur = await conn.execute("UPDATE records SET kind = ? WHERE id = ?", (kind, record_id))
        await conn.commit()
        return cur.rowcount > 0

    async def merge(
        self,
        survivor_id: str,
        loser_ids: list[str],
        *,
        text: str | None = None,
        kind: str | None = None,
    ) -> Record | None:
        """Collapse N records into ONE in a single transaction (the consolidate
        primitive). Each loser's `superseded_by` is pointed at the survivor and
        evicted from the vector index; the survivor's labels become the union of
        all members' labels. If `text` is given the survivor is re-texted (and
        re-confirmed) in the same transaction. Aborts (returns None) if the
        survivor or any loser is pinned — pinned records are never merged away.
        Returns the (re-fetched) survivor."""
        conn = await self._ensure_conn()
        survivor = await self.get(survivor_id)
        if survivor is None or survivor.pinned:
            return None
        losers: list[Record] = []
        for lid in loser_ids:
            if lid == survivor_id:
                continue
            loser = await self.get(lid)
            if loser is None:
                continue
            if loser.pinned:
                return None  # never merge a pinned record away
            losers.append(loser)
        if not losers:
            return survivor

        new_text = (text or "").strip() or None
        if new_text and new_text != survivor.text:
            await conn.execute(
                "UPDATE records SET text = ?, last_confirmed_at = ? WHERE id = ?",
                (new_text, now_iso(), survivor_id),
            )
        if kind and kind != survivor.kind:
            await conn.execute("UPDATE records SET kind = ? WHERE id = ?", (kind, survivor_id))
        for loser in losers:
            await conn.execute(
                "UPDATE records SET superseded_by = ? WHERE id = ?",
                (survivor_id, loser.id),
            )
            await conn.execute(
                "INSERT OR IGNORE INTO record_labels (record_id, label, label_kind, created_at) "
                "SELECT ?, label, label_kind, created_at FROM record_labels WHERE record_id = ?",
                (survivor_id, loser.id),
            )
        await conn.commit()

        for loser in losers:
            await self._unindex(loser.id)
        merged = await self.get(survivor_id)
        if merged is not None and new_text:
            self._index(merged)  # re-embed the re-texted survivor
        return merged

    async def confirm(self, record_id: str) -> bool:
        conn = await self._ensure_conn()
        cur = await conn.execute("UPDATE records SET last_confirmed_at = ? WHERE id = ?", (now_iso(), record_id))
        await conn.commit()
        return cur.rowcount > 0

    async def set_pinned(self, record_id: str, pinned: bool) -> bool:
        conn = await self._ensure_conn()
        cur = await conn.execute("UPDATE records SET pinned = ? WHERE id = ?", (1 if pinned else 0, record_id))
        await conn.commit()
        return cur.rowcount > 0

    async def update(self, record_id: str, text: str) -> bool:
        conn = await self._ensure_conn()
        cur = await conn.execute(
            "UPDATE records SET text = ?, last_confirmed_at = ? WHERE id = ?",
            (text, now_iso(), record_id),
        )
        await conn.commit()
        if cur.rowcount == 0:
            return False
        record = await self.get(record_id)
        if record is not None:
            self._index(record)
        return True

    async def delete(self, record_id: str) -> None:
        conn = await self._ensure_conn()
        await conn.execute("DELETE FROM record_labels WHERE record_id = ?", (record_id,))
        await conn.execute("DELETE FROM records WHERE id = ?", (record_id,))
        await conn.commit()
        await self._unindex(record_id)  # inline (cheap, pure-DB) — no upsert-vs-delete race

    async def _unindex(self, record_id: str) -> None:
        if self._search_index is not None:
            try:
                await self._search_index.delete("record", record_id)
            except Exception:
                _logger.warning("record index delete failed", exc_info=True)

    async def prune(self) -> dict[str, int]:
        """LINT: deterministic structural hygiene. Hard-deletes superseded records
        (the chosen tombstone policy — records carry no archived status), drops the
        labels those deletes orphan, and reconciles the vector index so recall can't
        surface dead content. Idempotent: a clean store prunes nothing. Pinned
        records are never superseded, so they are never touched here. FTS stays
        consistent via the AFTER DELETE trigger."""
        conn = await self._ensure_conn()
        rows = await conn.execute_fetchall("SELECT id FROM records WHERE superseded_by IS NOT NULL")
        dead = len(rows)
        cur = await conn.execute(
            "DELETE FROM record_labels WHERE record_id IN (SELECT id FROM records WHERE superseded_by IS NOT NULL)"
        )
        labels = cur.rowcount
        await conn.execute("DELETE FROM records WHERE superseded_by IS NOT NULL")
        # Belt: labels whose record no longer exists at all (pre-existing orphans).
        cur = await conn.execute("DELETE FROM record_labels WHERE record_id NOT IN (SELECT id FROM records)")
        labels += cur.rowcount
        await conn.commit()
        vectors = await self._reconcile_vectors()
        return {"records": dead, "labels": labels, "vectors": vectors}

    async def wipe_except_pinned(self) -> dict[str, int]:
        """/init re-derivation primitive: hard-delete every non-pinned record (and
        its labels), keeping only pinned survivors. The AFTER DELETE trigger keeps
        records_fts consistent; `_reconcile_vectors` then drops the vectors whose
        record id is no longer live. Returns counts of victims/survivors/vectors."""
        conn = await self._ensure_conn()
        rows = await conn.execute_fetchall("SELECT COUNT(*) AS n FROM records WHERE pinned = 0")
        victims = rows[0]["n"] if rows else 0
        rows = await conn.execute_fetchall("SELECT COUNT(*) AS n FROM records WHERE pinned = 1")
        survivors = rows[0]["n"] if rows else 0
        await conn.execute("DELETE FROM record_labels WHERE record_id IN (SELECT id FROM records WHERE pinned = 0)")
        await conn.execute("DELETE FROM records WHERE pinned = 0")
        await conn.commit()
        vectors = await self._reconcile_vectors()
        return {"deleted": victims, "kept_pinned": survivors, "vectors": vectors}

    async def _reconcile_vectors(self) -> int:
        """Drop every 'record'-source vector whose source_id is no longer a live
        record — covers vectors left behind by the supersede→delete path and any
        pre-existing orphans. No-op when no vector index is wired."""
        index = self._search_index
        if index is None:
            return 0
        try:
            indexed = await index.store.get_indexed_hashes("record")  # {source_id: (rowid, hash)}
        except Exception:
            _logger.warning("vector reconcile: enumerate failed", exc_info=True)
            return 0
        conn = await self._ensure_conn()
        live = {r["id"] for r in await conn.execute_fetchall("SELECT id FROM records")}
        dropped = 0
        for source_id in indexed:
            if source_id not in live:
                try:
                    await index.delete("record", source_id)
                    dropped += 1
                except Exception:
                    _logger.warning("vector reconcile: delete failed", exc_info=True)
        return dropped

    # -- labels (open-vocabulary, attached at write time by the curator) ------

    async def set_labels(
        self,
        record_id: str,
        labels: list[str],
        *,
        entity_labels: list[str] | None = None,
    ) -> None:
        """Replace the record's labels wholesale (the curator's UPDATE semantics).

        `labels` is the legacy flat list (stored as kind='meta').
        `entity_labels` are stored as kind='entity'; when provided, the full set
        written is entity_labels (kind='entity') + labels (kind='meta').
        When only `labels` is given (legacy callers), all are stored as 'meta'.
        """
        conn = await self._ensure_conn()
        await conn.execute("DELETE FROM record_labels WHERE record_id = ?", (record_id,))
        ts = now_iso()
        rows: list[tuple] = [(record_id, lbl, "meta", ts) for lbl in labels]
        if entity_labels:
            rows += [(record_id, lbl, "entity", ts) for lbl in entity_labels if lbl not in labels]
        await conn.executemany(
            "INSERT OR IGNORE INTO record_labels (record_id, label, label_kind, created_at) VALUES (?, ?, ?, ?)",
            rows,
        )
        await conn.commit()

    async def add_labels(
        self,
        record_id: str,
        labels: list[str],
        *,
        entity_labels: list[str] | None = None,
    ) -> None:
        """Union new labels onto the record (backfill / lens promotion)."""
        if not labels and not entity_labels:
            return
        conn = await self._ensure_conn()
        ts = now_iso()
        rows: list[tuple] = [(record_id, lbl, "meta", ts) for lbl in labels]
        if entity_labels:
            rows += [(record_id, lbl, "entity", ts) for lbl in entity_labels]
        await conn.executemany(
            "INSERT OR IGNORE INTO record_labels (record_id, label, label_kind, created_at) VALUES (?, ?, ?, ?)",
            rows,
        )
        await conn.commit()

    async def labels_of(self, record_id: str) -> list[str]:
        conn = await self._ensure_conn()
        rows = await conn.execute_fetchall(
            "SELECT label FROM record_labels WHERE record_id = ? ORDER BY label",
            (record_id,),
        )
        return [row["label"] for row in rows]

    async def labels_for(self, record_ids: list[str], *, include_kind: bool = False) -> dict[str, list[str]]:
        """Batch-hydrate labels for many records in ONE query (no N+1). Every
        requested id is a key; unlabeled records map to [].

        When `include_kind=True`, returns dicts instead of strings under each key
        but that mode is reserved for artifact projection use; the default flat
        list is unchanged so callers don't break.
        """
        if not record_ids:
            return {}
        conn = await self._ensure_conn()
        rows = await conn.execute_fetchall(
            f"SELECT record_id, label, label_kind FROM record_labels "
            f"WHERE record_id IN ({','.join('?' * len(record_ids))}) ORDER BY label",
            tuple(record_ids),
        )
        if include_kind:
            # Return dict[record_id, list[dict(label, kind)]] — typed for artifacts
            out_typed: dict[str, list[dict]] = {rid: [] for rid in record_ids}
            for row in rows:
                out_typed[row["record_id"]].append({"label": row["label"], "kind": row["label_kind"]})
            return out_typed  # type: ignore[return-value]
        out: dict[str, list[str]] = {rid: [] for rid in record_ids}
        for row in rows:
            out[row["record_id"]].append(row["label"])
        return out

    async def records_for_label(self, label: str, *, limit: int = 200) -> list[Record]:
        """The "everything about X" page: active records carrying the label,
        newest-confirmed first."""
        conn = await self._ensure_conn()
        rows = await conn.execute_fetchall(
            "SELECT records.* FROM records "
            "JOIN record_labels ON record_labels.record_id = records.id "
            "WHERE record_labels.label = ? AND records.superseded_by IS NULL "
            "ORDER BY records.last_confirmed_at DESC LIMIT ?",
            (label, limit),
        )
        return [self._row_to_record(row) for row in rows]

    async def list_labels(self) -> list[dict]:
        """[{"label", "count", "kind"}] over ACTIVE records, biggest hubs first.

        PK is (record_id, label) so label_kind is uniform per label; MAX() is exact.
        """
        conn = await self._ensure_conn()
        rows = await conn.execute_fetchall(
            "SELECT record_labels.label AS label, COUNT(*) AS count, "
            "MAX(record_labels.label_kind) AS kind FROM record_labels "
            "JOIN records ON records.id = record_labels.record_id "
            "WHERE records.superseded_by IS NULL "
            "GROUP BY record_labels.label ORDER BY count DESC, label ASC"
        )
        return [{"label": row["label"], "count": row["count"], "kind": row["kind"]} for row in rows]

    async def rename_label(self, old: str, new: str) -> None:
        """Lint canonicalization: fold `old` into `new` with union semantics — a
        record already carrying both keeps one row."""
        conn = await self._ensure_conn()
        await conn.execute(
            "INSERT OR IGNORE INTO record_labels (record_id, label, label_kind, created_at) "
            "SELECT record_id, ?, label_kind, created_at FROM record_labels WHERE label = ?",
            (new, old),
        )
        await conn.execute("DELETE FROM record_labels WHERE label = ?", (old,))
        await conn.commit()

    async def set_label_kind(self, label: str, kind: str) -> int:
        """Retype every occurrence of `label` to `kind` ('entity'|'meta'). One
        UPDATE — PK is (record_id, label), so label_kind is uniform per label and
        no per-record fan-out or duplicate-key risk applies. Idempotent."""
        conn = await self._ensure_conn()
        cur = await conn.execute("UPDATE record_labels SET label_kind = ? WHERE label = ?", (kind, label))
        await conn.commit()
        return cur.rowcount

    # -- reads ---------------------------------------------------------------

    async def get(self, record_id: str) -> Record | None:
        conn = await self._ensure_conn()
        rows = await conn.execute_fetchall("SELECT * FROM records WHERE id = ?", (record_id,))
        return self._row_to_record(rows[0]) if rows else None

    async def search(
        self,
        query: str,
        *,
        kinds: list[str] | None = None,
        limit: int = 10,
        include_superseded: bool = False,
        scopes: list[tuple[str | None, str | None]] | None = None,
    ) -> list[Record]:
        """Hybrid FTS (records_fts) ⊕ vector (SearchIndex, source="record")
        fused with rrf_merge over the WHOLE flat pool, then post-filtered by
        kinds/supersession. Bridges the two databases in Python on the record id."""
        conn = await self._ensure_conn()
        window = max(limit * 8, 80) if scopes is not None else max(limit * 4, 40)

        def _matches(record: Record) -> bool:
            if not include_superseded and record.superseded_by is not None:
                return False
            if kinds is not None and record.kind not in kinds:
                return False
            if scopes is not None:
                record_scope = (record.scope_kind, record.scope_key)
                if record_scope not in scopes and not (
                    record.scope_kind is None and record.scope_key is None and ("global", None) in scopes
                ):
                    return False
            return True

        # FTS leg (local records_fts in memory.db) — kind/active in SQL.
        fts_ranked: list[tuple[str, float]] = []
        fts_query = build_fts_or_query(query)
        if fts_query:
            where = ["records_fts MATCH ?"]
            params: list = [fts_query]
            if not include_superseded:
                where.append("records.superseded_by IS NULL")
            if kinds:
                where.append(f"records.kind IN ({','.join('?' * len(kinds))})")
                params += list(kinds)
            if scopes is not None:
                if not scopes:
                    return []
                parts = []
                for sk, sv in scopes:
                    if sk == "global" and sv is None:
                        parts.append(
                            "((records.scope_kind IS ? AND records.scope_key IS ?) OR (records.scope_kind IS NULL AND records.scope_key IS NULL))"
                        )
                    else:
                        parts.append("(records.scope_kind IS ? AND records.scope_key IS ?)")
                    params.extend([sk, sv])
                where.append("(" + " OR ".join(parts) + ")")
            params.append(window)
            try:
                rows = await conn.execute_fetchall(
                    f"SELECT records.id AS id FROM records_fts "
                    f"JOIN records ON records_fts.rowid = records.rowid "
                    f"WHERE {' AND '.join(where)} ORDER BY records_fts.rank LIMIT ?",
                    tuple(params),
                )
                fts_ranked = [(row["id"], 1.0) for row in rows]
            except Exception:
                _logger.warning("record FTS search failed; vector-only", exc_info=True)

        # Vector leg (search.db) — kind filtered on metadata; superseded records
        # are already evicted from the index on supersede/delete.
        vec_ranked: list[tuple[str, float]] = []
        index = self._search_index
        if index is not None:
            try:
                emb = await index.embedder.embed_one(query)
                raw = await index.store.vector_search(serialize_embedding(emb), sources=["record"], limit=window)
                for item_id, score in raw:
                    item = await index.store.get_by_id(item_id)
                    meta = item.metadata if item and item.metadata else None
                    if not meta or "record_id" not in meta:
                        continue
                    if kinds is not None and meta.get("kind") not in kinds:
                        continue
                    vec_ranked.append((meta["record_id"], score))
            except Exception:
                _logger.warning("record vector search failed; FTS-only", exc_info=True)

        fused = rrf_merge([fts_ranked, vec_ranked], k=RRF_K)
        ordered = sorted(fused, key=lambda rid: fused[rid], reverse=True)
        if not ordered:
            return []

        # Batch-hydrate (one query, not N+1), preserve rank order, re-apply the
        # filters as a safety net (covers any lingering index/SQL skew).
        rows = await conn.execute_fetchall(
            f"SELECT * FROM records WHERE id IN ({','.join('?' * len(ordered))})",
            tuple(ordered),
        )
        by_id = {r["id"]: self._row_to_record(r) for r in rows}
        out: list[Record] = []
        for rid in ordered:
            record = by_id.get(rid)
            if record is not None and _matches(record):
                out.append(record)
                if len(out) >= limit:
                    break
        return out

    async def list(
        self,
        *,
        pinned_only: bool = False,
        include_superseded: bool = False,
        limit: int | None = 50,
        offset: int = 0,
        scopes: list[tuple[str | None, str | None]] | None = None,
        kinds: list[str] | None = None,
    ) -> list[Record]:
        """The flat Records list — active records (newest-confirmed first) by
        default; `include_superseded` widens it to the whole lineage for the
        admin claim browser's "all statuses" view. `limit=None` is the explicit
        unbounded export/rebuild path and still returns active-only records
        unless `include_superseded=True` is supplied."""
        conn = await self._ensure_conn()
        if scopes == []:
            return []
        where = [] if include_superseded else ["superseded_by IS NULL"]
        params: list = []
        if scopes is not None:
            parts = []
            for sk, sv in scopes:
                if sk == "global" and sv is None:
                    parts.append("((scope_kind IS ? AND scope_key IS ?) OR (scope_kind IS NULL AND scope_key IS NULL))")
                else:
                    parts.append("(scope_kind IS ? AND scope_key IS ?)")
                params.extend([sk, sv])
            where.append("(" + " OR ".join(parts) + ")")
        if kinds:
            where.append(f"kind IN ({','.join('?' * len(kinds))})")
            params.extend(kinds)
        if pinned_only:
            where.append("pinned = 1")
        sql = "SELECT * FROM records"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY last_confirmed_at DESC"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        rows = await conn.execute_fetchall(sql, tuple(params))
        return [self._row_to_record(row) for row in rows]

    async def count_active(self) -> int:
        """Size of the active pool — the lens-coverage denominator (scope_pool)."""
        conn = await self._ensure_conn()
        rows = await conn.execute_fetchall("SELECT COUNT(*) AS n FROM records WHERE superseded_by IS NULL")
        return rows[0]["n"] if rows else 0

    async def neighborhood(self, record: Record, *, limit: int = 8) -> list[Record]:
        """The active records that lexically/semantically resemble `record`
        (hybrid recall), minus the record itself — its consolidation neighborhood.
        Thin wrapper over `search`, which already excludes superseded records and
        degrades to a single available leg when the other is down."""
        hits = await self.search(record.text, limit=limit + 1, include_superseded=False)
        return [h for h in hits if h.id != record.id][:limit]

    async def updated_since(self, watermark: str | None, *, limit: int) -> list[Record]:
        """Active records confirmed at/after the watermark, oldest-confirmed first
        (the O(delta) consolidation candidate set). `>=`-inclusive so a tie-group
        sharing the boundary timestamp is never skipped; `None` returns the whole
        active pool oldest-first."""
        conn = await self._ensure_conn()
        sql = "SELECT * FROM records WHERE superseded_by IS NULL"
        params: list = []
        if watermark is not None:
            sql += " AND last_confirmed_at >= ?"
            params.append(watermark)
        sql += " ORDER BY last_confirmed_at ASC LIMIT ?"
        params.append(limit)
        rows = await conn.execute_fetchall(sql, tuple(params))
        return [self._row_to_record(row) for row in rows]

    # -- internals -----------------------------------------------------------

    def _index(self, record: Record) -> None:
        """Fire-and-forget vector index (mirror the transcript hook). The
        SearchIndex embeds unconditionally on upsert — no EMBED_SOURCES gate."""
        if self._search_index is not None and record.text.strip():
            self._track(
                self._search_index.upsert(
                    source="record",
                    source_id=record.id,
                    title=f"{record.kind} record",
                    content=record.text,
                    metadata={"record_id": record.id, "kind": record.kind},
                )
            )

    @staticmethod
    def _row_to_record(row) -> Record:
        return Record(
            id=row["id"],
            text=row["text"],
            kind=row["kind"],
            created_at=row["created_at"],
            last_confirmed_at=row["last_confirmed_at"],
            superseded_by=row["superseded_by"],
            scope_kind=row["scope_kind"],
            scope_key=row["scope_key"],
            pinned=bool(row["pinned"]),
            source_ref=SourceRef.from_dict(json.loads(row["source_ref"])) if row["source_ref"] else None,
        )
