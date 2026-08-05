# Implementation

> A checked item means implemented, not verified. See `verification.md` for observed proof.

## Intended outcome

Close out the six live issues from `research.md` per the user's 2026-08-05 direction:
remove Memory Dream instead of fixing its scheduling (P-01 → P-01R), apply the two data
repairs, and land the two code fixes — without over-engineering or defensive code.

## Checklist

- [x] **P-01R — Remove Memory Dream entirely** (supersedes P-01; user: "remove dream automation it's a bit useless")
  - `apps/server/arden/constants.py`: dropped `BUILTIN_MEMORY_DREAM_ID` / `MEMORY_DREAM_AT`;
    added `"builtin-memory-dream"` to `RETIRED_BUILTIN_AUTOMATION_IDS` so seeding deletes the
    stored automation on next boot.
  - `automation/builtins.py`: removed the dream `BuiltinSpec`.
  - `server/runtime/automation.py`: removed `get_fact_dream` wiring, the `memory_dream` handler
    registration, and `_run_memory_dream`.
  - `server/runtime/core.py`: removed `_get_fact_dream` and the dream imports.
  - `wiki/health.py`: removed `WikiHealthIssueOwner.DREAM`; `fact_page_owner` collapsed to a
    constant and was inlined as `SYNTHESIS` at both call sites (`wiki/health.py`,
    `server/wiki_health.py`).
  - Deleted `memory/facts/dream.py`, `memory/facts/completion_dream.py`,
    `tests/test_fact_dream.py`, `tests/test_fact_dream_completion.py`; updated
    `test_automation_store.py`, `test_fact_runtime.py`, `test_runtime_wiki_health.py`,
    `test_prompt_consistency.py`; reworded the one "Dream" mention in `wiki/service.py`'s
    insights directory contract.
  - Existing dream-produced pages (`insights/2026-07.md`, `insights/2026-08.md`) are left in
    place as ordinary content; health issues on them now attribute to Synthesis.

- [x] **P-02 — Repoint stale tool-result blob paths** (F-06, F-07)
  - Backed up all 935 `tool_results` rows to `~/.arden/sessions.tool_results.backup-20260805.sql`.
  - `UPDATE tool_results SET blob_path = replace(blob_path, '/Users/escept1co/.ntrp/',
    '/Users/escept1co/.arden/') WHERE blob_path LIKE '/Users/escept1co/.ntrp/%'` → 495 rows.
  - Run against the live WAL-mode DB (single statement, 5 s busy timeout); server left running.
  - The blob-ref-at-read-time follow-up was **not** taken — one absolute-path repair is enough;
    revisit only if the directory ever moves again.

- [x] **P-05 — Evict the dead `memory_line` partition** (F-12)
  - Deleted 78 `items` rows and their `items_vec` embeddings from `~/.arden/search.db` via a
    `sqlite_vec`-loaded connection (the bare CLI lacks the vec0 module; `items_fts` is
    trigger-synced, `items_vec` is not — mirrored the code's own `clear_source` order).
  - Removed the stale `memory_line` mention from the comment at `search/store.py:399`.

- [x] **P-03 — Read-gate run-scoping** (F-08, F-09) — **already fixed before this pass; no change made**
  - The working tree already carries the 2026-08-04 fix: `RunRegistry._resource_observations`
    keyed by session, injected by reference into every `RunState` (`server/state.py:265-266`),
    threaded through `create_agent(resource_observations=...)`, with
    `downgrade_resource_observations` handling compaction.
  - All window failures at 13:xx/17:0x UTC predate the fix; the two later ones (18:17:41,
    2026-08-05T00:42:53) each follow a server restart within ~1 minute — in-memory receipts do
    not survive restarts, so those are legitimate first-touch re-read demands.

- [x] **P-04 — GeneratedRegionConflictError structured failure** (F-10) — **already fixed in `443aead9`; no change made**
  - Commit `443aead9` (2026-08-04 08:50 UTC, in HEAD) rerouted `next`/`_wait_for_state` through
    `_completed_state()`, which maps the exception to `wiki_generated_region_conflict` with its
    recovery action. Covered by `tests/test_wiki_maintenance_agent.py:154`. The 2026-08-03
    tracebacks predate the commit; no recurrence since.

- [x] **P-06 — Determine whether fact maintenance is degraded** (F-14, F-15) — *investigated 2026-08-05; verdict: not degraded, but structurally scope-blind*
  - Transcripts are unavailable: consolidate runs have no `chat_session_id`, and their ephemeral
    sessions persist zero `session_messages` / `session_events` / `tool_results` rows — only
    tool-call status. Evidence had to come from the reviewed facts themselves.
  - Reconstructed the 2026-08-04T23:37 run's review set (the 6 fact events after the morning
    watermark). Five are genuinely distinct events — `amended 0; merged 0` is **correct** for them.
  - The sixth is a real near-duplicate pair the reviewer **cannot see**: 17:13:20
    "submitted applications to three openai roles…" (scope `area:area_7ae5c98cc338`, manual
    `fact_changes`) vs 17:13:52 "confirmed that he had just submitted applications to the three
    openai positions" (scope `user`, memory-capture) — same event, recorded twice 32 s apart by
    two writers. `_prepare_cluster` pools candidates with `fact.scope == target.scope`
    (`memory/facts/maintenance/runner.py:452`), so cross-scope duplicates are invisible to
    maintenance **by construction**.
  - Secondary observations: subject drift (`'Timur Ganiev'` vs `'Timur'`) would also weaken
    shared-subject candidate matching; the 23:37 run logged one `invalid_arguments` decision
    (agent self-corrected on retry — the constrained interface worked); capture quality is
    slightly over-permissive if anything ("timur likes research." as a durable fact), so the
    July→August volume drop is the backlog draining, not under-firing.
  - **Design question surfaced, not silently fixed:** should maintenance see cross-scope
    duplicates? Merging across scopes changes the survivor's visibility domain (area vs user),
    so it is an authz/product decision, not a bug fix.

- [x] **P-07 — Wire `fact_search` through the hybrid index** (F-18; user: "wire it properly")
  - `memory/facts/index.py`: `FactIndexProjection` now depends on the **ledger**, not the service
    (it only ever used `revision` and `facts_at`) — this breaks the service↔projection circularity
    so the hook injects at construction instead of by post-hoc field mutation. Added
    `ranked_fact_ids(query, limit)` using the same sync-then-guard pattern as
    `semantic_candidates`.
  - `memory/facts/service.py`: `RankedFactSearch` Protocol (exact call shape, no `Callable[...]`
    ellipsis), `ranked_search` constructor field (`None` = no index configured → substring, the
    documented degraded mode), `FactSearchUnavailableError`, and `_ranked_page` — top-N,
    `has_more=False` (relevance order has no stable cursor), every hit revalidated against
    current ledger state, page bound enforced at the boundary. `include_inactive` archaeology
    stays on the exhaustive substring scan by design.
  - `server/runtime/core.py`: projection constructed in `_init_facts` beside the ledger;
    `FactService(..., ranked_search=projection.ranked_fact_ids)`; the index_sync startup phase
    now only syncs.
  - `tools/facts.py`: `search_unavailable` retryable failure mapping; tool + query descriptions
    now state ranked semantics and the `status='all'` substring behavior.
  - Tests: ranked ordering + visibility filtering, subject filter + limit enforcement,
    unavailable-index error, inactive-scan bypass (`test_fact_service.py`); `ranked_fact_ids`
    serve/unavailable (`test_fact_index.py`).

## Notes

- Constraints applied per user: no defensive code (no fallbacks for the removed dream handler —
  retired-id seeding deletes the stored row outright), no over-engineering (no blob-root
  indirection layer, no dream tombstone owner).
- The dream removal takes full effect on the next server restart (retired-id sweep +
  seed without the spec). Until then the running server still holds the old automation row.
- Nothing committed; per standing preference the user reviews first.
