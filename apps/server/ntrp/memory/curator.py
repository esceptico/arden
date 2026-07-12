"""The sleep-time Dreamer — the background memory writer.

ONE LLM call per session: reads new transcript turns since a per-session
watermark, reconciles them against existing similar records (ADD/UPDATE/
SUPERSEDE/NOOP), and applies the ops — text AND labels — to the flat record
pool. Labels are open-vocabulary names (referents and categories) decided once
at write time; the prompt carries the existing vocabulary so names get reused
instead of re-minted. Everything enters through the worthiness gate; targeted
feed automations (feeds/) carry transient integration state outside the record
pool entirely.

The watermark lives in a tiny `meta(key, value)` table the Dreamer OWNS inside
`config.memory_db_path`. Key form: `curate_watermark:{session_id}`. Anti-heartbeat:
a session with no new turns costs one DB read and no LLM call.
"""

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

from ntrp.database import connect as db_connect
from ntrp.logging import get_logger
from ntrp.memory.models import Kind, Record, SourceRef, now_iso
from ntrp.memory.reconciler import RecordOperation, validate_operations
from ntrp.memory.records import RecordStore
from ntrp.memory.scopes import USER_SCOPE, MemoryScope, apply_scope_to_source, scope_for_write
from ntrp.observability import observed_trace

_logger = get_logger(__name__)

# Periodic backstop: sessions that never complete a chat run (automations, idle
# chats, abandoned mid-run sessions) still get curated. Cheap: already-curated
# sessions cost one DB read, no LLM.
SWEEP_INTERVAL_SECONDS = 600
SWEEP_SESSION_LIMIT = 50
CURATION_BATCH_MAX_TURNS = 40
CURATION_BATCH_MAX_CHARS = 6_000
CURATION_TURN_MAX_CHARS = 2_000
# Defense-in-depth atop source-trust: drop passive integration items that look like
# prompt injection before they ever enter memory (a poisoned email/message can't
# launder instructions into the store for the dream/agent to later read). Heuristic,
# not exhaustive — source-trust (observations are low-trust, the dream phrases them
# tentatively) remains the primary mitigation.
_INJECTION_RE = re.compile(
    r"ignore (?:all |any |the )?(?:previous|prior|above|earlier) (?:instructions|prompts|messages|context)"
    r"|disregard (?:all |any |the )?(?:previous|prior|above) (?:instructions|prompts)"
    r"|(?:new|updated) (?:system )?instructions\s*:"
    r"|you are now (?:a |an |the )"
    r"|pretend (?:to be|you are)"
    r"|(?:reveal|print|show|repeat) (?:your |the )?system prompt",
    re.IGNORECASE,
)
CURATION_ROLES = {"user", "assistant"}
LABEL_VOCAB_LIMIT = 40
MAX_LABELS_PER_RECORD = 4
ALLOWED_KINDS = {"directive", "fact", "source", "lesson"}
# A backfill subject equal to one of these is the USER, not an external entity — skip it
# (the user is me.md, not a topic page). The prompt also handles the user's actual name.
_SELF_REFERENCES = {"me", "i", "self", "user", "the user", "myself", "you"}
# Legacy/alias kinds that map onto a writable kind. Narrative kinds ("note",
# "action", "summary") are intentionally absent: junk/narrative is SKIPPED at
# write time, not coerced into a record.
LEGACY_KIND_MAP = {
    "preference": "fact",
    "project_fact": "fact",
    "feedback": "source",
    "changelog": "source",
}
BAD_TEXT_PATTERNS = ("project-proj_", "source=curator:", "facts/project-", "summaries/project-", "sources/index.md")

# consolidate.run_once() is a heavy FTS+LLM sweep over the record pool. Running
# it INLINE on the chat server (after every curation) saturates CPU/SQLite and
# times out chat — especially right after a bulk consolidation, when the delta
# is the whole pool. Default OFF: consolidation runs out-of-process
# (scripts.run_consolidation). Set NTRP_INLINE_CONSOLIDATE=1 to re-enable inline.
_INLINE_CONSOLIDATE = os.environ.get("NTRP_INLINE_CONSOLIDATE", "0").lower() in ("1", "true", "yes")

_SYSTEM_PROMPT = (
    "You maintain a set of atomic memory RECORDS about the user (and their world). "
    "Given the EXISTING SIMILAR RECORDS and NEW CONVERSATION TURNS, return a SINGLE "
    "JSON object:\n"
    "{\n"
    '  "records": [\n'
    '    {"op": "ADD",       "text": "<self-contained statement>", "kind": "directive|fact|source|lesson", "entity_labels": ["Dex", "Regina Lin"], "meta_labels": ["Bug", "Open loop"]},\n'
    '    {"op": "UPDATE",    "id": "<existing id>", "text": "<corrected statement>", "entity_labels": ["..."], "meta_labels": ["..."]},\n'
    '    {"op": "SUPERSEDE", "id": "<existing id>", "text": "<replacement statement>", "kind": "...", "entity_labels": ["..."], "meta_labels": ["..."]},\n'
    '    {"op": "NOOP"}\n'
    "  ]\n"
    "}\n"
    "Rules:\n"
    "(1) ADMIT GATE / WORTHINESS — the bar is HIGH for chatter, OPEN for durable "
    "knowledge. Admit two classes:\n"
    "  (a) USER knowledge: standing preferences, stable facts about the user or "
    "their world, behaviour rules, real source pointers.\n"
    "  (b) WORKING knowledge — the user's projects and engineering work are their "
    "life; memory that can't hold them is useless. Admit durable project state "
    "(what a project IS, what's being built and why), decisions made and their "
    "reasoning, gotchas/constraints discovered the hard way ('X breaks when Y'), "
    "ownership and blockers, and open loops (waiting-on, pending decisions). "
    "Write each as a standalone fact a future agent can act on.\n"
    "DROP (admit NOTHING for) transient session recaps, catch-up/status "
    "narratives, tool-by-tool narration, and play-by-play of a work session — "
    "the OUTCOME (decision/gotcha/state change) is the record, the journey is "
    "transcript-search material. A durable fact is one that will still be true "
    "and useful next month; 'currently debugging Z' is not, 'Z's root cause was "
    "the stale vite watcher' is. Most exchanges contain NOTHING worth "
    "remembering. Operational/automation runs (the agent doing its job, SOPs, "
    "routine tool output) admit NOTHING unless they reveal durable knowledge. "
    "Never turn one short task or debugging segment into a global fact or "
    "pattern. Explicit user corrections are maximal signal — always admit them, "
    "keep the user's wording.\n"
    "(1b) DIRECTIVE GATE — `directive` is the RAREST kind, reserved for a short, "
    "durable, user-STATED rule about how the assistant must behave (e.g. 'answer "
    "concisely', 'never commit without my review'). Keep it to 1-2 sentences. A "
    "context-specific instruction — about one codebase, tool, automation, "
    "migration, or workflow — is a `fact`, NOT a directive. NEVER promote a "
    "debugging fix, an implementation/SOP detail, a one-off task instruction, or a "
    "design opinion to a directive; those are facts or nothing. When unsure between "
    "directive and fact, choose fact.\n"
    "(1c) LESSON CAPTURE (continual learning) — a `lesson` record is the assistant's "
    "PLAYBOOK: a durable working-pattern you DISTILL from how the interaction actually "
    "went — a recurring user correction turned into a forward rule, a failure-mode to "
    "avoid, or a strategy that worked. It differs from a `directive` (a rule the user "
    "explicitly STATED) in that YOU infer it from the exchange. This is the ONE "
    "exception to the drop-meta rule: capture lessons even from dev/process/ntrp "
    "exchanges, because that is where the assistant learns to improve. Each lesson is "
    "ONE actionable, second-person sentence (e.g. 'Verify claims against the running "
    "system before reporting status — the user is repeatedly burned by confident-but-"
    "wrong updates.'). Mint a lesson only for a pattern likely to recur, not a one-off. "
    "Lessons take no entity_labels.\n"
    "(2) Records are atomic, self-contained, short, and usable in future prompts (resolve pronouns inline), typed by "
    "FUNCTION not subject. Allowed kinds: directive (standing instruction), fact (stable durable statement), source (receipt pointer only), lesson (distilled playbook rule, see 1c). Choose the op against the EXISTING SIMILAR RECORDS: "
    "ADD (new), UPDATE (edit an existing one), SUPERSEDE (replace a now-wrong "
    "one), NOOP (no durable change). For UPDATE/SUPERSEDE, use ONLY ids that appear "
    "in EXISTING SIMILAR RECORDS; never invent an id.\n"
    "(3) LABELS — attach labels to every ADD/UPDATE/SUPERSEDE using two distinct fields:\n"
    "  entity_labels: 0-2 — the NAMED subject the record is fundamentally about: a person "
    "(Regina Lin, Kevin Gu), company/org (Replika, Anthropic, ThirdLayer), product (Dex, ntrp), "
    "project (MATS), place (Yerevan), or named concept (O-1A). TAG the named subject even when the "
    "fact is about the USER'S relationship or history with it: 'you worked at Replika on post-training' "
    "-> Replika; 'Kevin is your main reviewer' -> Kevin Gu; 'you live in Yerevan' -> Yerevan. Recurring "
    "people, orgs, and products SHOULD become their own topic pages. But NOT subjects merely mentioned in "
    "passing or listed as examples (a rule about ntrp that says 'pages such as O-1A and health' is about "
    "ntrp, NOT O-1A/health). A pure self-fact with no external named subject (your birthday, a generic "
    "preference) gets an EMPTY list. ONE subject per record: if a fact bundles several people/orgs (a "
    "collaborator list), SPLIT it into one ADD per subject so each accumulates its own records. Tag "
    "freely — a single mention does NOT create a page (the promotion threshold parks a one-record subject "
    "on me.md until it recurs), so only sustained subjects ever get a page.\n"
    "  meta_labels: 0-3 category/workflow tags — Bug, Open loop, Health, Skills, Automation, "
    "etc. These classify the record but never get their own page.\n"
    "REUSE names from LABEL VOCABULARY whenever they fit, with exact casing; mint new ones only "
    "when nothing fits. UPDATE labels REPLACE the record's labels; omit both fields to keep them.\n"
    "(4) Never store assistant implementation reports, verification transcripts, or UI/file-path churn as memory — "
    "when work concludes in a durable state change ('memory is now file-canonical'), store THAT one fact, not the report. "
    "If the user corrects memory behavior, store only the durable behavior rule/preference, not a narrative of the fix. "
    "Never expose internal ids/provenance strings like project-proj_..., source=curator:..., facts/project-..., or summaries/project-... in record text. "
    "Write record text as clean human prose: keep meaningful names (people, products, experiment/metric names) but omit raw absolute file paths, commit hashes, UUIDs, and opaque run/tool ids — they are noise the projection now renders verbatim.\n"
    "(5) Output ONLY the JSON object, no preamble."
)

_ENTITY_BACKFILL_SYSTEM = (
    "You tag memory records with the one NAMED subject each is fundamentally about, so "
    "recurring people/orgs/products become their own topic pages. For EACH record below, return "
    "the single named subject — a person (Kevin Gu), company/org (Replika, ThirdLayer), product "
    "(Dex, ntrp), project (MATS), or place (Yerevan) — that the record is centrally about, INCLUDING "
    "when it states the user's relationship or history with that subject ('worked at Replika' -> "
    "Replika; 'Kevin is the reviewer' -> Kevin Gu). Return null for a pure self-fact with no external "
    "named subject (birthday, a generic preference), or for a record that bundles several subjects at "
    "once (leave those for the curator to split). Reuse an EXISTING SUBJECT's exact casing when one "
    'fits. Output ONLY a JSON object mapping record id -> subject string or null, e.g. '
    '{"a1b2c3d4": "Kevin Gu", "e5f6a7b8": null}.'
)

# Appended to the system prompt for the /init BULK re-derivation. The default gate
# is brutal on purpose (turn-by-turn pollution control); for a one-time pass over
# the user's whole history that brutality yields almost nothing, so this OVERRIDES
# rule (1) toward comprehensive capture. Rules (1b)-(5) still apply.
_BULK_OVERRIDE = (
    "\n\nBULK RE-DERIVATION MODE — OVERRIDE the worthiness bar in rule (1). This is a "
    "one-time pass over the user's ENTIRE history to rebuild memory comprehensively. "
    "Admit EVERY durable thing about the user and their world: identity, role, "
    "employer, location, timezone, preferences, working style, the people / orgs / "
    "products / projects they work with, ongoing work and goals, and stable personal "
    "facts. WORKING KNOWLEDGE is first-class here: what each project IS and why it "
    "exists, architecture and design decisions with their reasoning, gotchas and "
    "constraints discovered the hard way, ownership, blockers, and open loops — "
    "written as standalone facts a future agent can act on (outcome, not the "
    "debugging journey). Multiple records per batch are expected — do not collapse a "
    "rich batch into one bland record. Skip ONLY pure transient noise (tool-call "
    "spam, bare 'ok' / 'thanks', one-off debugging chatter with no durable "
    "takeaway). When in doubt, ADMIT — comprehensiveness is the goal and "
    "consolidation dedups later."
)


class Curator:
    """The sleep-time Dreamer. ONE LLM call per session: read new transcript
    turns since a per-session watermark, reconcile them into the flat record
    pool, labeling each written record from the open vocabulary."""

    def __init__(
        self,
        llm,  # completion client for config.memory_model
        sessions,  # SessionService — reads transcript turns via messages_since
        *,
        model: str,  # config.memory_model id (for the call)
        db_path: Path,  # config.memory_db_path — the Dreamer owns this meta DB
        record_store: RecordStore,  # the flat record pool; ops land here
        consolidate=None,  # Consolidate — the CONSOLIDATE/LINT step (None -> skip)
        reasoning_effort: str | None = None,
    ) -> None:
        self._llm = llm
        self._sessions = sessions
        self._model = model
        self._record_store = record_store
        self._consolidate = consolidate
        self._reasoning_effort = reasoning_effort

        # The Dreamer owns this meta DB (watermark). Co-located with the records.
        self._db_path = db_path
        self._conn = None  # type: ignore[assignment]
        self._conn_lock = asyncio.Lock()

        # De-dupe in-flight curations per session.
        self._tasks: dict[str, asyncio.Task] = {}
        self._pending_questions: dict[str, str] = {}

        # The periodic curation sweep (started by knowledge.connect()).
        self._sweep_task: asyncio.Task | None = None

    # -- public API ----------------------------------------------------------

    def schedule_curation(self, session_id: str) -> None:
        """Fire-and-forget: spawn a tracked asyncio task running curate_session.
        Called from chat.py end-of-run. Swallows + logs errors; never blocks the
        response path. De-dupes: if a curation for this session is already
        in-flight, no-op."""
        existing = self._tasks.get(session_id)
        if existing is not None and not existing.done():
            return

        task = asyncio.create_task(self._run_curation(session_id))
        self._tasks[session_id] = task

    def start_sweep(self) -> None:
        """Start the periodic curation sweep (idempotent). The backstop for
        sessions that never complete a chat run, so they still reach memory."""
        if self._sweep_task is None or self._sweep_task.done():
            self._sweep_task = asyncio.create_task(self._sweep_loop())

    def pending_question(self, session_id: str) -> str | None:
        return self._pending_questions.get(session_id)

    async def sweep_once(self) -> int:
        """Schedule curation for the most-recent live USER CHATS. Automation
        channels and spawned agent sessions are operational transcripts —
        memory never reads them. Cheap: relies on schedule_curation's in-flight
        de-dupe and curate_session's no-new-turns no-op, so already-curated
        sessions cost one DB read and no LLM call. Returns the number of
        sessions scheduled."""
        rows = await self._sessions.recent_session_scopes(SWEEP_SESSION_LIMIT)
        scheduled = 0
        for row in rows:
            if row["session_type"] != "chat" or row["origin_automation_id"] is not None:
                continue
            self.schedule_curation(row["session_id"])
            scheduled += 1
        # Off-hot-path importance backfill: score any unscored lines (file store).
        if hasattr(self._record_store, "score_pending"):
            try:
                await self._record_store.score_pending()
            except Exception:
                _logger.warning("score_pending failed", exc_info=True)
        return scheduled

    async def curate_session(self, session_id: str) -> bool:
        """1. Read watermark (max seq already curated for this session).
        2. Load new transcript turns (seq > watermark) via the sessions store.
        3. If no new substantive turns -> advance watermark, return False.
        4. ONE LLM call emitting record ops (text + labels). Admit gate: empty
           ops + advance.
        5. Apply ops to the flat pool.
        6. Advance watermark ONLY after a successful apply (or confirmed no-change)."""
        watermark = await self._read_watermark(session_id)
        rows = await self._sessions.messages_since(session_id, watermark)

        turns, max_seq = self._select_batch(rows, watermark)

        if not turns:
            await self._write_watermark(session_id, max_seq)
            return False

        batch_key = self._batch_key(session_id, max_seq)
        if hasattr(self._record_store, "operation_batch_committed") and self._record_store.operation_batch_committed(batch_key):
            await self._write_watermark(session_id, max_seq)
            return False

        session_scope = await self._sessions.session_scope(session_id)
        explicit_scope = await self._area_scope(session_scope.get("area_id") if session_scope else None)
        ops = await self._complete(turns, header="NEW TURNS", explicit_scope=explicit_scope)
        if ops is None:
            # The LLM call failed (or returned nothing usable); do NOT advance the
            # watermark so the same turns are retried next time (idempotent — the
            # model dedupes against the existing records).
            return False

        sources = self._batch_sources(session_id, turns, explicit_scope)
        operations = await self._typed_operations(ops, sources, explicit_scope)
        if question := next((operation.question for operation in operations if operation.op == "ASK"), None):
            self._pending_questions[session_id] = question
            _logger.info("curation requires clarification", session_id=session_id, question=question)
            return False
        self._pending_questions.pop(session_id, None)
        new_records = await self._apply_operations(operations, session_id, sources, batch_key=batch_key)

        # CONSOLIDATE/LINT — THE memory step: turn the raw record pile into a small,
        # clean, current body (merge duplicates, supersede stale, drop orphans).
        # Best-effort + O(delta): no-ops cheaply when nothing changed since its own
        # watermark, so this is safe to call on every curation.
        if new_records and self._consolidate is not None and _INLINE_CONSOLIDATE:
            try:
                await self._consolidate.run_once()
            except Exception:
                _logger.warning("consolidation failed", exc_info=True)

        await self._write_watermark(session_id, max_seq)
        return bool(ops)

    @observed_trace("memory.curate.full", tags="memory")
    async def curate_session_fully(
        self,
        session_id: str,
        *,
        area_id: str | None = None,
        derivation_run_id: str = "incremental",
        max_calls: int | None = None,
        bulk: bool = False,
    ) -> dict:
        """Full re-derivation of ONE session: loop curate_session-style batches,
        draining ALL transcript turns rather than the single 40-turn batch the
        incremental tick does. Advances the in-process watermark each iteration via
        the batch's max_seq so the next batch starts where this one ended.

        Returns {"admitted": <records written>, "calls": <LLM calls spent>,
        "capped": <budget hit before draining>}. `max_calls` is an optional
        per-session LLM budget (the /init driver threads its global remainder in);
        when hit we stop early with capped=True so the driver can account for it.
        """
        admitted = 0
        calls = 0
        capped = False
        watermark = await self._read_watermark(session_id)
        if area_id is None:
            session_scope = await self._sessions.session_scope(session_id)
            area_id = session_scope.get("area_id") if session_scope else None
        explicit_scope = await self._area_scope(area_id)

        while True:
            if max_calls is not None and calls >= max_calls:
                capped = True
                break
            rows = await self._sessions.messages_since(session_id, watermark)
            turns, max_seq = self._select_batch(rows, watermark)
            if not turns:
                await self._write_watermark(session_id, max_seq)
                break

            batch_key = self._batch_key(session_id, max_seq, derivation_run_id)
            if hasattr(self._record_store, "operation_batch_committed") and self._record_store.operation_batch_committed(batch_key):
                await self._write_watermark(session_id, max_seq)
                watermark = max_seq
                continue

            ops = await self._complete(turns, header="NEW TURNS", bulk=bulk, explicit_scope=explicit_scope)
            calls += 1
            if ops is None:
                # Failed/empty completion: do NOT advance (the batch retries next
                # run). Stop the drain so a persistent failure can't spin.
                break

            sources = self._batch_sources(session_id, turns, explicit_scope)
            operations = await self._typed_operations(ops, sources, explicit_scope)
            if question := next((operation.question for operation in operations if operation.op == "ASK"), None):
                self._pending_questions[session_id] = question
                _logger.info("curation requires clarification", session_id=session_id, question=question)
                break
            self._pending_questions.pop(session_id, None)
            new_records = await self._apply_operations(operations, session_id, sources, batch_key=batch_key)
            admitted += len(new_records)
            await self._write_watermark(session_id, max_seq)
            watermark = max_seq

        return {"admitted": admitted, "calls": calls, "capped": capped}

    @observed_trace("memory.labels", tags="memory")
    async def backfill_entity_labels(self, *, limit: int = 80) -> int:
        """Tag active fact/source records that carry NO entity label with the single
        NAMED subject they're about, so recurring people/orgs/products accumulate into
        topic pages (the promotion threshold still gates page creation). One LLM call
        over the untagged set; idempotent (already-tagged records are skipped) and a
        no-op without an LLM. Returns the count newly tagged."""
        if self._llm is None or not self._model:
            return 0
        rows = await self._record_store.list(limit=None, scopes=None, kinds=["fact", "source"])
        labels = await self._record_store.labels_for([r.id for r in rows], include_kind=True)
        untagged = [r for r in rows if not any(e["kind"] == "entity" for e in labels.get(r.id, []))]
        # Newest first: a one-off bundled/self-fact the model leaves null must not crowd
        # the cap and starve genuinely new untagged records of a tag.
        untagged.sort(key=lambda r: r.last_confirmed_at or "", reverse=True)
        untagged = untagged[:limit]
        if not untagged:
            return 0
        vocab = [row["label"] for row in await self._record_store.list_labels() if row["kind"] == "entity"]
        listing = "\n".join(f'{{"id": "{r.id}", "text": {json.dumps(r.text)}}}' for r in untagged)
        messages = [
            {"role": "system", "content": _ENTITY_BACKFILL_SYSTEM},
            {"role": "user", "content": f"EXISTING SUBJECTS (reuse exact casing when one fits): "
             f"{', '.join(vocab) or '(none yet)'}\n\nRECORDS:\n{listing}"},
        ]
        try:
            resp = await self._llm.completion(
                messages=messages, model=self._model, reasoning_effort=self._reasoning_effort,
            )
        except Exception:
            _logger.warning("entity backfill LLM call failed", exc_info=True)
            return 0
        content = resp.choices[0].message.content if resp.choices else None
        body = (content or "").strip()
        if body.startswith("```"):
            body = body.split("\n", 1)[-1]
            body = body[: body.rfind("```")] if "```" in body else body
        try:
            mapping = json.loads(body.strip())
        except (ValueError, TypeError):
            return 0
        if not isinstance(mapping, dict):
            return 0
        by_id = {r.id: r for r in untagged}
        tagged = 0
        for rid, subject in mapping.items():
            if rid in by_id and isinstance(subject, str) and subject.strip():
                # Never promote the USER themselves to a topic page — that just duplicates me.md.
                if subject.strip().lower() in _SELF_REFERENCES:
                    continue
                await self._record_store.set_labels(rid, [], entity_labels=[subject.strip()])
                tagged += 1
        if tagged:
            _logger.info("entity backfill tagged records", tagged=tagged, examined=len(untagged))
        return tagged

    async def reset_watermarks(self) -> None:
        """/init: clear every per-session curate watermark so the next read starts
        fresh (full re-curation from the start of each transcript). Also sweeps
        retired ingest watermarks left by the removed integration-observation flow."""
        conn = await self._ensure_conn()
        await conn.execute("DELETE FROM meta WHERE key LIKE 'curate_watermark:%'")
        await conn.execute("DELETE FROM meta WHERE key LIKE 'ingest_watermark:%'")
        await conn.commit()

    async def stop(self) -> None:
        """Cancel the sweep loop and await/cancel in-flight curation tasks.
        Called from knowledge.stop()."""
        if self._sweep_task is not None and not self._sweep_task.done():
            self._sweep_task.cancel()
            await asyncio.gather(self._sweep_task, return_exceptions=True)
        self._sweep_task = None
        tasks = [t for t in self._tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # -- internals -----------------------------------------------------------

    async def _run_curation(self, session_id: str) -> None:
        try:
            await self.curate_session(session_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.warning("curation failed", session_id=session_id, exc_info=True)
        finally:
            current = self._tasks.get(session_id)
            if current is asyncio.current_task():
                self._tasks.pop(session_id, None)

    async def _sweep_loop(self) -> None:
        # Sleep first so startup isn't a thundering herd of curations.
        while True:
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            try:
                await self.sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.warning("curation sweep failed", exc_info=True)

    @observed_trace("memory.curate", tags="memory")
    async def _complete(
        self,
        lines: list[dict],
        header: str,
        *,
        bulk: bool = False,
        explicit_scope: MemoryScope | None = None,
    ) -> list[dict] | None:
        """ONE LLM call emitting the record-ops. Pre-searches the flat record pool
        for the candidate set so the model picks from REAL ids only, and carries
        the label vocabulary (top names by count + the recalled records' labels)
        so names get reused instead of re-minted. Returns the parsed op list, or
        None on a content-less/failed reply (no-advance).

        `lines` is the source-agnostic body (chat turns today; integration records
        in Phase 2) and `header` labels the block in the prompt."""
        existing = await self._record_store.search("\n".join(line["content"] for line in lines), limit=20)
        existing_labels = await self._record_store.labels_for([r.id for r in existing])
        existing_block = (
            "\n".join(
                f"- {r.id} [{', '.join(existing_labels[r.id])}]: {r.text}"
                if existing_labels[r.id]
                else f"- {r.id}: {r.text}"
                for r in existing
            )
            if existing
            else "(none)"
        )

        vocabulary = [row["label"] for row in (await self._record_store.list_labels())[:LABEL_VOCAB_LIMIT]]
        for labels in existing_labels.values():
            vocabulary.extend(label for label in labels if label not in vocabulary)
        vocabulary_block = ", ".join(vocabulary) if vocabulary else "(none yet)"

        scope_label = f"{explicit_scope.kind}:{explicit_scope.key or ''}" if explicit_scope else "unspecified"
        user_prompt = (
            f"EXISTING SIMILAR RECORDS (id [labels]: text — use these ids only):\n"
            f"{existing_block}\n\n"
            f"LABEL VOCABULARY (reuse before minting):\n"
            f"{vocabulary_block}\n\n"
            f"SCOPE: {scope_label}\n\n"
            f"{header} (JSONL envelopes):\n"
            + "\n".join(json.dumps(line, ensure_ascii=False, separators=(",", ":")) for line in lines)
        )
        from ntrp.memory.file_store import load_conventions

        messages = [
            {"role": "system", "content": f"<operating_manual>\n{load_conventions()}\n</operating_manual>\n\n"
             + _SYSTEM_PROMPT + (_BULK_OVERRIDE if bulk else "")},
            {"role": "user", "content": user_prompt},
        ]
        try:
            resp = await self._llm.completion(
                messages=messages,
                model=self._model,
                reasoning_effort=self._reasoning_effort,
            )
        except Exception:
            _logger.warning("curator LLM call failed", exc_info=True)
            return None
        content = resp.choices[0].message.content if resp.choices else None
        # A content-less but non-erroring completion (refusal, content filter,
        # truncation, reasoning-only finish) must NOT advance the watermark.
        if not content or not content.strip():
            return None
        return self._parse_completion(content)

    async def _area_scope(self, area_id: str | None) -> MemoryScope:
        """Resolve one explicit area ID for the complete curation batch."""
        if not area_id:
            return USER_SCOPE
        area = await self._sessions.get_area(area_id)
        if area is None:
            raise ValueError(f"session area does not exist: {area_id}")
        return MemoryScope("area", str(area["area_id"]))

    @staticmethod
    def _batch_key(session_id: str, max_seq: int, derivation_run_id: str = "incremental") -> str:
        return hashlib.sha256(f"{derivation_run_id}:{session_id}:{max_seq}".encode()).hexdigest()

    @staticmethod
    def _batch_sources(session_id: str, turns: list[dict], scope: MemoryScope) -> tuple[SourceRef, ...]:
        captured_at = now_iso()
        sources: list[SourceRef] = []
        for turn in turns:
            occurred_at = turn.get("created_at")
            precision = "unknown"
            if isinstance(occurred_at, str):
                fraction = re.search(r"\.(\d+)(?:Z|[+-]\d{2}:\d{2})$", occurred_at)
                precision = "millisecond" if fraction and len(fraction.group(1)) == 3 else "second" if fraction is None else "unknown"
            sources.append(
                SourceRef(
                    kind="chat_message",
                    ref=turn["message_id"],
                    captured_at=captured_at,
                    scope_kind=scope.kind,
                    scope_key=scope.key,
                    occurred_at=occurred_at,
                    time_precision=precision,
                    role=turn["role"],
                    extra={"session_id": session_id, "seq": turn["seq"]},
                )
            )
        return tuple(sources)

    async def _typed_operations(
        self,
        raw_operations: list[dict],
        sources: tuple[SourceRef, ...],
        explicit_scope: MemoryScope,
    ) -> tuple[RecordOperation, ...]:
        operations: list[RecordOperation] = []
        for raw in raw_operations:
            verb = str(raw.get("op", "")).upper()
            meta_labels, entity_labels = self._op_labels(raw)
            labels = tuple(meta_labels) if meta_labels is not None else None
            entities = tuple(entity_labels) if entity_labels is not None else None
            if verb == "ADD":
                text = self._op_text(raw)
                kind = self._op_kind(raw, default="fact")
                if text is None or kind is None:
                    continue
                scope = scope_for_write(
                    kind=kind,
                    source_ref=sources[0],
                    explicit_scope=explicit_scope if explicit_scope.kind == "area" else None,
                )
                operations.append(
                    RecordOperation.add(
                        text,
                        kind=Kind(kind),
                        scope=scope,
                        meta_labels=labels,
                        entity_labels=entities,
                    )
                )
            elif verb in {"UPDATE", "SUPERSEDE"}:
                text = self._op_text(raw)
                target = raw.get("id") or next(iter(raw.get("target_ids") or ()), None)
                if text is None or not isinstance(target, str):
                    raise ValueError(f"{verb} requires a target and text")
                raw_kind = self._op_kind(raw, default="fact") if raw.get("kind") else None
                operations.append(
                    RecordOperation.supersede(
                        target,
                        text,
                        kind=Kind(raw_kind) if raw_kind else None,
                        meta_labels=labels,
                        entity_labels=entities,
                    )
                )
            elif verb == "MERGE":
                text = self._op_text(raw)
                targets = tuple(raw.get("target_ids") or raw.get("ids") or ())
                if text is None:
                    raise ValueError("MERGE requires text")
                operations.append(RecordOperation.merge(targets, text))
            elif verb == "RETRACT":
                targets = tuple(raw.get("target_ids") or ([raw["id"]] if raw.get("id") else ()))
                operations.append(RecordOperation.retract(*targets))
            elif verb == "NOOP":
                operations.append(
                    RecordOperation(
                        op="NOOP",
                        text=raw.get("text"),
                        kind=raw.get("kind"),
                        scope=raw.get("scope"),
                        target_ids=tuple(raw.get("target_ids") or ()),
                        question=raw.get("question"),
                        meta_labels=labels,
                        entity_labels=entities,
                    )
                )
            elif verb == "ASK":
                operations.append(
                    RecordOperation(
                        op="ASK",
                        text=raw.get("text"),
                        kind=raw.get("kind"),
                        scope=raw.get("scope"),
                        target_ids=tuple(raw.get("target_ids") or ([raw["id"]] if raw.get("id") else ())),
                        question=raw.get("question"),
                        meta_labels=labels,
                        entity_labels=entities,
                    )
                )
            else:
                raise ValueError(f"unknown record operation: {verb}")
        records = tuple(await self._record_store.list(limit=None, scopes=None))
        return validate_operations(tuple(operations), records, sources)

    async def _apply_operations(
        self,
        operations: tuple[RecordOperation, ...],
        session_id: str,
        sources: tuple[SourceRef, ...],
        *,
        batch_key: str,
    ) -> list[Record]:
        if hasattr(self._record_store, "apply_operations"):
            self._record_store.apply_operations(operations, sources, batch_key=batch_key)
            written = [operation for operation in operations if operation.op in {"ADD", "SUPERSEDE", "MERGE"}]
            return [record for record in await self._record_store.list(limit=None, scopes=None) if record.text in {op.text for op in written}]

        records: list[Record] = []
        source = sources[0]
        for operation in operations:
            if operation.op == "MERGE":
                record = await self._record_store.merge(
                    operation.target_ids[0],
                    list(operation.target_ids[1:]),
                    text=operation.text,
                    kind=operation.kind,
                )
            elif operation.op == "RETRACT":
                for target in operation.target_ids:
                    await self._record_store.delete(target, source_ref=source)
                record = None
            elif operation.op == "ASK":
                record = None
            else:
                raw = {
                    "op": operation.op,
                    "id": operation.target_ids[0] if operation.target_ids else None,
                    "text": operation.text,
                    "kind": operation.kind,
                    "meta_labels": list(operation.meta_labels) if operation.meta_labels is not None else None,
                    "entity_labels": list(operation.entity_labels) if operation.entity_labels is not None else None,
                }
                record = await self._apply_op(raw, session_id, source)
            if record is not None:
                records.append(record)
        return records

    @staticmethod
    def _parse_completion(content: str) -> list[dict] | None:
        """Parse the LLM's single JSON object into the op list. Tolerates a
        ```json fence. A reply we cannot parse as the expected object is a failure
        (None, no-advance), distinct from a legitimate empty op list ([])."""
        body = content.strip()
        if body.startswith("```"):
            body = body.split("\n", 1)[-1]
            if body.endswith("```"):
                body = body[: body.rfind("```")]
            body = body.strip()
        try:
            data = json.loads(body)
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        raw_ops = data.get("records")
        if not isinstance(raw_ops, list):
            return None
        return [op for op in raw_ops if isinstance(op, dict)]

    async def _apply_op(self, op: dict, session_id: str, source_ref: SourceRef) -> Record | None:
        """Apply one reconciliation op (text + labels) against the flat record
        pool. Returns the added/updated record, or None for NOOP/no-op.

        `source_ref` is the provenance the written record carries — the chat path
        passes `SourceRef(kind="curator", ref=session_id)`; this is the seam
        integrations reuse in Phase 2. `session_id` still drives scope_for_write."""
        verb = str(op.get("op", "")).upper()
        base_source = source_ref
        meta_labels, entity_labels = self._op_labels(op)
        if verb == "ADD":
            text = self._op_text(op)
            kind = self._op_kind(op, default="fact")
            if text and kind is not None:
                scope = scope_for_write(kind=kind, session_id=session_id, source_ref=base_source)
                source = apply_scope_to_source(base_source, scope)
                record = await self._record_store.add(
                    text, kind=kind, source_ref=source, scope_kind=scope.kind, scope_key=scope.key
                )
                if meta_labels or entity_labels:
                    await self._record_store.set_labels(
                        record.id, meta_labels or [], entity_labels=entity_labels or []
                    )
                return record
        elif verb == "UPDATE":
            rid, text = op.get("id"), self._op_text(op)
            if rid and text:
                old = await self._record_store.get(rid)
                if old is not None:
                    scope = scope_for_write(kind=old.kind, session_id=session_id, source_ref=base_source)
                    source = apply_scope_to_source(base_source, scope)
                    record = await self._record_store.supersede_with(
                        rid,
                        text=text,
                        kind=old.kind,
                        source_ref=source,
                        scope_kind=old.scope_kind or scope.kind,
                        scope_key=old.scope_key if old.scope_kind else scope.key,
                    )
                    # UPDATE labels REPLACE; absent fields keep the old set.
                    if meta_labels is not None or entity_labels is not None:
                        await self._record_store.set_labels(
                            record.id, meta_labels or [], entity_labels=entity_labels or []
                        )
                    return await self._record_store.get(record.id)
                # Stale/hallucinated id -> land the correction as an ADD, unless
                # the kind is narrative/unwritable (skip rather than mint).
                kind = self._op_kind(op, default="fact")
                if kind is None:
                    return None
                _logger.warning("UPDATE on unknown record id; landing as ADD", record_id=rid)
                scope = scope_for_write(kind=kind, session_id=session_id, source_ref=base_source)
                source = apply_scope_to_source(base_source, scope)
                record = await self._record_store.add(
                    text, kind=kind, source_ref=source, scope_kind=scope.kind, scope_key=scope.key
                )
                if meta_labels or entity_labels:
                    await self._record_store.set_labels(
                        record.id, meta_labels or [], entity_labels=entity_labels or []
                    )
                return record
        elif verb == "SUPERSEDE":
            rid, text = op.get("id"), self._op_text(op)
            if rid and text:
                old = await self._record_store.get(rid)
                default_kind = old.kind if old else "fact"
                kind = self._op_kind(op, default=default_kind) or default_kind
                scope = scope_for_write(kind=kind, session_id=session_id, source_ref=base_source)
                source = apply_scope_to_source(base_source, scope)
                record = await self._record_store.supersede_with(
                    rid,
                    text=text,
                    kind=kind,
                    source_ref=source,
                    scope_kind=(old.scope_kind if old else scope.kind),
                    scope_key=(old.scope_key if old else scope.key),
                )
                # The successor takes the op's labels; without any it keeps the
                # old record's set (supersede_with already copied it).
                if meta_labels or entity_labels:
                    await self._record_store.set_labels(
                        record.id, meta_labels or [], entity_labels=entity_labels or []
                    )
                return record
        elif verb == "NOOP":
            return None
        return None

    @staticmethod
    def _op_kind(op: dict, *, default: str) -> str | None:
        """Resolve the op's kind to a writable kind. An absent kind defaults; an
        explicit narrative/unknown kind ('summary', 'note', 'action', junk) maps
        to None so the caller SKIPS it rather than minting a low-value record."""
        raw_value = op.get("kind")
        if not raw_value:
            return default
        raw = str(raw_value).strip().lower()
        if raw in ALLOWED_KINDS:
            return raw
        mapped = LEGACY_KIND_MAP.get(raw)
        return mapped if mapped in ALLOWED_KINDS else None

    @staticmethod
    def _op_text(op: dict) -> str | None:
        raw = op.get("text")
        if not isinstance(raw, str):
            return None
        text = " ".join(raw.split()).strip()
        if not text:
            return None
        if len(text) > 1200:
            return None
        lower = text.lower()
        if any(pattern in lower for pattern in BAD_TEXT_PATTERNS):
            return None
        return text

    @staticmethod
    def _op_labels(op: dict) -> tuple[list[str] | None, list[str] | None]:
        """Return (meta_labels, entity_labels) sanitized, or (None, None) when
        both fields are absent — None means 'keep existing' on UPDATE.

        Accepts the new split format (entity_labels / meta_labels) and falls back
        to the legacy flat `labels` field (all treated as meta) for compatibility.
        """
        def _clean(raw) -> list[str] | None:
            if not isinstance(raw, list):
                return None
            out: list[str] = []
            for item in raw:
                if isinstance(item, str) and (name := item.strip()) and name not in out:
                    out.append(name)
            return out[:MAX_LABELS_PER_RECORD]

        meta = _clean(op.get("meta_labels"))
        entity = _clean(op.get("entity_labels"))

        # Legacy flat `labels` field: treat all as meta if new fields absent
        if meta is None and entity is None:
            legacy = _clean(op.get("labels"))
            return legacy, None

        return meta or [], entity or []

    @classmethod
    def _select_batch(cls, rows: list[dict], watermark: int) -> tuple[list[dict], int]:
        turns: list[dict] = []
        total_chars = 0
        max_seq = watermark
        for row in sorted(rows, key=lambda r: r.get("seq", watermark)):
            seq = row.get("seq")
            if seq is None:
                continue
            turn = cls._flatten_turn(row)
            if not turn:
                max_seq = max(max_seq, seq)
                continue
            if turns and (
                len(turns) >= CURATION_BATCH_MAX_TURNS
                or total_chars + len(turn["content"]) > CURATION_BATCH_MAX_CHARS
            ):
                break
            turns.append(turn)
            total_chars += len(turn["content"])
            max_seq = max(max_seq, seq)
        return turns, max_seq

    @staticmethod
    def _flatten_turn(row) -> dict | None:
        """Preserve a stable, role-separated transcript envelope for curation."""
        message = row.get("message") if isinstance(row, dict) else None
        role = (row.get("role") if isinstance(row, dict) else "") or ""
        if role not in CURATION_ROLES:
            return None
        text = Curator._flatten_content(message)
        if not text:
            return None
        if len(text) > CURATION_TURN_MAX_CHARS:
            text = text[:CURATION_TURN_MAX_CHARS].rstrip()
        seq = row.get("seq")
        return {
            "seq": seq,
            "message_id": str(row.get("message_id") or f"seq:{seq}"),
            "role": role,
            "created_at": row.get("created_at"),
            "content": text,
        }

    @staticmethod
    def _flatten_content(message) -> str:
        def walk(raw) -> list[str]:
            if raw is None:
                return []
            if isinstance(raw, str):
                return [raw]
            if isinstance(raw, list):
                out: list[str] = []
                for block in raw:
                    if isinstance(block, dict):
                        t = block.get("type")
                        if t == "text" and block.get("text"):
                            out.append(str(block["text"]))
                    elif isinstance(block, str):
                        out.append(block)
                return out
            if isinstance(raw, dict):
                return walk(raw.get("content"))
            return [str(raw)]

        content = message.get("content") if isinstance(message, dict) else message
        return "\n".join(p for p in walk(content) if p).strip()

    @staticmethod
    def _max_seq(rows, *, default: int) -> int:
        seqs = [row["seq"] for row in rows if isinstance(row, dict) and row.get("seq") is not None]
        return max(seqs, default=default)

    # -- watermark (the Dreamer's own meta table) ----------------------------

    async def _ensure_conn(self):
        if self._conn is not None:
            return self._conn
        async with self._conn_lock:
            if self._conn is None:
                conn = await db_connect(self._db_path)
                await conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
                await conn.commit()
                self._conn = conn
        return self._conn

    @staticmethod
    def _watermark_key(session_id: str) -> str:
        return f"curate_watermark:{session_id}"

    async def _read_watermark(self, session_id: str) -> int:
        conn = await self._ensure_conn()
        rows = await conn.execute_fetchall("SELECT value FROM meta WHERE key = ?", (self._watermark_key(session_id),))
        if not rows:
            return -1
        try:
            return int(rows[0]["value"])
        except (TypeError, ValueError):
            return -1

    async def _write_watermark(self, session_id: str, value: int) -> None:
        conn = await self._ensure_conn()
        await conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (self._watermark_key(session_id), str(value)),
        )
        await conn.commit()
