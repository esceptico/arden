# Task 3: Ask State Store + Focus-Set Nomination — Report

## STATUS: DONE

**Commit hash:** ad38e9d5

**Test summary:** 3/3 PASS
- `test_store_upsert_resolve_roundtrip` ✓
- `test_snoozed_asks_hidden_until_deadline` ✓
- `test_nominate_focus_one_per_slice_kind_priority` ✓

## Changes

### Created: `apps/server/ntrp/slices/asks.py`

- **AskStore** class:
  - `__init__(path: Path)` – loads existing asks from JSON file if exists
  - `upsert(ask: Ask) → None` – adds/updates ask, flushes to disk
  - `resolve(ask_id: str, state: AskState, snoozed_until: str | None = None) → Ask` – updates state + snooze deadline, validates ask exists, persists
  - `list(slice_key: str | None = None, include_resolved: bool = False) → list[Ask]` – filters by slice (optional), excludes resolved unless requested, re-admits snoozed asks if deadline passed, sorted by created_at (newest first)

- **nominate_focus** function:
  - Selects one best ask per slice based on kind priority: decide < drift < review < act
  - Breaks priority ties with created_at (newer first)
  - Capped at `cap` parameter (default 4)
  - Returns up to `cap` asks total, ranked by kind then created_at

### Created: `apps/server/tests/test_slices_asks.py`

Three tests covering:
1. Roundtrip upsert + resolve to dismissed state
2. Snooze expiry logic (re-admits when deadline passes)
3. Focus nomination with slice isolation and kind priority ordering

## Implementation Notes

- Snooze re-admission works via ISO timestamp comparison: if `snoozed_until <= datetime.now(UTC).isoformat()`, ask is treated as active.
- `nominate_focus` sorts by `(kind_priority, created_at)` tuple — this ensures kind priority dominates, then within same kind, newer asks come first (low tuple values = high priority).
- Persistence uses simple JSON with `asdict()` serialization; path parent directories created on first flush.
- No external dependencies beyond Python stdlib (json, dataclasses, pathlib, datetime).

## Concerns

None. Implementation matches the brief verbatim, all tests pass, code is clean and follows project conventions.

---

## Follow-up: Bug Fixes (Task 3 Review Findings)

**STATUS: DONE**

**Commit hash:** 0b7e3dd7

Two confirmed bugs from code review were fixed via TDD (failing test added first, confirmed red, then fixed).

### Bug 1 (High): `nominate_focus` tie-break inverted

The original claim in this report ("low tuple values = high priority... newer asks come first") was **incorrect** — the actual code did `(_KIND_PRIORITY[a.kind], a.created_at) < (...)`, which for equal kind priority picked the **older** `created_at` (since ISO date strings compare lexicographically and `<` favors the smaller/older string). Correct semantics: for equal kind priority, prefer the **newer** ask.

Fix: replaced the tuple-min comparison with an explicit `_is_better(a, cur)` helper that compares kind priority first, and only on a tie compares `created_at` with `>` (newer wins). Final cross-slice ranking now does a two-pass stable sort — ascending by `created_at` (reverse=True) then stable-sort by kind priority — producing "priority asc, created_at desc" ordering before capping.

Regression test added: `test_nominate_focus_same_kind_prefers_newer` — confirmed RED before the fix (`AssertionError: assert ['old'] == ['new']`), GREEN after.

### Bug 2 (Medium): snooze re-admission compared ISO strings lexicographically

`AskStore.list` compared `a.snoozed_until <= now` as raw ISO strings. This breaks when timestamps have differing timezone representations (e.g. a non-UTC offset like `+05:00` vs. `now.isoformat()`'s `+00:00`), since lexicographic string ordering doesn't track real chronological order across differing offsets.

Fix: added module-level `_parse(ts: str) -> datetime` using `datetime.fromisoformat`, attaching UTC via `.replace(tzinfo=UTC)` when naive. `AskStore.list` now compares `_parse(a.snoozed_until) <= datetime.now(UTC)` as real datetimes.

Regression test added: `test_snooze_comparison_handles_aware_and_naive` — constructs a `snoozed_until` that is 1 hour in the past in real UTC terms but carries a `+05:00` offset (so lexicographic string comparison against `now.isoformat()`'s `+00:00` form gets the ordering backwards), plus a naive past timestamp. Confirmed RED before the fix (`assert ['a2'] == ['a1', 'a2']` — the `+05:00` case was wrongly kept hidden), GREEN after.

### Test output (final, all green)

```
tests/test_slices_asks.py::test_store_upsert_resolve_roundtrip PASSED    [ 20%]
tests/test_slices_asks.py::test_snoozed_asks_hidden_until_deadline PASSED [ 40%]
tests/test_slices_asks.py::test_nominate_focus_one_per_slice_kind_priority PASSED [ 60%]
tests/test_slices_asks.py::test_nominate_focus_same_kind_prefers_newer PASSED [ 80%]
tests/test_slices_asks.py::test_snooze_comparison_handles_aware_and_naive PASSED [100%]
============================== 5 passed in 0.03s ===============================
```

Sibling regression check: `uv run pytest tests/ -q -k slices` → `10 passed, 1251 deselected`.

### Files touched (commit 0b7e3dd7, staged individually — no `-A`/`.`)

- `apps/server/ntrp/slices/asks.py`
- `apps/server/tests/test_slices_asks.py`
