# Slice 7 — Skill inducer + `is_toolable` gate + `proposal` flow

**Status:** Draft for PM A/B gate, then codex fire. **REVISED 2026-05-28 05:35** — repo-surface corrections + honest gate degradation (see §16 audit). **PRE-REQUISITES: slices 5 + 6 must land first.** Brief is speculative; revise post-slice-6 if claim/contradiction shape diverges.

> **CRITICAL:** the `is_toolable` gate as originally specified in `arden-memory-redesign-spec.md` §3.5 assumes per-claim metadata, per-episode `tool_sequence`, and episode success-signal metadata — **none of which exist on `MemoryItem` today**. Slice 7 implements a **degraded** gate using only `tags` and existing repo methods. The degradation is honest, not silent. See §16 audit log for the explicit list of skipped checks and their re-home plan.
**Prereqs:** slice 6 shipped. Claim layer producing `kind=claim` rows with `is_toolable` field on metadata (added in this slice). Contradiction watcher resolving conflicts. Test baseline ≥ 856 passed.
**Backlog absorbed from `slice-07-backlog.md`:** §2B (`test_knowledge_write_gate.py` — 30 tests, 29 dead; reborn as proposal-gate tests against the new `kind=proposal` flow).
**Out of scope:** UI for skill proposals (post-slice-7, separate UX slice), entity-resolution-driven skill grouping (slice 8+), skill-execution runtime changes (skills already execute via existing `~/.arden/skills/<name>/SKILL.md` loading).

---

## 0. TL;DR

Add the `SkillInducer` that scans `kind=claim` rows for patterns matching the `is_toolable` gate (repetition + determinism + trigger + success-signal per spec §3.5). Gate-passing patterns produce `kind=proposal` rows with a draft SKILL.md body in `/tmp/arden/proposed-skills/`. User approval (via `POST /admin/memory/proposals/{id}/approve`) flips the proposal to `kind=skill`, moves the file to `~/.arden/skills/<name>/SKILL.md`, and writes an evidence-edge back to the claim cluster that induced it. Rejection (`/admin/memory/proposals/{id}/reject`) deletes the draft file and marks the proposal `status='rejected'`. This is the CL payoff: future behavior changes via approved skills.

---

## 1. Goal — concrete

After slice 7 ships:

1. Pass 2 produces a `kind=claim` row (slice 5).
2. **NEW:** Each new claim is scanned via `IsToolableGate.evaluate(claim)` to set `metadata.is_toolable: bool` + `metadata.toolable_reason: str`.
3. **NEW:** `SkillInducer.run()` (daily scheduler + manual endpoint) finds clusters of `is_toolable=True` claims sharing trigger + workflow shape, drafts SKILL.md content via LLM, writes `kind=proposal` rows.
4. **NEW:** `GET /admin/memory/proposals` lists open proposals with diff vs existing skills.
5. **NEW:** `POST /admin/memory/proposals/{id}/approve` promotes proposal → skill: write `kind=skill` row, move SKILL.md from `/tmp/arden/proposed-skills/` to `~/.arden/skills/<name>/`, add `role='derives_from'` edges from skill to source claims, set proposal `status='approved'`.
6. **NEW:** `POST /admin/memory/proposals/{id}/reject` marks proposal `status='rejected'`, deletes draft file.
7. **NEW:** Retrieval surfaces `kind=skill` rows with `reason='skill_match'` when a user query semantically matches a skill's trigger.

**Verification:** seed DB with 4 claims describing a repeated workflow (e.g. "user triages PRs every morning", "user uses gh CLI for triage", "user files notes in Notion after triage", "user assigns reviewers based on file paths"). Run skill-inducer. Assert: 1 proposal row created with draft SKILL.md containing those steps. Approve it. Assert: skill row + file at `~/.arden/skills/<name>/SKILL.md` + edges back to the 4 claims.

---

## 2. Hard scope boundaries

### Files codex MAY touch

**Add:**
- `apps/server/arden/memory/skill_inducer.py` — `SkillInducer`, `IsToolableGate`, `ProposalDraft` dataclass, scoring helpers.
- `apps/server/arden/memory/prompts/is_toolable.txt` — LLM prompt for borderline toolability judgment.
- `apps/server/arden/memory/prompts/skill_draft.txt` — LLM prompt for SKILL.md body drafting.
- `apps/server/tests/memory/test_skill_inducer.py` — ≥ 15 tests (higher bar than slices 5/6 because backlog §2B contributes ~10 ported cases).
- `apps/server/tests/memory/test_proposal_flow.py` — ≥ 8 tests for approve/reject endpoints.

**Modify:**
- `apps/server/arden/memory/pattern_finder.py` — `_persist_claim` calls `IsToolableGate.evaluate_and_tag(claim_id)` after the contradiction watcher fires (so superseded claims don't get the toolable scan). One added line at end of method. No other changes.
- `apps/server/arden/memory/retrieval.py` — extend reason-label set with `'skill_match'`. One branch added.
- Slice-5/6 admin router — add 4 endpoints: `/admin/memory/skill-inducer/run`, `/admin/memory/proposals`, `/admin/memory/proposals/{id}/approve`, `/admin/memory/proposals/{id}/reject`.
- The slice-4/5 scheduler — add `skill-inducer-daily` registration (06:00 UTC, 1-hour after pass-2 scheduler).

### Files codex MUST NOT touch (frozen zones)

- `apps/server/arden/memory/connectors/*`, `buffers_store.py`, `episode_close.py`, `activation.py` — slices 2/3.
- `apps/server/arden/memory/pattern_finder.py` body — EXCEPT the one-line `IsToolableGate.evaluate_and_tag` call. Do NOT refactor pass-1/pass-2 or the slice-6 contradiction hook.
- `apps/server/arden/memory/contradictions.py` — slice 6 module, frozen.
- `apps/server/arden/knowledge/skill_promotions.py`, `write_gate.py`, `fact_consolidation.py`, `contradictions.py` — old knowledge layer. Deprecation comments only. They remain referenced by knowledge routes / desktop UI; removal is post-slice-7 cleanup.
- `~/.arden/skills/*/SKILL.md` files — existing skills are untouched. New skills land in their own directories.

### Files codex MUST NOT delete

No deletions. The old knowledge layer stays in place until a follow-up cleanup slice.

---

## 3. The `is_toolable` gate (DEGRADED — see §16 audit)

Per spec §3.5, four criteria. **Degraded implementation:** repetition + LLM-judged-trigger only. Determinism and success-signal both require episode metadata that doesn't exist on `MemoryItem` today (see §16). They're skipped — codex documents this in §10 and the spec gets a "v1 limitation" footnote.

### 3.1 Repetition (KEPT)

```python
async def _check_repetition(self, claim: MemoryItem) -> tuple[bool, str]:
    # `list_parent_edges` returns this claim's parent edges. Count role='evidence'.
    edges = await self.repo.list_parent_edges(claim.id)
    evidence_count = sum(1 for e in edges if e.role == "evidence")
    if evidence_count < self.min_episodes:  # default 3
        return False, f"only {evidence_count} supporting items (need ≥ 3)"
    return True, f"{evidence_count} supporting items"
```

Note: "supporting items" not "supporting episodes" — evidence chain may include observations and prior claims, not raw episodes. To strictly count episodes, codex would need to recursively follow edges via `list_parent_edges(parent_id)` until hitting `kind='episode'` items. For slice 7 v1, accept any-kind evidence count. Document in §10.

### 3.2 Determinism (SKIPPED in v1)

Spec called for tool-call-sequence Jaccard over evidence episodes. **Impossible today** because:
- `MemoryItem.metadata` doesn't exist (no JSON metadata column)
- No `tool_sequence` is ever stored against episodes at episode-close time
- Adding `tool_sequence` storage requires touching `memory/connectors/chat.py` and `buffers_store.py` — both are frozen-zone slice-2 territory

**Re-home:** a follow-up slice "memory-metadata-column" adds a `metadata JSON` column + `tool_sequence` recording at episode-close, and slice 7 v2 re-enables this check.

For v1, `_check_determinism` is a stub that returns `(True, "skipped — see §16")`. The gate still functions; it just permits more claims through than the full spec would. Risk: more noisy proposals. Mitigation: user-approval gate is the safety net.

### 3.3 Trigger identification (KEPT, but stored as tag)

```python
async def _check_trigger(self, claim: MemoryItem) -> tuple[bool, str]:
    # Use LLM judge to extract a trigger phrase from the claim + immediate evidence
    edges = await self.repo.list_parent_edges(claim.id)
    evidence_ids = [e.parent_id for e in edges if e.role == "evidence"]
    evidence = [await self._get_item_or_raise(eid) for eid in evidence_ids[:5]]
    trigger = await self._extract_trigger_with_llm(claim, evidence)
    if trigger is None or trigger == "unclear":
        return False, "no identifiable trigger"
    # Persist trigger as a tag prefixed `trigger:<slug>` (no metadata column to use).
    # Slugify: lowercase, ASCII-only, dashes for spaces, max 40 chars.
    trigger_tag = f"trigger:{_slugify(trigger)}"
    await self._add_tag_if_missing(claim.id, trigger_tag)
    return True, f"trigger: '{trigger}'"
```

`_add_tag_if_missing` uses the same raw-SQL `json_each` / `json_insert` pattern from slice-6 §5.2. `_get_item_or_raise` is also the helper from slice 6 §3.2 — codex reuses if slice 6 landed first, otherwise re-implements.

LLM prompt at `apps/server/arden/memory/prompts/is_toolable.txt`:
```
Given this claim and supporting evidence, identify the precondition or trigger that starts the workflow.

Claim: {claim_content}
Evidence (observations and/or prior claims):
{evidence_bullets}

Answer with ONE short phrase (≤ 10 words) describing when this workflow begins, or the word "unclear" if no consistent trigger exists.
```

### 3.4 Success signal (SKIPPED in v1)

Spec called for episode `feedback_score` metadata OR "absence of correction within 24h" check. **Impossible today** because:
- `feedback` and `usage` fields on `MemoryItem` are dicts but exist primarily on the insert path, NOT exposed on read in any consumable form by the gate
- "absence of correction within 24h" requires episode-correction tagging that doesn't exist

Same re-home as §3.2.

For v1, `_check_success_signal` returns `(True, "skipped — see §16")`.

### 3.5 Aggregate

```python
async def evaluate(self, claim: MemoryItem) -> tuple[bool, str]:
    checks = [
        await self._check_repetition(claim),
        await self._check_determinism(claim),   # stub returns True
        await self._check_trigger(claim),
        await self._check_success_signal(claim),  # stub returns True
    ]
    passed = all(ok for ok, _ in checks)
    reason = "; ".join(msg for _, msg in checks)
    return passed, reason

async def evaluate_and_tag(self, claim_id: str) -> None:
    claim = await self._get_item_or_raise(claim_id)
    is_toolable, reason = await self.evaluate(claim)
    # Persist verdict as a tag: `toolable:true` or `toolable:false`.
    # Verdict reason is logged, NOT persisted (no metadata column).
    verdict_tag = "toolable:true" if is_toolable else "toolable:false"
    await self._add_tag_if_missing(claim_id, verdict_tag)
    logger.info("is_toolable verdict claim=%s passed=%s reason=%s", claim_id, is_toolable, reason)
```

**Honest gate behavior:** with determinism + success-signal stubbed to True, the v1 gate is effectively "repetition ≥ 3 AND trigger identifiable". That's permissive. Risk: noisy proposals. Mitigation: user must explicitly approve each one.

---

## 4. Skill inducer — clustering toolable claims

`SkillInducer.run(window_days=30, scope='user')`:

1. Fetch all `kind=claim` rows via `list_recent_items(kind='claim', window_days=window_days, ...)`, filter in Python for tag `toolable:true` AND status `active`.
2. For each candidate, look up its parent edges and skip if a `kind=proposal` row already derives from it (via reverse-edge query — there's no built-in reverse query method, so raw SQL: `SELECT child_id FROM memory_item_parents WHERE parent_id=? AND role='derives_from'`). Codex MAY add a `list_child_edges(parent_id)` helper if multiple call sites end up needing it.
3. Cluster claims by `trigger:<slug>` tag overlap (no entities available — tag overlap is the only signal). Min cluster size: 1.
4. For each cluster, draft SKILL.md body via LLM (prompt §5).
5. Write `kind=proposal` row + `/tmp/arden/proposed-skills/<slug>/SKILL.md` file + `role='derives_from'` edges to source claims (`insert_parent_edge(proposal_id, claim_id, 'derives_from')`).

`SkillInducerRunResult` dataclass:
```python
@dataclass(slots=True)
class SkillInducerRunResult:
    claims_considered: int
    toolable_claims: int
    clusters_found: int
    proposals_written: int
    elapsed_ms: int
```

Mirror slice-4/5 result-dataclass shape.

---

## 5. SKILL.md drafting

`apps/server/arden/memory/prompts/skill_draft.txt`:

```
You are drafting a reusable skill in Markdown.

The skill formalizes this recurring workflow:

Trigger: {trigger}
Claim(s):
{claim_bullets}

Supporting episodes (concrete examples):
{episode_bullets}

Write a SKILL.md body with this exact structure:

# <Skill name in Title Case>

## When to use
<1-2 sentences describing the trigger condition>

## Steps
1. <step>
2. <step>
...

## Inputs
- <named input, if any>

## Outputs
- <named output, if any>

## Notes
<any caveats, edge cases, or invariants from the evidence>

Keep it concrete. Use the user's actual vocabulary from the claims/episodes. Do NOT invent steps that weren't supported by the evidence.
```

Skill slug derived from the title: lowercase, hyphenated, ASCII-only.

---

## 6. Proposal lifecycle

**Constraint:** no `metadata` JSON column on `MemoryItem`. Proposal lifecycle state (open/approved/rejected, skill_slug, draft_path) lives in `tags` and `source_refs` (which IS a `list[dict]` and IS exposed on `MemoryItemInsert`).

### 6.1 Storage

`kind=proposal` row (note: `kind='proposal'` must be allowed by the schema — codex confirms by checking `memory_items` CHECK constraints; if proposal isn't allowed yet, codex adds a migration in this slice. Document in §10).

```python
MemoryItemInsert(
    kind="proposal",
    content=draft_skill_md,            # full SKILL.md body in content
    provenance="inferred",
    source_refs=[
        {"type": "proposal", "skill_slug": slug, "draft_path": f"/tmp/arden/proposed-skills/{slug}/SKILL.md"},
        *[{"type": "source_claim", "id": cid} for cid in source_claim_ids],
    ],
    confidence=0.5,                    # placeholder; proposals don't really have a confidence
    status="active",                   # status flips via tag 'proposal-status:approved' or 'rejected'
    scope=scope,
    tags=["proposal", "skill-draft", "proposal-status:open", f"slug:{slug}", f"trigger:{_slugify(trigger)}"],
)
```

`source_refs` is the right place for structured pointers (it's already `list[dict[str, Any]]`). Lifecycle status goes in `tags` so it's queryable via the existing tag-overlap query.

### 6.2 Approve

```
POST /admin/memory/proposals/{id}/approve
```

1. Load proposal via `_get_item_or_raise(id)`. If tags contain `proposal-status:approved` or `proposal-status:rejected`, return 409.
2. Extract `slug` and `draft_path` from `source_refs` (find dict with `type='proposal'`).
3. Read draft file at `draft_path`. If missing, 410 (gone — codex chooses 410 vs 404).
4. Compute target dir: `~/.arden/skills/<slug>/`. If exists, 409 (collision; user must rename via `?slug=<new>` query param).
5. `mkdir -p ~/.arden/skills/<slug>/` then `mv draft_path ~/.arden/skills/<slug>/SKILL.md`.
6. Write new `kind=skill` row:
   ```python
   skill_id = await repo.insert_item(
       MemoryItemInsert(
           kind="skill",
           content=draft_skill_md,
           provenance="approved-proposal",
           source_refs=[{"type": "skill_path", "path": f"~/.arden/skills/{slug}/SKILL.md"}],
           confidence=1.0,
           status="active",
           scope=proposal.scope,
           tags=["skill", f"slug:{slug}", trigger_tag],
       ),
       commit=False,
   )
   ```
7. For each source claim (extracted from proposal's `source_refs`): `insert_parent_edge(skill_id, claim_id, 'derives_from', commit=False)`.
8. Flip proposal status: append tag `proposal-status:approved`, remove `proposal-status:open` (raw SQL `json_remove` + `json_insert`). Append `approved-at:<iso>` tag.
9. Commit transaction. Return `{skill_id, skill_path}`.

`provenance` field accepts arbitrary strings per `MemoryItemInsert`; `"approved-proposal"` documents the origin.

### 6.3 Reject

```
POST /admin/memory/proposals/{id}/reject
```

1. Load proposal. If status tag is not `open`, 409.
2. Extract `draft_path` from `source_refs`. Delete file if exists (`os.unlink` ignored if missing).
3. Flip proposal status: append `proposal-status:rejected`, remove `proposal-status:open`. Optionally append `rejection-reason:<slugified-reason>` tag if request body includes `reason`.
4. Return `{rejected_at}`.

### 6.4 List

```
GET /admin/memory/proposals?status=open
```

Implementation: `list_recent_items(kind='proposal', window_days=365, ...)`, filter in Python for `proposal-status:<status>` tag. Return JSON with proposal id, content (the draft SKILL.md), source-claim count (from `source_refs`), and the slug.

Codex MAY add a `list_recent_items` `tag_filter` kwarg if Python-side filtering hurts list perf in tests; otherwise plain in-memory filter is fine for the v1 proposal volume (expected < 100 open proposals at any time).

---

## 7. Retrieval — `skill_match` reason

`apps/server/arden/memory/retrieval.py`: when a `kind=skill` row matches the query (cosine on content embedding OR FTS on trigger phrase), surface with `reason='skill_match'`. ONE branch added; no ranking changes.

---

## 8. Backlog absorption — §2B `test_knowledge_write_gate.py`

Per `slice-07-backlog.md` lines 89-111: 30 tests, file deleted untracked (NOT recoverable). The covered behavior (workflow cluster discovery, write-gate routing, duplicate/conflict routing) is now the proposal flow.

**Re-home action:** write ≥ 10 tests in `test_skill_inducer.py` covering:
- Workflow cluster discovery (was: "Workflow cluster discovery from repeated tool usage")
- Determinism rejection (was: "Workflow cluster candidate body capping" — different shape, same intent: bad candidates don't progress)
- Proposal review markers (was: "Workflow cluster review markers")
- Toolable rejection for unsourced claims (was: "Unsourced/legacy durable candidate routing")
- Duplicate-proposal routing (was: "Duplicate/conflict → review routing")

These count toward the §9 gate of ≥ 15 tests in `test_skill_inducer.py`.

---

## 9. Hard gates

1. `cd apps/server && uv run pytest tests/ --co -q 2>&1 | tail -5` → 0 errors.
2. `cd apps/server && uv run pytest tests/memory/ -q 2>&1 | tail -5` → ≥ 104 passed (81 prior + ≥ 15 inducer + ≥ 8 proposal flow), 0 failed.
3. `cd apps/server && uv run pytest tests/ -q 2>&1 | tail -5` → ≥ 879 passed, 0 failed, 0 xfailed.
4. `cd apps/server && uv run ruff check arden/ tests/ 2>&1 | tail -3` → All checks passed!
5. `grep -c 'class SkillInducer\|class IsToolableGate' apps/server/arden/memory/skill_inducer.py` → 2
6. `grep -c '^async def test_\|^def test_' apps/server/tests/memory/test_skill_inducer.py` → ≥ 15
7. `grep -c '^async def test_\|^def test_' apps/server/tests/memory/test_proposal_flow.py` → ≥ 8
8. `wc -l apps/server/arden/memory/skill_inducer.py` → ≥ 250, ≤ 600
9. `grep -n 'IsToolableGate' apps/server/arden/memory/pattern_finder.py` → exactly 2 lines (import + one call site)
10. `ls ~/.arden/skills/` BEFORE and AFTER an approve-flow integration test → directory count +1, and the new dir contains SKILL.md
11. `find /tmp/arden/proposed-skills/ -name SKILL.md 2>/dev/null | wc -l` after a reject-flow test → 0 (drafts cleaned up)

---

## 10. PM checklist for codex's report

1. Confirm: determinism + success-signal checks are stubbed-to-True with explicit "skipped — see §16" reason strings. No `tool_sequence` was added (no metadata column to put it in).
2. What `is_toolable=True` rate did your test fixtures produce? With determinism/success stubbed, expect 30–60% (much higher than spec's 5–15% target). Document.
3. Did the `memory_items` schema CHECK constraint already permit `kind='proposal'` and `kind='skill'`, or did you add a migration? Paste the constraint check or migration diff.
4. How are `memory_item_parents` reverse queries (`role='derives_from'` from claim → proposal) implemented — raw SQL inline, or did you add a `list_child_edges` helper? Paste the call site.
5. List the ≥ 10 ported §2B behaviors. Which got dropped entirely?
6. Paste all 11 gate outputs.
7. Did the approve flow `mv` atomically (same fs) or `copy + unlink`? Document the cross-fs risk if `/tmp` and `~/.arden/` are on different mounts (unlikely on macOS, possible on Linux).
8. `git diff --stat` vs `main` HEAD.

---

## 11. Codex prompt (verbatim — extracted by invoke.sh §13)

```
You are implementing slice 7 of the Arden memory redesign — the final pipeline slice.

Prerequisite: slices 5 + 6 landed. Verify with:
  cd apps/server && uv run pytest tests/memory/test_claim_layer.py tests/memory/test_contradictions.py -q

Read `docs/internal/slices/slice-07-skill-inducer.md` start to finish, then:
1. Slice 5 + 6 briefs for prior conventions.
2. `apps/server/arden/memory/pattern_finder.py` + `contradictions.py` for hook points.
3. `apps/server/arden/memory/retrieval.py` for the `skill_match` reason addition.
4. `docs/internal/arden-memory-redesign-spec.md` §3.5 (skill inducer + is_toolable) + §2.5 (skill + proposal kinds).
5. `docs/internal/slices/slice-07-backlog.md` §2B for the ported test behaviors.
6. `~/.arden/skills/add-skill/SKILL.md` for the canonical SKILL.md shape.

Implement §1-§8 of the brief. Tests per §9 (≥ 15 inducer + ≥ 8 proposal). Run gates §9. Answer §10.

Frozen zones in §2. The exception in §3.2 (one episode-close metadata field) is the ONLY allowed deviation; document it.

Commit only when all 11 gates green:
  feat(memory): slice 7 — skill inducer + is_toolable gate + proposal flow

Do NOT push. Ask in §10 if ambiguous. This is the slice that completes the memory redesign DoD #2.
```

---

## 12. Sequence of work

1. Read phase: spec §3.5, slice 5+6 briefs, existing SKILL.md examples.
2. `IsToolableGate` skeleton + each of the 4 checks.
3. Episode-close `tool_sequence` addition (the §3.2 exception).
4. `SkillInducer.run` + clustering + LLM SKILL.md drafting.
5. Proposal CRUD endpoints (list, approve, reject) + filesystem moves.
6. `skill_match` retrieval label.
7. `_persist_claim` hook (1 line) for `evaluate_and_tag`.
8. Scheduler registration for `skill-inducer-daily`.
9. Tests (≥ 15 inducer + ≥ 8 proposal flow), including backlog §2B re-homed behaviors.
10. Gates + report.

---

## 13. Sequence of work — codex's plan (aliased for invoke.sh)

(See §12.)

---

## 14. Out of scope explicitly

- **UI for proposals** — separate post-slice-7 UX slice. Slice 7 is API + storage only.
- **Skill execution runtime** — already exists; this slice produces SKILL.md files that the existing loader will pick up.
- **Auto-approval** — non-negotiable per spec §3.5. All proposals require explicit user action.
- **Skill versioning / updates to existing skills** — if a proposal duplicates an existing skill slug, reject with 409; updating existing skills is a follow-up.
- **Cross-scope skill induction** (user-scope skill from project-scope claims) — defer; slice 7 only induces within a single scope.
- **`is_toolable` re-evaluation** when new evidence arrives — once tagged, the value persists until the claim is superseded. Refresh is post-slice-7.

---

## 15. Risk register

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| `is_toolable` gate too strict → zero proposals ever produced | Medium | High | Each check has a tunable threshold env var; PM checklist Q3 monitors rate. |
| `is_toolable` gate too loose → noisy proposals | Medium | Medium | Approval gate is the safety net; reject endpoint exists. |
| Filesystem race on approve (file moved twice, parallel approve calls) | Low | High | Approve handler takes an exclusive DB-level lock on the proposal row before fs ops; 409 on second call. |
| `~/.arden/skills/` skill slug collision | Medium | Medium | 409 with explicit "rename via ?slug= param" instruction. |
| LLM-drafted SKILL.md hallucinates steps not in evidence | Medium | High | Approval gate; user must read before approving. Logging includes raw evidence link. |
| Episode `tool_sequence` field absent in pre-slice-7 episodes | High | Low | Determinism check returns False with "episodes lack tool_sequence metadata"; only affects historical data, new episodes have it. |
| Test count gate forces shallow tests | Low | Medium | §9 sets minimums; codex can write more. PM checklist Q4 asks about depth. |
| `proposal` kind unrecognized by retrieval until skill exists | Low | Low | Intentional: proposals are not surfaced. Only skills go via `skill_match`. |

---

## 16. Repo-surface audit + honest gate degradation (2026-05-28 05:35)

Audited `apps/server/arden/memory/items_store.py` + `pattern_finder.py` + the connector layer. Findings that drove this revision:

### What the slice 7 v1 brief assumed (and was wrong about)

| Assumption | Reality |
|------------|---------|
| `MemoryItem.metadata` JSON field | Does not exist. Only structured fields: `tags`, `source_refs`, `usage`, `feedback`. |
| Episode `tool_sequence` metadata at episode-close | Does not exist. No tool-call projection in `memory/connectors/chat.py` or `buffers_store.py`. |
| `repo.count_parents(role=...)` | Does not exist. Use `list_parent_edges` + Python count. |
| `repo.get_evidence_chain(item_id, max_depth)` | Does not exist. Walk `list_parent_edges` manually if needed. |
| `repo.update_metadata` / `update_metadata_key` | Don't exist. Persist verdict state via tags + raw SQL. |
| `repo.get_item(item_id)` | Doesn't exist. Codex adds `_get_item_or_raise` helper inline (shared with slice 6). |

### What v2 drops or stubs

| Spec §3.5 gate | v1 brief | v2 brief |
|----------------|----------|----------|
| Repetition | Real check via `count_parents` | **Real check** via `list_parent_edges` + Python count |
| Determinism (tool-sequence Jaccard) | Real check via `metadata.tool_sequence` | **SKIPPED** — stubbed to True. Re-home: future "memory-metadata-column" slice. |
| Trigger identification | LLM judge + metadata store | **Real check** — LLM judge + tag-store (`trigger:<slug>`) |
| Success signal (feedback / no-correction) | Real check via episode metadata | **SKIPPED** — stubbed to True. Same re-home. |

Effective v1 gate: **repetition ≥ 3 AND trigger identifiable**. Permissive. The user-approval gate on every proposal is the safety net.

### Re-home plan for skipped gates

A future slice "memory-metadata-column" adds:
- `memory_items.metadata JSON` column (one schema migration)
- Episode-close hook to record `tool_sequence` per turn
- Episode-correction tagging (e.g. `corrects:<prior_episode_id>` tag for 24h-correction-absence success check)

After that slice ships, `_check_determinism` and `_check_success_signal` get real implementations and slice 7's gate matches spec §3.5 fully.

### Carry-forward for slices 8+ skill execution

`kind='skill'` rows from this slice persist a `slug:<name>` tag and a `source_refs` entry pointing at `~/.arden/skills/<slug>/SKILL.md`. The existing skill loader (already in production) discovers skills by directory listing of `~/.arden/skills/`. So slice 7's skill files are picked up automatically — no executor changes required. Retrieval `skill_match` reason is the only new label needed.

---

**End of brief.**
