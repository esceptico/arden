# ARDEN Memory Rebuild — Build Plan

Design source of truth: `~/vault/Memory Consolidation/Memory — vision (new spec).md` + `Lens — spec.md` (+ `_grounding (validated).md`, `_devils-advocate.md`).
Branch: `feature/memory-rebuild` (NOT main). Orchestration: `/workflows`, **stage-by-stage** — commit + user review between stages.
Hard rules: server stays bootable after each commit; ruff + pytest green per stage; no test-plan bloat; remove aggressively (committed → restorable).

## Stage 0 — Prep (main loop, not a workflow)
- [ ] Create `feature/memory-rebuild` branch.
- [ ] **(USER) stop the server.**
- [ ] Backup `~/.arden/memory.db` once → `.bak-rebuild-<ts>`, then drop/delete it (start empty). Backups remain the replay/eval corpus.

## Stage 1 — Strip old memory (workflow: map → remove → verify-boot) → commit
- Fan-out: impact-map who imports `apps/server/arden/memory/*` (runtime/knowledge.py, routers admin_memory + learnings, agent-loop injection/activation, memory tools, tests).
- Remove memory internals: `episodes, items_store, lenses, lens_pass, lens_author, activation, pattern_finder, contradictions, learnings, skill_inducer, buffers_store, runtime, retrieval, models, store/, connectors/*`.
- Cut/stub all call sites so the server still imports & boots. Delete `tests/memory/`.
- Verify: server boots, ruff clean, pytest collects/green. Commit: "strip memory to bare".

## Stage 2 — Invariants → commit
- Foundational layer only (vision §1/§3): one object table (`memory_items`) + role-typed edge table, scope, provenance, validity (no-delete/invalidate), immutable-raw + source_refs. Models + store + schema/migration + minimal schema tests. NO pipeline yet.
- Verify; commit: "memory invariants: object+edge model".

## Stage 3 — Memory to working stage (workflow: components → integrate → verify) → commit
- Capture (boundary) → **Admit** (LLM gate, predictive-value rubric, cost-bounded) → **Extract** (atomic self-contained claims, evidence-linked) → **Reconcile** (recall→LLM ADD/UPDATE/NOOP/CONTRADICT, canonical-subject recall via alias+embed+FTS) → **background Consolidate/Lint + watermarks** → **Retrieve**. `remember()` = same path, just a tool.
- Honor §13 cost budget (single→low-double-digit calls/exchange). Validate against replay corpus.
- Verify; commit: "memory working: admit→reconcile→consolidate".

## Stage 4 — Lens → commit
- Materialized-view lens (criterion + LLM membership scoring), **entity = constrained lens** (exclusive/transitive identity), candidate-recall investment, page projection (structured edits, not free-prose reparse), scale modes (incremental/on-demand/batch).
- Verify; commit: "memory lenses".

## Orchestration notes
- Within a stage: fan out *analysis + component drafting*, converge with ONE integration agent + ONE verify agent (no parallel agents editing the same module → avoids incoherent merges).
- Across stages: sequential; each stage gated by a commit the user reviews.
- Main loop (me) owns: branch, DB purge, commits, the server-stop handshake. Workflows own: code gen, removal, verification.

## Review (complete)
- Stage 0: branch `feature/memory-rebuild`; live DB backed up → `memory.db.bak-rebuild-20260601-015521` + purged.
- Desktop UI fix → `main` (`649fe758`), branch rebased onto it.
- Stage 1 `8212f20f` strip memory to bare — 776 tests, server boots.
- Stage 2 `9ea16bae` invariants — object+edge model, single-axis validity, trust=transparent columns, never-delete. (review pass: fixed search-leaks-superseded, supersede atomicity, _now→public.)
- Stage 3 `b0a5e52a` pipeline — capture→admit→extract→reconcile→consolidate/lint→retrieve + remember/recall tools + /init alignment. Caught & removed TWO resurrected keyword-list heuristics (admit `_TOOL_STATUS_MARKERS`/`_PREFIXES`); fixed test-hang (aiosqlite teardown). 
- Stage 4 `d9eb183a` lenses — materialized views, LLM criterion-membership, page projection (claim-id anchors), scale modes, entity=constrained-lens reusing reconcile substrate.
- Final: heuristic-free throughout (LLM judgment for every decision; embed+FTS for recall; no keyword/regex/threshold gates). 951 tests pass, boot OK, ruff clean, offline test fakes, live DB never touched. Branch also carries desktop merges (user's parallel work). Nothing pushed; server not restarted.
- Stage 5 `1c9740f0` UI — backend /admin/memory router (12 routes: claims, lenses+pages, provenance graph, fts/retrieve search, structured page write-back, lens lifecycle) + rewritten desktop UI (MemoryModal → Lenses·Claims·Graph; lens-as-editable-markdown-page hero; woven-glass on Flexoki tokens; motion/react action animations). Removed orphaned learnings UI + MemoryItemsPane. 986 backend tests; desktop typecheck+build green.
- Open for the user (morning): re-enable the desktop UI (you disabled it); **visual QA the new Memory tab** (headless build is green but rendered look needs your eye); restart server to bring the /admin/memory API + new pipeline live; run `/init` to pre-populate profile/identity (seeds user entity-lens aliases → closes User≠Timur from data); decide merge/PR. Note: corrections-`learnings` feature was removed (no router in new model) — if you want a corrections-review UI, it'd be built fresh.
