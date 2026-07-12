# Memory Ledger Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make canonical memory records preserve stable identity, scope, time, evidence, and lifecycle history through recoverable writes and automatic migration.

**Architecture:** Keep readable Markdown record lines, add a schema-v2 adjacent metadata comment, derive active state from append-only lifecycle entries, and commit every canonical file set through a vault journal. A typed reconciler becomes the only path from extracted intent to ledger mutation.

**Tech Stack:** Python 3.13, dataclasses, Pydantic, FastAPI runtime, pytest, Ruff

## Global Constraints

- Markdown remains canonical; SQLite and generated prose are not authoritative.
- Preserve unknown schema-v2 metadata during parse/render round trips.
- Never infer scope from a page after record creation.
- Never invent timestamp precision during migration.
- Preserve complete evidence lists across corrections and merges.
- Lifecycle changes append entries; they do not delete or mutate historical entries.
- Canonical commits finish before watermarks or derived state advance.
- Keep existing public memory-store methods compatible until their callers migrate.
- Use temporary fixture vaults until Task 6 enables backup, migration, and startup validation; never open a real legacy vault with an intermediate commit.

---

### Task 1: Schema-v2 Record and Evidence Codec

**Files:**
- Modify: `apps/server/ntrp/memory/models.py`
- Create: `apps/server/ntrp/memory/ledger.py`
- Modify: `apps/server/ntrp/memory/pages.py`
- Test: `apps/server/tests/test_memory_ledger.py`
- Modify: `apps/server/tests/test_memory_records.py`

**Interfaces:**
- Produces: `TimePrecision = Literal["millisecond", "second", "minute", "day", "unknown"]`
- Extends: `SourceRef` with `occurred_at`, `time_precision`, `role`, and `excerpt_hash`
- Extends: `Record` with `sources: tuple[SourceRef, ...]`; keep `source_ref` as a first-source compatibility view
- Produces: `LedgerMeta`, `LedgerEntry`, `parse_ledger_entry`, `render_ledger_entry`
- Preserves: `format_line`, `parse_line`, `render_raw`, and `parse_raw` as compatibility wrappers

- [ ] **Step 1: Write failing round-trip tests**

```python
def test_v2_entry_round_trips_all_evidence_and_unknown_metadata():
    raw = (
        "- 2026-07-12T14:23:41.582+04:00 ^rec-1 [fact] [imp:8] Concise replies.\n"
        '  <!-- ntrp:meta {"recorded_at":"2026-07-12T10:23:42.014Z",'
        '"sequence":42,"time_precision":"millisecond","scope":{"kind":"user"},'
        '"sources":[{"kind":"chat_message","ref":"s:m","role":"user",'
        '"occurred_at":"2026-07-12T14:23:41.582+04:00"}],"future":{"x":1}} -->'
    )
    entry = parse_ledger_entry(raw)
    assert entry.meta.sources[0].ref == "s:m"
    assert entry.meta.extra == {"future": {"x": 1}}
    assert parse_ledger_entry(render_ledger_entry(entry)) == entry

def test_date_only_legacy_line_keeps_day_precision():
    entry = parse_ledger_entry("- 2025-01-03 ^old [fact] Legacy fact (src:curator)")
    assert entry.occurred_at == "2025-01-03"
    assert entry.meta.time_precision == "day"
```

- [ ] **Step 2: Run the focused tests and confirm the codec is missing**

Run from `apps/server`:

`uv run pytest -q -p no:cacheprovider tests/test_memory_ledger.py tests/test_memory_records.py`

Expected: FAIL because `memory.ledger` and the extended evidence fields do not exist.

- [ ] **Step 3: Implement immutable schema-v2 value objects**

```python
@dataclass(frozen=True)
class LedgerMeta:
    recorded_at: str
    sequence: int
    time_precision: TimePrecision
    scope_kind: str
    scope_key: str | None
    sources: tuple[SourceRef, ...]
    supersedes: tuple[str, ...] = ()
    operation: Literal["record", "retract"] = "record"
    extra: Mapping[str, object] = field(default_factory=dict)

@dataclass(frozen=True)
class LedgerEntry:
    id: str
    text: str
    kind: Kind
    occurred_at: str | None
    meta: LedgerMeta
    pinned: bool = False
    imp: int | None = None
    entity: tuple[str, ...] = ()
```

Validate RFC 3339 offsets without normalizing away the original text. The readable line owns `occurred_at`, ID, kind, importance, entity, and text; the JSON comment must reject duplicate authoritative fields.

- [ ] **Step 4: Route raw-page parsing through the new codec**

Recognize `<!-- ntrp:records schema=2 ... -->`. Parse legacy lines into conservative in-memory entries, but render schema 2 only when the containing page has migrated. Keep visible-page parsing unchanged.

- [ ] **Step 5: Run codec and existing record tests**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_ledger.py tests/test_memory_records.py tests/test_memory_artifacts.py`

Expected: PASS.

- [ ] **Step 6: Commit the codec**

Run: `git add apps/server/ntrp/memory/models.py apps/server/ntrp/memory/ledger.py apps/server/ntrp/memory/pages.py apps/server/tests/test_memory_ledger.py apps/server/tests/test_memory_records.py && git commit -m "feat(memory): add schema v2 ledger codec"`

### Task 2: Append-only Lifecycle and Stable Record Scope

**Files:**
- Modify: `apps/server/ntrp/memory/file_store.py`
- Modify: `apps/server/ntrp/memory/pages.py`
- Modify: `apps/server/ntrp/memory/scopes.py`
- Modify: `apps/server/ntrp/memory/models.py`
- Test: `apps/server/tests/test_memory_records.py`
- Modify: `apps/server/tests/test_memory_scopes.py`

**Interfaces:**
- Produces: `Page.active_entries() -> tuple[LedgerEntry, ...]`
- Produces: `FilePageStore.append_entries(entries: Sequence[LedgerEntry]) -> None`
- Produces: `FilePageStore.history(record_id: str) -> tuple[LedgerEntry, ...]`
- Changes: `update`, `supersede_with`, and `delete` append successor/retract entries
- Changes: new area-specific writes use `MemoryScope("area", area_id)`; migration accepts legacy `project` as an alias
- Preserves: existing `MemoryStore` return types for callers

- [ ] **Step 1: Write failing lifecycle and move tests**

```python
async def test_moving_page_does_not_change_record_scope(store, vault):
    record = await store.add("Area fact", Kind.FACT, scope_kind="area", scope_key="a1")
    move_raw_pair(vault, "topics/a.md", "notes/a.md")
    await store.refresh_from_disk()
    assert (await store.get(record.id)).scope_key == "a1"

async def test_forget_appends_retract_and_keeps_history(store):
    record = await store.add("Temporary", Kind.FACT)
    await store.delete(record.id)
    assert await store.get(record.id) is None
    assert [entry.meta.operation for entry in store.history(record.id)] == ["record", "retract"]
```

Add merge coverage proving one successor can supersede multiple predecessors and unions every evidence reference.

- [ ] **Step 2: Run tests and confirm positional scope and mutation behavior**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_records.py tests/test_memory_scopes.py`

Expected: FAIL because scope is reconstructed from page placement and deletion removes the line.

- [ ] **Step 3: Derive active state from the relationship graph**

Index entries by ID, validate every `supersedes` target, mark targeted records inactive, and apply explicit retract entries last by `(recorded_at UTC, sequence)`. Duplicate IDs and missing targets must be health errors, not last-write-wins behavior.

- [ ] **Step 4: Persist scope and evidence from ledger metadata**

Remove `_scope_for()` from record reconstruction. `_to_record()` must consume `entry.meta.scope_*` and the complete `sources` tuple. Page frontmatter supplies defaults only for newly created page-originated operations.

- [ ] **Step 5: Convert mutations into append operations**

`update` appends a successor, `supersede_with` appends one successor with explicit predecessor IDs, and `delete` appends a retract entry citing the initiating source. Do not rewrite predecessor lines.

- [ ] **Step 6: Run store, scope, search, and artifact regressions**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_records.py tests/test_memory_scopes.py tests/test_memory_profile.py tests/test_memory_artifacts.py`

Expected: PASS.

- [ ] **Step 7: Commit append-only storage**

Run: `git add apps/server/ntrp/memory/file_store.py apps/server/ntrp/memory/pages.py apps/server/ntrp/memory/scopes.py apps/server/ntrp/memory/models.py apps/server/tests/test_memory_records.py apps/server/tests/test_memory_scopes.py && git commit -m "refactor(memory): preserve scope and lifecycle history"`

### Task 3: Recoverable Multi-file Canonical Commits

**Files:**
- Create: `apps/server/ntrp/memory/journal.py`
- Modify: `apps/server/ntrp/memory/file_store.py`
- Modify: `apps/server/ntrp/server/runtime/knowledge.py`
- Test: `apps/server/tests/test_memory_journal.py`
- Modify: `apps/server/tests/test_memory_records.py`

**Interfaces:**
- Produces: `VaultJournal.prepare(files: Mapping[Path, bytes]) -> PreparedCommit`
- Produces: `VaultJournal.commit(files: Mapping[Path, bytes]) -> str`
- Produces: `VaultJournal.recover() -> tuple[str, ...]`
- Produces: `FilePageStore.canonical_revision: str`

- [ ] **Step 1: Write failure-injection tests**

Cover interruption before prepare completion, after prepare marker, after the first target replacement, and after commit marker. Assert startup either completes the full target set or restores the full previous set, never a mixture.

```python
def test_recovery_finishes_commit_after_partial_replace(tmp_path, fail_after_first_replace):
    journal = VaultJournal(tmp_path)
    with pytest.raises(InjectedFailure):
        journal.commit({Path("me.md"): b"new", Path("raw/me.md"): b"raw-new"})
    VaultJournal(tmp_path).recover()
    assert read_pair(tmp_path) in {("old", "raw-old"), ("new", "raw-new")}
```

- [ ] **Step 2: Run tests and confirm no journal exists**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_journal.py`

Expected: FAIL on import.

- [ ] **Step 3: Implement manifest-based prepare/commit/recovery**

Each `.ntrp/journal/<commit-id>/manifest.json` records target, staged file, backup file, and SHA-256. Fsync staged bytes and manifest, write `PREPARED`, replace all targets, validate hashes, then write `COMMITTED`. Recovery validates the manifest and either finishes replacement or restores all backups before removing the journal directory.

- [ ] **Step 4: Make `FilePageStore` stage complete file sets**

Replace consecutive `_write_atomic` calls with one `VaultJournal.commit()` containing every raw sidecar in the record operation. Allow callers such as page editing to add visible user pages and event ledgers to that same commit. Generated prose, indexes, health, and daily projections are excluded. Expose the committed manifest hash as `canonical_revision`.

- [ ] **Step 5: Recover before accepting writes**

Call `journal.recover()` in `KnowledgeRuntime` before constructing curation, consolidation, synthesis, or watchers.

- [ ] **Step 6: Run journal and write-path tests**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_journal.py tests/test_memory_records.py tests/test_memory_filesystem_tools.py`

Expected: PASS.

- [ ] **Step 7: Commit journaled writes**

Run: `git add apps/server/ntrp/memory/journal.py apps/server/ntrp/memory/file_store.py apps/server/ntrp/server/runtime/knowledge.py apps/server/tests/test_memory_journal.py apps/server/tests/test_memory_records.py && git commit -m "feat(memory): journal canonical vault writes"`

### Task 4: Typed Reconciliation, Curator Context, and Watermark Safety

**Files:**
- Create: `apps/server/ntrp/memory/reconciler.py`
- Modify: `apps/server/ntrp/memory/file_store.py`
- Modify: `apps/server/ntrp/memory/curator.py`
- Modify: `apps/server/ntrp/services/session.py`
- Modify: `apps/server/ntrp/context/store.py`
- Test: `apps/server/tests/test_memory_reconciler.py`
- Modify: `apps/server/tests/test_memory_curator.py`

**Interfaces:**
- Produces: `RecordOperation` with `ADD | SUPERSEDE | MERGE | RETRACT | NOOP | ASK`
- Produces: `validate_operations(operations, records, source) -> tuple[RecordOperation, ...]`
- Produces: `FilePageStore.plan_operations(...) -> Mapping[Path, bytes]` without writing
- Produces: `FilePageStore.apply_operations(...) -> str` returning committed revision
- Changes: curator input preserves role, message ID, source timestamp, session ID, and area ID

- [ ] **Step 1: Write failing typed-operation tests**

```python
def test_contradiction_is_not_substring_deduplicated():
    ops = validate_operations(
        [RecordOperation.add("User does not drink coffee", kind=Kind.FACT)],
        records=[fact("User drinks coffee")],
        source=user_message_source(),
    )
    assert ops[0].op == "ADD"  # the model may later select SUPERSEDE; code must not drop it

async def test_failed_second_operation_keeps_watermark_retryable(curator, sessions, store):
    store.fail_operation(2)
    with pytest.raises(InjectedFailure):
        await curator.curate_session("s1")
    assert sessions.watermark("s1") is None
```

Also assert assistant messages retain role `assistant`, session area becomes scope `area:<id>`, invalid targets fail the entire operation batch, and evidence is required unless explicitly `source:unknown`.

- [ ] **Step 2: Run focused tests and confirm current failures**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_reconciler.py tests/test_memory_curator.py`

Expected: FAIL because operations are loosely applied, area is absent, and watermark advances after per-operation errors.

- [ ] **Step 3: Implement the shared operation validator**

```python
class RecordOperation(BaseModel):
    op: Literal["ADD", "SUPERSEDE", "MERGE", "RETRACT", "NOOP", "ASK"]
    text: str | None = None
    kind: Kind | None = None
    scope: MemoryScope | None = None
    target_ids: tuple[str, ...] = ()
    question: str | None = None
```

Validate required fields per operation, target existence, source evidence, scope, and timestamp precision. `ASK` never mutates storage.

- [ ] **Step 4: Preserve structured session envelopes**

Change `_select_batch` to return role-separated messages with stable message sequence/ID and timestamps. Resolve the session's `area_id` once through `recent_session_scopes()` and `get_area()`; pass it explicitly to scope selection and the LLM contract.

- [ ] **Step 5: Apply each curation batch in one journal commit**

Validate the full batch, build all ledger changes, commit once, then advance the watermark to the maximum consumed message sequence. Any extraction, validation, or persistence error leaves the watermark unchanged.

- [ ] **Step 6: Run curator and retrieval tests**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_reconciler.py tests/test_memory_curator.py tests/test_memory_records.py tests/test_memory_profile.py`

Expected: PASS.

- [ ] **Step 7: Commit reconciliation and curator fixes**

Run: `git add apps/server/ntrp/memory/reconciler.py apps/server/ntrp/memory/file_store.py apps/server/ntrp/memory/curator.py apps/server/ntrp/services/session.py apps/server/ntrp/context/store.py apps/server/tests/test_memory_reconciler.py apps/server/tests/test_memory_curator.py && git commit -m "fix(memory): reconcile curated records atomically"`

### Task 5: Direct Tools and Consolidation Correctness

**Files:**
- Modify: `apps/server/ntrp/tools/memory.py`
- Modify: `apps/server/ntrp/memory/consolidate.py`
- Test: `apps/server/tests/test_memory_remember.py`
- Modify: `apps/server/tests/test_memory_consolidate.py`

**Interfaces:**
- Direct `remember` consumes the same `RecordOperation` validator
- Direct `forget` emits `RETRACT` with tool-call evidence
- Consolidation stores a fingerprint only after judgment and operation commit succeed

- [ ] **Step 1: Add failing contradiction and retry tests**

Test exact duplicates as `NOOP`, complementary facts as separate records, contradictions as model-selected `SUPERSEDE` or `ADD`, and unavailable reconciliation as an explicit error rather than substring acceptance. Test `None` judgment and apply failure both leave consolidation fingerprint absent.

- [ ] **Step 2: Run focused tests**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_remember.py tests/test_memory_consolidate.py`

Expected: FAIL on substring deduplication and premature fingerprint writes.

- [ ] **Step 3: Route direct tools through reconciliation**

Keep exact normalized equality as a deterministic duplicate fast path. Send every non-identical candidate through typed reconciliation. Build evidence from the tool call and triggering user message when available; never synthesize a line-ID source.

- [ ] **Step 4: Move consolidation fingerprint persistence**

Order the successful path as `judge -> validate -> apply canonical commit -> write fingerprint`. `None`, invalid operations, or failed apply must remain retryable.

- [ ] **Step 5: Run tool and consolidation regressions**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_remember.py tests/test_memory_consolidate.py tests/test_memory_filesystem_tools.py`

Expected: PASS.

- [ ] **Step 6: Commit correctness fixes**

Run: `git add apps/server/ntrp/tools/memory.py apps/server/ntrp/memory/consolidate.py apps/server/tests/test_memory_remember.py apps/server/tests/test_memory_consolidate.py && git commit -m "fix(memory): reconcile direct and consolidated writes"`

### Task 6: Automatic Backup, Migration, and Health Validation

**Files:**
- Create: `apps/server/ntrp/memory/migrate_ledger_v2.py`
- Modify: `apps/server/ntrp/memory/artifacts.py`
- Modify: `apps/server/ntrp/memory/file_store.py`
- Modify: `apps/server/ntrp/server/runtime/knowledge.py`
- Test: `apps/server/tests/test_memory_ledger_migration.py`
- Create: `apps/server/tests/test_memory_health.py`

**Interfaces:**
- Produces: `migrate_vault_to_v2(root: Path) -> MigrationReport`
- Produces: `validate_vault(root: Path) -> VaultHealth`
- Migration runs after journal recovery and before memory writes/watchers

- [ ] **Step 1: Create migration fixtures and failing tests**

Cover clean legacy records, duplicate identical IDs, conflicting duplicate IDs, malformed lines, missing metadata, date-only records, interrupted staging, and a second idempotent run. Assert a full `.ntrp/backups/<timestamp>/` exists before target replacement.

- [ ] **Step 2: Run migration tests and confirm the migrator is missing**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_ledger_migration.py tests/test_memory_health.py`

Expected: FAIL on import and missing validation fields.

- [ ] **Step 3: Implement staged migration**

Parse the source vault, copy it to backup, render into `.ntrp/maintenance/migration-v2/<run-id>/`, validate every staged page, then journal-commit staged targets. Collapse byte-equivalent duplicate IDs; allocate new IDs for conflicting duplicates and update internal relationship references. Record `time_precision: day` for legacy dates and `unknown` when absent.

- [ ] **Step 4: Expand memory health**

Report schema version, last migration, backup path, duplicate IDs, invalid relationship targets, malformed metadata, missing evidence, invalid scope, timestamp precision violations, and interrupted journals.

- [ ] **Step 5: Wire blocking startup order**

Runtime order must be: journal recovery, migration detection/staging/commit, vault validation, then store services and watcher. A failed migration leaves the source untouched and stops memory writes with the exact file and error.

- [ ] **Step 6: Run the complete server memory suite and lint**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_*.py`

Run: `uv run ruff check ntrp tests`

Expected: PASS.

- [ ] **Step 7: Commit migration and health checks**

Run: `git add apps/server/ntrp/memory/migrate_ledger_v2.py apps/server/ntrp/memory/artifacts.py apps/server/ntrp/memory/file_store.py apps/server/ntrp/server/runtime/knowledge.py apps/server/tests/test_memory_ledger_migration.py apps/server/tests/test_memory_health.py && git commit -m "feat(memory): migrate vaults to ledger schema v2"`

## Completion Gate

- [ ] Run the existing lexical recall evaluation and record the baseline/result without weakening probes.
- [ ] Manually inspect a migrated fixture: visible page, raw sidecar, backup, journal state, and health report.
- [ ] Confirm a page move preserves ID, scope, evidence, and active state.
- [ ] Confirm an injected canonical-write failure leaves the curator watermark unchanged.
- [ ] Do not begin page-edit event work until all invariants above pass.
