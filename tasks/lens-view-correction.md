# Lens-as-VIEW correction — locked model + checklist

The Stage-4 build got lenses backwards (made them `kind=LENS` rows in memory_items,
auto-minted per subject, member_of edges, graph nodes/squares). This corrects it.

## Locked model (non-negotiable)
- **Memory = claims only** + claim↔claim edges (evidence / supersedes / contradicts). Only claims are memory participants. The graph shows ONLY claims + these edges.
- **Subject / coreference = a claim attribute** (`canonical_subject`). Aliases are themselves claims. Reconcile resolves the subject + merges/supersedes claims by subject. NO entity rows, NO entity-lenses, NO auto-minting.
- **A lens is a VIEW, not memory.** A separate `lenses` registry (name + criterion + scope + detail). It **links to / projects the relevant claims and renders a view over them** — never a row in memory_items, never a graph node, no member_of edges. Create/delete a lens touches zero claims.
- **A lens view can GROUP + profile.** A `persons` lens projects person-claims, grouped by `canonical_subject` → lists *Regina Lin · Wife · Kevin Gu · …*, each expandable to a **profile** synthesized from that subject's claims, with drill-down links to the underlying claims/evidence. The "profile" IS the projection of a subject's claims — not a stored entity.

## Checklist (fix all, autonomously)
1. **Structural** (workflow wt6br10p4, running): lenses out of memory_items (claims-only + a separate lenses registry); reconcile = subject-on-claim, no minting/member_of; lens = view/projection layer; API = claim-graph (whole + rooted) + lens-as-view endpoints.
2. **Graph UI**: claims only, **circles (NO squares/rect anywhere)**, color/size encodes provenance/corroboration; **whole-graph by default** with click-to-focus; **fix label overlap** (hover/selected labels or collision-avoided). The screenshot showed 4 squares + colliding "The user wants assistants to…" labels — that must be gone.
3. **Lens create → LLM-generated criterion**: you give a name/intent ("Bugs", "persons"), the LLM **synthesizes** the one-sentence inclusion criterion (editable). NOT manual blank entry (it was generated before; the build regressed it).
4. **Detail level → plain labels**: `gist/structured/dossier` → **Summary / List / Full** (or simpler / cut entirely). No internal jargon in the UI.
5. **Lens view = grouped + profiles** (the persons-lens shape above): grouped-by-subject, per-subject profile synthesized from claims, drill-down to claims.
6. Heuristic ban holds (LLM judges; embed+FTS recalls; no keyword/regex/threshold gate). Grep-gate.
7. **Purge live ~/.arden/memory.db** after (schema changed again) so the corrected schema rebuilds clean on restart.

## Autonomous plan (user asleep)
- Let wt6br10p4 (structural) finish → verify hard (boot, build, structural gate: no lens rows/nodes/squares).
- Run a follow-up workflow for the view-layer UX: items 3, 4, 5 (criterion auto-gen, plain labels, grouped persons-profiles) + the graph polish (2).
- Verify, purge DB, **commit**, leave a morning summary. Do NOT push.

## DONE (autonomous, overnight)
- `fa1084a4` structural: lenses-as-views, claims-only memory_items + canonical_subject, separate `lenses` registry + `lens_membership_cache`, no Kind/lens cols/MEMBER_OF, graph circles-only. Verified: 996 tests, gate clean by hand.
- `e3aefd23` lens UX: criterion auto-synthesized on create (name → LLM criterion, editable); plain detail labels (Summary/List/Full, no Gist/Dossier); `render_mode=grouped_by_subject` → persons lens groups by canonical_subject into per-subject profiles w/ drill-down; deleted 3 stale contract docs (re-violation landmines). Verified: 1004 tests, desktop typecheck+build, heuristic+model gate clean.
- DB purged (stale Stage-4 schema → backed up `memory.db.bak-prelensview-*`); corrected schema builds on restart.
- All on `main`, NOT pushed. Checklist items 1–7 complete.
