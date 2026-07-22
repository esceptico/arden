# Memory Page Events and Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users organize and edit visible memory pages freely while every change becomes a revision-safe event, reconciles into canonical records, and feeds rebuildable timelines and indexes.

**Architecture:** A `PageEditService` owns preview/apply/external-ingest flows over SHA-256 revisions. Accepted edits journal the page, exact patch, and resolved record operations together. Indexes, backlinks, synthesis, and daily pages are projections keyed by canonical revision.

**Tech Stack:** Python 3.13, FastAPI, Pydantic, `difflib`, Markdown/YAML parsing, pytest, Ruff

## Global Constraints

- This plan starts only after the ledger-core completion gate passes.
- Every user-visible save records an exact patch and actor.
- A stale base revision never overwrites current content.
- `ASK` never retracts memory until the user resolves it.
- External edits already changed the file; unresolved questions become post-save review.
- Engine-origin writes never re-enter as user edits.
- User prose outside managed markers is never rewritten.
- Daily pages, indexes, backlinks, and synthesis bases remain rebuildable projections.
- Do not use keyword or regex heuristics to infer semantic memory operations.

---

### Task 1: Open Vault Filesystem and Managed Directory Indexes

**Files:**
- Create: `apps/server/arden/memory/vault_index.py`
- Modify: `apps/server/arden/memory/artifacts.py`
- Modify: `apps/server/arden/tools/memory.py`
- Modify: `apps/server/arden/server/runtime/knowledge.py`
- Create: `apps/server/tests/test_memory_vault_index.py`
- Modify: `apps/server/tests/test_memory_artifacts.py`
- Modify: `apps/server/tests/test_memory_filesystem_tools.py`

**Interfaces:**
- Produces: `VaultIndexer.scan() -> IndexReport`
- Produces: `VaultIndexer.render_updates() -> Mapping[Path, bytes]`
- Expands artifact reads/search to arbitrary Markdown and text outside `raw/` and `.arden/`
- Preserves user prose outside `<!-- arden:index:start -->` markers

- [ ] **Step 1: Write failing arbitrary-path and managed-block tests**

```python
def test_nested_user_file_is_searchable_and_indexed(vault):
    write(vault / "research/models/notes.md", "---\nsummary: Model notes\n---\n# Notes")
    report = VaultIndexer(vault).scan()
    assert "research/" in managed_block(vault / "index.md")
    assert "notes.md — Model notes" in managed_block(vault / "research/models/README.md")
    assert report.missing_descriptions == ()

def test_index_update_preserves_user_prose(vault):
    write(vault / "index.md", "My intro\n\n<!-- arden:index:start -->old<!-- arden:index:end -->\nFooter")
    VaultIndexer(vault).apply()
    assert read(vault / "index.md").startswith("My intro")
    assert read(vault / "index.md").endswith("Footer")
```

Also test symlink rejection, ignored engine namespaces, moves, deletes, existing descriptions, and `Needs description` health output.

- [ ] **Step 2: Run tests and confirm current path restrictions**

Run from `apps/server`:

`uv run pytest -q -p no:cacheprovider tests/test_memory_vault_index.py tests/test_memory_artifacts.py tests/test_memory_filesystem_tools.py`

Expected: FAIL because artifact path allowlists reject arbitrary directories and no managed README writer exists.

- [ ] **Step 3: Implement safe open-file discovery**

Permit regular `.md` and `.txt` files under the resolved vault root. Exclude `raw/`, `.arden/`, generated health files, symlinks, sockets, and paths escaping the root. Arbitrary files are searchable resources, not automatically parsed as records.

- [ ] **Step 4: Implement deterministic managed blocks**

List immediate children only. Resolve descriptions in order: `summary` frontmatter, existing managed description, first meaningful heading/sentence, then `Needs description`. Sort directories before files using normalized casefolded paths.

- [ ] **Step 5: Schedule index updates after canonical writes**

`render_updates()` must be side-effect free. Run it after the triggering canonical commit and persist its projection separately; failure marks the index stale and schedules retry without rolling back knowledge. A maintenance scan uses the same projection path after external filesystem changes.

- [ ] **Step 6: Expose a compact root map to resident context**

Return root entries and descriptions only; deeper traversal stays tool-driven. Do not embed all nested content into prompts.

- [ ] **Step 7: Run focused tests and commit**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_vault_index.py tests/test_memory_artifacts.py tests/test_memory_filesystem_tools.py`

Expected: PASS.

Run: `git add apps/server/arden/memory/vault_index.py apps/server/arden/memory/artifacts.py apps/server/arden/tools/memory.py apps/server/arden/server/runtime/knowledge.py apps/server/tests/test_memory_vault_index.py apps/server/tests/test_memory_artifacts.py apps/server/tests/test_memory_filesystem_tools.py && git commit -m "feat(memory): index open vault directories"`

### Task 2: Page Revisions and Exact Event Ledger

**Files:**
- Create: `apps/server/arden/memory/page_events.py`
- Create: `apps/server/arden/memory/page_edit_service.py`
- Modify: `apps/server/arden/memory/artifacts.py`
- Create: `apps/server/tests/test_memory_page_events.py`

**Interfaces:**
- Produces: `page_revision(content: bytes) -> str`
- Produces: `PageEditEvent`, `PageEditPreview`, `PageEditDecision`
- Produces: `PageEditService.preview(...)`, `apply(...)`, and `history(...)`
- Stores events in `raw/events/YYYY-MM-DD.md` using configured local timezone

- [ ] **Step 1: Write failing revision and event round-trip tests**

```python
async def test_apply_commits_page_patch_event_and_operations_together(service, vault):
    base = read_bytes(vault / "topics/a.md")
    preview = await service.preview(
        path="topics/a.md",
        base_revision=page_revision(base),
        content=base + b"\nA durable statement.\n",
        actor="user:desktop",
    )
    event = await service.apply(preview.id, decisions={})
    assert event.patch == unified_patch(base, read_bytes(vault / "topics/a.md"))
    assert event.base_revision == page_revision(base)
    assert event.result_revision == page_revision(read_bytes(vault / "topics/a.md"))
    assert event.operations[0].sources[0].ref == f"page_edit:{event.id}"
```

Add tests for millisecond timestamp plus original offset, sequence tie-breaking, actor, exact patch, pending reconciliation, restart-safe preview expiry, and event parse/render round trips.

- [ ] **Step 2: Run tests and confirm page-event support is absent**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_page_events.py`

Expected: FAIL on missing modules.

- [ ] **Step 3: Define event and preview contracts**

```python
class PageEditPreview(BaseModel):
    id: str
    path: str
    base_revision: str
    result_revision: str
    patch: str
    operations: tuple[RecordOperation, ...]
    questions: tuple[PageEditQuestion, ...]

class PageEditEvent(BaseModel):
    id: str
    occurred_at: str
    sequence: int
    actor: str
    origin: Literal["desktop", "external", "agent", "synthesis"]
    path: str
    base_revision: str
    result_revision: str
    patch: str
    operations: tuple[AppliedPageOperation, ...]
    reconciliation: Literal["applied", "pending", "needs_review"]
```

Persist the accepted preview payload under `.arden/maintenance/page-edit-previews/` until applied or expired so restart cannot change its semantics.

- [ ] **Step 4: Implement structural preview analysis**

Compare the base and candidate Markdown AST/blocks and send only changed durable statements plus exact context to the model-backed reconciler. Formatting, ordering, and wording-only changes may return `NOOP`; semantic deletion may return `RETRACT` or `ASK`. No text heuristic may choose an operation.

- [ ] **Step 5: Implement one-commit apply**

Re-read the current page and reject if its revision differs from `base_revision`. Require a decision for each `ASK`; `Note only` resolves to `NOOP`, `Forget memory` resolves to validated `RETRACT`. Combine `FilePageStore.plan_operations()` with candidate page and event-ledger bytes, then commit them in one journal transaction. Trigger index and other derived updates only after that commit.

- [ ] **Step 6: Support analysis-unavailable saves**

Allow explicit save-as-pending: commit page plus event with the exact patch and no semantic mutation. A retry reconciles that stored event patch once and appends resulting lifecycle operations; it never diffs against newer page content.

- [ ] **Step 7: Run tests and commit**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_page_events.py tests/test_memory_records.py tests/test_memory_journal.py`

Expected: PASS.

Run: `git add apps/server/arden/memory/page_events.py apps/server/arden/memory/page_edit_service.py apps/server/arden/memory/artifacts.py apps/server/tests/test_memory_page_events.py && git commit -m "feat(memory): record revision-safe page edits"`

### Task 3: Page-edit HTTP Contract and Conflict Responses

**Files:**
- Modify: `apps/server/arden/server/routers/memory.py`
- Modify: `apps/server/arden/server/runtime/knowledge.py`
- Modify: `apps/server/tests/test_memory_router.py`

**Interfaces:**
- `POST /admin/memory/page-edits/preview`
- `PUT /admin/memory/page-edits/apply`
- `GET /admin/memory/page-edits/history?path=<path>`
- `409` conflict includes current content, current revision, base revision, and candidate revision

- [ ] **Step 1: Write failing router contract tests**

Test preview is non-mutating, unresolved `ASK` returns 422, stale apply returns 409 without writes, accepted apply returns event/revision, pending save is explicit, and history is newest-first with stable pagination.

- [ ] **Step 2: Run router tests**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_router.py`

Expected: FAIL with 404 for the page-edit routes.

- [ ] **Step 3: Add request/response models and dependency wiring**

```python
class PageEditPreviewRequest(BaseModel):
    path: str
    base_revision: str
    content: str
    actor: str = "user:desktop"

class PageEditApplyRequest(BaseModel):
    preview_id: str
    decisions: dict[str, Literal["note_only", "forget_memory"]]
    save_pending: bool = False
```

Keep page paths in request bodies/query parameters so the existing catch-all artifact-read route cannot swallow action suffixes.

- [ ] **Step 4: Map domain failures precisely**

Return 404 for missing page, 403 for machine-only page, 409 for stale revision, 422 for unresolved questions/invalid decisions, and 503 only when analysis is unavailable and the caller did not request pending save.

- [ ] **Step 5: Run tests and commit**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_router.py tests/test_memory_page_events.py`

Expected: PASS.

Run: `git add apps/server/arden/server/routers/memory.py apps/server/arden/server/runtime/knowledge.py apps/server/tests/test_memory_router.py && git commit -m "feat(server): expose memory page edit review"`

### Task 4: External Edit Ingestion and Origin-loop Suppression

**Files:**
- Modify: `apps/server/arden/memory/file_store.py`
- Modify: `apps/server/arden/memory/page_edit_service.py`
- Modify: `apps/server/arden/server/app.py`
- Modify: `apps/server/arden/server/runtime/knowledge.py`
- Create: `apps/server/tests/test_memory_external_edits.py`
- Modify: `apps/server/tests/test_memory_filesystem_tools.py`

**Interfaces:**
- Produces: `ObservedFileChange(path, before, after, base_revision, result_revision)`
- Produces: `PageEditService.ingest_external(change) -> PageEditEvent | None`
- Engine writes register `(path, result_revision, origin)` before watcher notification

- [ ] **Step 1: Write failing watcher tests**

Cover an Obsidian-style edit, external deletion with `ASK`, engine write suppression, rapid two-edit ordering, restart snapshot recovery, and SSE publication only after event ingestion state is durable.

- [ ] **Step 2: Run tests and confirm watcher only returns paths**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_external_edits.py tests/test_memory_filesystem_tools.py`

Expected: FAIL because `refresh_from_disk()` has no before/after snapshot or origin.

- [ ] **Step 3: Persist the observed revision map**

Store last observed editable-page bytes/revisions under `.arden/maintenance/observed-pages.json` plus content-addressed bases. Update it only after the external event is committed or an engine-origin revision is acknowledged.

- [ ] **Step 4: Ingest external changes through the same event contract**

Append an `origin: external` event with the exact patch. Auto-apply validated unambiguous operations. Store ambiguous operations as `needs_review`; because the visible file already changed, review actions resolve memory effects only.

- [ ] **Step 5: Suppress engine-origin loops**

Journaled engine writes register their exact result revision and origin. The watcher consumes that marker once; a path-only or time-window suppression is not sufficient.

- [ ] **Step 6: Publish enriched SSE state**

Continue `memory_changed`, adding revision and `review_required` metadata without breaking existing clients. Publication happens after canonical event persistence.

- [ ] **Step 7: Run tests and commit**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_external_edits.py tests/test_memory_filesystem_tools.py tests/test_memory_page_events.py`

Expected: PASS.

Run: `git add apps/server/arden/memory/file_store.py apps/server/arden/memory/page_edit_service.py apps/server/arden/server/app.py apps/server/arden/server/runtime/knowledge.py apps/server/tests/test_memory_external_edits.py apps/server/tests/test_memory_filesystem_tools.py && git commit -m "feat(memory): ingest external page edits"`

### Task 5: Revision-based Synthesis and Three-way Merge

**Files:**
- Create: `apps/server/arden/memory/merge.py`
- Modify: `apps/server/arden/memory/synthesize.py`
- Modify: `apps/server/arden/memory/page_edit_service.py`
- Create: `apps/server/tests/test_memory_synthesis_merge.py`
- Modify: `apps/server/tests/test_memory_records.py`

**Interfaces:**
- Produces: `three_way_merge(base, current, generated) -> MergeResult`
- Synthesis freshness consumes `canonical_revision`, not a date
- Generated bases live at `.arden/maintenance/synthesis-bases/<page-key>/<revision>.md`
- Accepted merges emit `SYNTHESIS_MERGE` events

- [ ] **Step 1: Write failing freshness and merge tests**

Test two same-day record changes, non-overlapping user/generated changes, overlapping changes, missing base, formatting-only user changes, and exact synthesis event history.

- [ ] **Step 2: Run tests and confirm day-only freshness**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_synthesis_merge.py tests/test_memory_records.py`

Expected: FAIL because `prose_synced` stores only `YYYY-MM-DD` and synthesis overwrites the body.

- [ ] **Step 3: Key generated output to canonical revision**

Replace `prose_synced` with `generated_from_revision`. Persist the last generated body as a rebuildable merge base after accepted generation.

- [ ] **Step 4: Add conservative three-way merging**

Apply non-overlapping generated hunks onto the current user page. On overlap or missing base, return a reviewable candidate and leave the visible page untouched. Never resolve conflicts with fuzzy or keyword matching.

- [ ] **Step 5: Record accepted generated changes**

Use the same event ledger with actor/origin `synthesis`, exact base/result revisions, patch, and source canonical revision. Mark engine origin to suppress watcher re-ingestion.

- [ ] **Step 6: Run tests and commit**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_synthesis_merge.py tests/test_memory_page_events.py tests/test_memory_records.py`

Expected: PASS.

Run: `git add apps/server/arden/memory/merge.py apps/server/arden/memory/synthesize.py apps/server/arden/memory/page_edit_service.py apps/server/tests/test_memory_synthesis_merge.py apps/server/tests/test_memory_records.py && git commit -m "feat(memory): merge synthesis without losing prose"`

### Task 6: Granular Daily Timeline Projection

**Files:**
- Create: `apps/server/arden/memory/daily.py`
- Modify: `apps/server/arden/server/runtime/knowledge.py`
- Modify: `apps/server/arden/memory/artifacts.py`
- Create: `apps/server/tests/test_memory_daily.py`

**Interfaces:**
- Produces: `DailyProjector.render(local_date: date, revision: str) -> DailyProjection`
- Consumes source evidence, record lifecycle changes, and page events
- Daily frontmatter records `generated_from_revision` and configured timezone

- [ ] **Step 1: Write failing temporal projection tests**

Cover millisecond ordering, equal timestamps with sequence, source offsets, date-only legacy entries, local-midnight boundaries, a daylight-saving transition, multiple source references, page edits, and same-day regeneration.

- [ ] **Step 2: Run tests and confirm no granular daily projector exists**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_daily.py`

Expected: FAIL on import.

- [ ] **Step 3: Build structured timeline events**

Normalize timestamps to UTC only for ordering, group by configured local calendar date, retain original offsets/precision, and use `sequence` as the final tie-breaker. One meaningful action/change is one event.

- [ ] **Step 4: Group only through a structured model decision**

The optional grouping contract returns explicit source event IDs and a summary. Validate that each input appears exactly once. On unavailable/invalid judgment, render ungrouped events; do not fall back to keyword rules.

- [ ] **Step 5: Preserve edited daily pages**

Treat generated daily content as a synthesis base. Rebuild untouched pages directly; replay/merge user page events onto the new base for edited pages; surface conflicts for review.

- [ ] **Step 6: Run tests and commit**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_daily.py tests/test_memory_synthesis_merge.py tests/test_memory_artifacts.py`

Expected: PASS.

Run: `git add apps/server/arden/memory/daily.py apps/server/arden/server/runtime/knowledge.py apps/server/arden/memory/artifacts.py apps/server/tests/test_memory_daily.py && git commit -m "feat(memory): project granular daily timelines"`

### Task 7: Rebuildable Link and Backlink Index

**Files:**
- Create: `apps/server/arden/memory/link_index.py`
- Modify: `apps/server/arden/server/routers/memory.py`
- Modify: `apps/server/arden/server/runtime/knowledge.py`
- Create: `apps/server/tests/test_memory_link_index.py`
- Modify: `apps/server/tests/test_memory_router.py`

**Interfaces:**
- Produces: `LinkIndex.rebuild(artifacts, revision) -> LinkIndexSnapshot`
- `GET /admin/memory/links?path=<path>` returns outgoing links and backlinks with context snippets
- Index stored under `.arden/indexes/links.json`

- [ ] **Step 1: Write failing alias, rename, and context tests**

Cover wikilinks by path/title/alias, unresolved links, renamed pages, duplicate aliases, context snippets, arbitrary user files, and engine namespace exclusion.

- [ ] **Step 2: Run tests and confirm no backlink endpoint exists**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_link_index.py tests/test_memory_router.py`

Expected: FAIL on import and route 404.

- [ ] **Step 3: Implement deterministic link extraction and resolution**

Reuse Markdown wikilink semantics, not regex-only title matching. Store unresolved/ambiguous targets explicitly. Index records canonical page path, link text, heading/block context, and source revision.

- [ ] **Step 4: Rebuild after canonical revision changes**

Link-index failure must not roll back the source write. Retain the last valid snapshot, mark it stale, and schedule retry.

- [ ] **Step 5: Add paginated route and run tests**

Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_link_index.py tests/test_memory_router.py`

Expected: PASS.

- [ ] **Step 6: Commit link indexing**

Run: `git add apps/server/arden/memory/link_index.py apps/server/arden/server/routers/memory.py apps/server/arden/server/runtime/knowledge.py apps/server/tests/test_memory_link_index.py apps/server/tests/test_memory_router.py && git commit -m "feat(memory): index links and backlinks"`

## Completion Gate

- [ ] Run: `uv run pytest -q -p no:cacheprovider tests/test_memory_*.py tests/test_artifact_frontmatter.py`
- [ ] Run: `uv run ruff check arden tests`
- [ ] Manually edit one page through HTTP and one through an external editor; inspect page, event, records, indexes, timeline, and SSE payload.
- [ ] Confirm stale save, synthesis overlap, and ambiguous deletion each preserve user content and require review.
- [ ] Confirm the daily page rebuilds twice on the same local day after distinct canonical revisions.
