"""Consolidate/Lint — the background pass that turns the raw record pile into a
SMALL, CLEAN, CURRENT body. THIS is the memory: records alone are raw and uncurated.

A periodic health-check of the active record pool. The only stage that removes
records from circulation: MERGE duplicates onto one survivor, SUPERSEDE
stale/contradicted records into a newer one, DROP genuine orphans. It never
authors a new fact and never raises trust. Each sweep also runs ONE bounded
label-hygiene call over the whole label vocabulary, folding near-duplicate
names (case/synonym variants) via rename_label.

Demote/merge-only. Candidate selection scans the active durable pool, but each
record's recall neighborhood is judged (one cheap LLM call) only when its content
fingerprint CHANGED since its last clean judge — cached in the consolidate meta DB.
So a clean night is nearly free and cost scales with what changed, not the corpus
size. Pinned records are inviolable. With no LLM configured, the pass is a no-op.

Adapted from the deleted claim-pipeline `consolidate.py`: scopes,
provenance/feedback/corroboration ordering, CONTRADICTS/SUPERSEDES edges, and the
high-trust flag path are all stripped — records are flat, edge-less, single-axis.
"""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ntrp.database import connect as db_connect
from ntrp.logging import get_logger
from ntrp.memory.models import Record, SourceRef
from ntrp.memory.prompts_consolidate import (
    LABEL_HYGIENE_RUBRIC,
    LINT_RUBRIC,
    DropOrphanOp,
    InvalidateOp,
    LabelOps,
    LintOps,
    MergeOp,
    RetypeOp,
)
from ntrp.memory.records import RecordStore
from ntrp.observability import observed_trace

_logger = get_logger(__name__)

# Function-types a consolidation op may assign (guard against a hallucinated kind).
_KINDS = {"directive", "fact", "source"}

NEIGHBORHOOD_LIMIT = 8
# Legacy `observation` lines (retired kind) are never judged. Lessons ARE judged,
# but only against other lessons: a lesson may merge with / supersede a lesson,
# never cross the boundary into durable facts (that would launder agent-inferred
# rules into user-grade knowledge) — enforced by kind-partitioned neighborhoods
# plus the retype guard.
_SKIP_KINDS = {"observation"}
LABEL_FINGERPRINT_KEY = "consolidate_label_fingerprint"
JUDGES_PER_SWEEP = 200  # cap LLM judgments/run; changed hoods beyond it roll to the next run


def _hood_fingerprint(hood: list[Record]) -> str:
    """Content hash of a neighborhood. A clean judge is cached by this fingerprint so an
    unchanged hood is never re-judged; any text edit, new neighbor, member removal, PIN,
    or re-confirmation (all of which the judge sees + acts on) changes the hash and
    re-judges it."""
    payload = "\n".join(
        f"{m.id}:{int(m.pinned)}:{m.last_confirmed_at}:{m.text}" for m in sorted(hood, key=lambda r: r.id)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class ConsolidateReport:
    merged: int = 0  # records folded onto a survivor
    retyped: int = 0  # records reclassified to their function-type (note -> fact/...)
    superseded: int = 0  # stale/contradicted records closed (incl. deleted orphans/stale)
    dropped: int = 0  # orphans removed
    relabeled: int = 0  # label spellings folded into a canonical name
    reclassified: int = 0  # labels retyped between entity/meta by the hygiene pass
    pruned: int = 0  # superseded tombstones hard-deleted by the LINT pass

    @property
    def summary_counts(self) -> dict[str, int]:
        return {
            "merged": self.merged,
            "superseded": self.superseded,
            "dropped": self.dropped,
            "retyped": self.retyped,
            "relabeled": self.relabeled,
            "reclassified": self.reclassified,
            "pruned": self.pruned,
        }

    @property
    def mutating_count(self) -> int:
        return (
            self.merged
            + self.retyped
            + self.superseded
            + self.dropped
            + self.relabeled
            + self.reclassified
            + self.pruned
        )

    @property
    def changed_memory(self) -> bool:
        return self.mutating_count > 0


class Consolidate:
    def __init__(
        self,
        records: RecordStore,
        llm,  # completion client (config.memory_model); None -> no-op
        *,
        model: str | None,
        db_path: Path,  # config.memory_db_path — shares the Curator's meta table
        reasoning_effort: str | None = None,
    ) -> None:
        self._records = records
        self._llm = llm
        self._model = model
        self._db_path = db_path
        self._reasoning_effort = reasoning_effort
        self._conn = None
        self._conn_lock = asyncio.Lock()

    # --- one sweep ---------------------------------------------------------

    @observed_trace("memory.consolidate", tags="memory")
    async def run_once(self) -> ConsolidateReport:
        """One sweep. Candidate selection scans the active durable pool, but a
        neighborhood whose content fingerprint is unchanged since its last clean judge
        is SKIPPED (no LLM) — so a clean night is nearly free and cost scales with what
        actually CHANGED, not the corpus size. Up to JUDGES_PER_SWEEP hoods are judged
        per run; any changed remainder rolls to the next run. No-op when no LLM."""
        report = ConsolidateReport()
        if self._llm is None or not self._model:
            return report

        delta = await self._select_delta()
        hoods = await self._build_neighborhoods(delta) if delta else []
        cached = await self._read_fingerprints()
        judged = 0
        for hood in hoods:
            seed = hood[0]
            fp = _hood_fingerprint(hood)
            if cached.get(seed.id) == fp:
                continue  # unchanged since the last clean judge — skip the LLM
            if judged >= JUDGES_PER_SWEEP:
                break  # cost cap; the remaining changed hoods are re-selected next run
            judged += 1
            ops = await self._judge(hood)
            await self._write_fingerprint(seed.id, fp)  # this exact hood has now been judged
            if ops is not None:
                await self._apply(ops, hood, report)

        # Prune against the TRUE active pool, not the (judge-capped) delta, so a large
        # corpus doesn't wrongly evict + re-judge fingerprints below the cap.
        active_ids = {r.id for r in await self._records.list(limit=None, scopes=None)}
        await self._prune_fingerprints(active_ids)
        await self._sync_label_hygiene(report, force=bool(judged))
        report.pruned = (await self._records.prune())["records"]
        return report

    @observed_trace("memory.labels", tags="memory")
    async def lint_labels_once(self) -> ConsolidateReport:
        """Standalone vocabulary pass: classify/rename labels (one LLM call) +
        prune, WITHOUT the per-neighborhood merge sweep. Run on startup so a fresh
        deploy classifies the cold-start label backlog into entity dossiers right
        away instead of waiting for the daily consolidation."""
        report = ConsolidateReport()
        if self._llm is None or not self._model:
            return report
        await self._sync_label_hygiene(report, force=True)
        report.pruned = (await self._records.prune())["records"]
        return report

    # --- candidate selection (bounded) ------------------------------------

    async def _select_delta(self) -> list[Record]:
        """The active candidate pool (retired observations excluded). Unchanged
        neighborhoods are skipped via the fingerprint cache in run_once, so scanning the
        whole pool is cheap on a clean night and there is no record-count ceiling — the
        per-run cost is bounded by JUDGES_PER_SWEEP changed hoods, not the corpus size."""
        rows = await self._records.updated_since(None, limit=JUDGES_PER_SWEEP * 100)
        return [r for r in rows if r.kind not in _SKIP_KINDS]

    async def _build_neighborhoods(self, delta: list[Record]) -> list[list[Record]]:
        """One neighborhood per delta record: the record plus a handful of active
        records that resemble it (so a new record merges against an older one
        outside the delta). Kind-partitioned at the lesson boundary: a lesson hood
        holds only lessons, a durable hood only durables — dedup happens within each
        pool, never across the trust boundary. De-duped by frozen id-set so two
        delta records that recall each other aren't judged twice."""
        seen: set[frozenset[str]] = set()
        hoods: list[list[Record]] = []
        for record in delta:
            lesson_pool = record.kind == "lesson"
            members = {record.id: record}
            for hit in await self._records.neighborhood(record, limit=NEIGHBORHOOD_LIMIT):
                if hit.kind in _SKIP_KINDS:
                    continue  # retired kinds never re-enter through a merge
                if (hit.kind == "lesson") != lesson_pool:
                    continue  # lessons only ever merge with lessons
                members.setdefault(hit.id, hit)
            key = frozenset(members)
            if key in seen:
                continue
            seen.add(key)
            hoods.append(list(members.values()))
        return hoods

    # --- LLM judgment -----------------------------------------------------

    async def _judge(self, hood: list[Record]) -> LintOps | None:
        cards = "\n".join(
            json.dumps(
                {
                    "id": r.id,
                    "text": r.text,
                    "kind": r.kind,
                    "last_confirmed_at": r.last_confirmed_at,
                    "pinned": r.pinned,
                }
            )
            for r in hood
        )
        from ntrp.memory.file_store import load_conventions

        messages = [
            {"role": "system", "content": f"<operating_manual>\n{load_conventions()}\n</operating_manual>\n\n" + LINT_RUBRIC},
            {"role": "user", "content": f"NEIGHBORHOOD:\n{cards}"},
        ]
        try:
            resp = await self._llm.completion(
                messages=messages,
                model=self._model,
                reasoning_effort=self._reasoning_effort,
                response_format=LintOps,
            )
        except Exception:
            _logger.warning("consolidate judgment failed for neighborhood", exc_info=True)
            return None
        content = resp.choices[0].message.content if resp.choices else None
        if not content or not content.strip():
            return None
        try:
            return LintOps.model_validate_json(content)
        except Exception:
            _logger.warning("consolidate: unparseable judgment", exc_info=True)
            return None

    # --- apply (idempotent, demote-only) ----------------------------------

    async def _apply(self, ops: LintOps, hood: list[Record], report: ConsolidateReport) -> None:
        live = await self._reload_active(hood)
        for op in ops.merges:
            await self._apply_merge(op, live, report)
        for op in ops.retypes:
            await self._apply_retype(op, live, report)
        for op in ops.invalidations:
            await self._apply_invalidate(op, live, report)
        for op in ops.orphans:
            await self._apply_orphan(op, live, report)

    async def _reload_active(self, hood: list[Record]) -> dict[str, Record]:
        """Re-fetch each member; keep only still-active rows (the idempotency guard
        — an op the previous neighborhood already applied is silently skipped)."""
        live: dict[str, Record] = {}
        for r in hood:
            fresh = await self._records.get(r.id)
            if fresh is not None and fresh.superseded_by is None:
                live[fresh.id] = fresh
        return live

    async def _apply_merge(self, op: MergeOp, live: dict[str, Record], report: ConsolidateReport) -> None:
        members = [live[m] for m in op.member_ids if m in live]
        if len(members) < 2:
            return  # hallucinated/stale ids dropped, not dead-ended
        if any(m.pinned for m in members):
            return  # never merge a pinned record away
        survivor = self._pick_survivor(members)
        loser_ids = [m.id for m in members if m.id != survivor.id]
        # A lesson merge stays a lesson — the judge cannot promote it to a durable
        # kind on the way through (hoods are kind-partitioned, so members agree).
        kind = op.kind if (op.kind in _KINDS and survivor.kind != "lesson") else None
        # An empty/whitespace merged_text would blank the survivor + evict its vector;
        # keep the survivor's existing text in that case.
        text = op.merged_text if (op.merged_text and op.merged_text.strip()) else None
        merged = await self._records.merge(survivor.id, loser_ids, text=text, kind=kind)
        if merged is None:
            return
        report.merged += len(loser_ids)
        # Reflect the merge in our live view so a later op in the same hood
        # doesn't act on a now-superseded loser.
        for member in members:
            live.pop(member.id, None)
        live[merged.id] = merged

    async def _apply_retype(self, op: RetypeOp, live: dict[str, Record], report: ConsolidateReport) -> None:
        """Reclassify a single record's function-type (e.g. a raw 'note' that is
        actually a standing fact/preference/workflow). Pinned records are untouched."""
        target = live.get(op.record_id)
        if target is None or target.pinned:
            return
        if target.kind == "lesson":
            return  # a lesson never becomes durable knowledge (trust laundering)
        if op.kind not in _KINDS or op.kind == target.kind:
            return
        source = SourceRef(
            kind="consolidate",
            ref=f"retype:{target.id}",
            scope_kind=target.scope_kind,
            scope_key=target.scope_key,
        )
        successor = await self._records.supersede_with(
            target.id,
            text=target.text,
            kind=op.kind,
            source_ref=source,
            scope_kind=target.scope_kind,
            scope_key=target.scope_key,
        )
        report.retyped += 1
        live.pop(target.id, None)
        live[successor.id] = successor

    async def _apply_invalidate(self, op: InvalidateOp, live: dict[str, Record], report: ConsolidateReport) -> None:
        target = live.get(op.record_id)
        if target is None or target.pinned:
            return
        contra = op.contradicted_by and live.get(op.contradicted_by)
        if contra is not None and contra.id != target.id and not contra.pinned:
            # A newer record supersedes the stale one: close the lineage into it.
            await self._records.supersede(target.id, contra.id)
            report.superseded += 1
        else:
            # Plain stale invalidation with no successor: records have no archived
            # status, so a stale record with nothing to fold into is deleted.
            await self._records.delete(target.id)
            report.superseded += 1
        live.pop(target.id, None)

    async def _apply_orphan(self, op: DropOrphanOp, live: dict[str, Record], report: ConsolidateReport) -> None:
        target = live.get(op.record_id)
        if target is None or target.pinned:
            return
        # Verify orphan-hood against the live store: a record is an orphan only if
        # it has no provenance source (records carry no edges).
        if target.source_ref is not None:
            return
        await self._records.delete(target.id)
        report.dropped += 1
        live.pop(target.id, None)

    @staticmethod
    def _pick_survivor(members: list[Record]) -> Record:
        """The best-grounded survivor: pinned wins, then most-recently confirmed,
        then the longest (most complete) text."""
        return max(members, key=lambda m: (m.pinned, m.last_confirmed_at or "", len(m.text)))

    # --- label hygiene (one bounded call per sweep) -------------------------

    async def _sync_label_hygiene(self, report: ConsolidateReport, *, force: bool) -> None:
        labels = await self._records.list_labels()
        current = self._label_fingerprint(labels)
        if not force and current == await self._read_label_fingerprint():
            return
        if await self._lint_labels(report, labels=labels):
            await self._write_label_fingerprint(await self._current_label_fingerprint())

    async def _lint_labels(self, report: ConsolidateReport, labels: list[dict] | None = None) -> bool:
        """Curate the label vocabulary: ONE LLM call over the whole list_labels()
        (it is small) both folds near-duplicate names (case/synonym variants like
        "dex"/"Dex memory") via rename_label AND classifies each label as
        entity|meta so entity dossiers can be built. Delta sweeps always run it;
        idle sweeps rerun it only when the durable vocabulary fingerprint
        changed. Skipped only when the vocabulary has < 2 labels."""
        labels = labels if labels is not None else await self._records.list_labels()
        if len(labels) < 2:
            return True
        ops = await self._judge_labels(labels)
        if ops is None:
            return False
        names = {entry["label"] for entry in labels}
        # Renames first — they mutate `names`; reclass then runs against the
        # post-rename name set so a folded `old` can't be retyped.
        for op in ops.renames:
            new = op.new.strip()
            if not new or op.old == new or op.old not in names:
                continue  # hallucinated/echoed names dropped, not dead-ended
            await self._records.rename_label(op.old, new)
            names.discard(op.old)
            names.add(new)
            report.relabeled += 1
        for op in ops.reclass:
            kind = op.kind.strip().lower()
            if kind not in ("entity", "meta") or op.label not in names:
                continue  # hallucinated label or bogus kind dropped, not dead-ended
            await self._records.set_label_kind(op.label, kind)
            report.reclassified += 1
        return True

    async def _judge_labels(self, labels: list[dict]) -> LabelOps | None:
        from ntrp.memory.file_store import load_conventions

        listing = "\n".join(f"{entry['label']}: {entry['count']} [{entry['kind']}]" for entry in labels)
        messages = [
            {"role": "system", "content": f"<operating_manual>\n{load_conventions()}\n</operating_manual>\n\n" + LABEL_HYGIENE_RUBRIC},
            {"role": "user", "content": f"VOCABULARY (label: active records):\n{listing}"},
        ]
        try:
            resp = await self._llm.completion(
                messages=messages,
                model=self._model,
                reasoning_effort=self._reasoning_effort,
                response_format=LabelOps,
            )
        except Exception:
            _logger.warning("label hygiene judgment failed", exc_info=True)
            return None
        content = resp.choices[0].message.content if resp.choices else None
        if not content or not content.strip():
            return None
        try:
            return LabelOps.model_validate_json(content)
        except Exception:
            _logger.warning("label hygiene: unparseable judgment", exc_info=True)
            return None

    @staticmethod
    def _label_fingerprint(labels: list[dict]) -> str:
        payload = [
            {"label": entry["label"], "count": entry["count"], "kind": entry["kind"]}
            for entry in sorted(labels, key=lambda entry: (entry["label"], entry["kind"], entry["count"]))
        ]
        return hashlib.sha256(json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()

    async def _current_label_fingerprint(self) -> str:
        return self._label_fingerprint(await self._records.list_labels())

    # --- metadata ---------------------------------------------------------

    async def _ensure_conn(self):
        if self._conn is not None:
            return self._conn
        async with self._conn_lock:
            if self._conn is None:
                self._conn = await db_connect(self._db_path)
                await self._conn.execute("PRAGMA journal_mode=WAL")
                await self._conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                await self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS consolidated (record_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)"
                )
                await self._conn.commit()
        return self._conn

    async def _read_fingerprints(self) -> dict[str, str]:
        conn = await self._ensure_conn()
        rows = await conn.execute_fetchall("SELECT record_id, fingerprint FROM consolidated")
        return {row["record_id"]: row["fingerprint"] for row in rows}

    async def _write_fingerprint(self, record_id: str, fingerprint: str) -> None:
        conn = await self._ensure_conn()
        await conn.execute(
            "INSERT INTO consolidated(record_id, fingerprint) VALUES(?, ?) "
            "ON CONFLICT(record_id) DO UPDATE SET fingerprint = excluded.fingerprint",
            (record_id, fingerprint),
        )
        await conn.commit()

    async def _prune_fingerprints(self, active_ids: set[str]) -> None:
        """Drop cached fingerprints for records no longer in the active pool (merged
        away, superseded, deleted) so the table stays bounded by the live corpus."""
        conn = await self._ensure_conn()
        rows = await conn.execute_fetchall("SELECT record_id FROM consolidated")
        stale = [r["record_id"] for r in rows if r["record_id"] not in active_ids]
        if stale:
            await conn.executemany("DELETE FROM consolidated WHERE record_id = ?", [(i,) for i in stale])
            await conn.commit()

    async def reset_watermark(self) -> None:
        """/init: clear the judged-fingerprint cache + label-vocab fingerprint so the
        next run_once re-judges the whole (re-derived) pool from scratch."""
        conn = await self._ensure_conn()
        await conn.execute("DELETE FROM consolidated")
        await conn.execute("DELETE FROM meta WHERE key = ?", (LABEL_FINGERPRINT_KEY,))
        await conn.commit()

    async def _read_label_fingerprint(self) -> str | None:
        return await self._read_meta(LABEL_FINGERPRINT_KEY)

    async def _write_label_fingerprint(self, value: str) -> None:
        await self._write_meta(LABEL_FINGERPRINT_KEY, value)

    async def _read_meta(self, key: str) -> str | None:
        conn = await self._ensure_conn()
        rows = await conn.execute_fetchall("SELECT value FROM meta WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    async def _write_meta(self, key: str, value: str) -> None:
        conn = await self._ensure_conn()
        await conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
