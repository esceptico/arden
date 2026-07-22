# Task 2: Page Projection — Open Loops from Prose

## Summary

Implemented prose parsing to extract "## Open loops" bullets from memory topic pages.

## Files Created

- `apps/server/arden/slices/projection.py` — Core implementation
  - `parse_open_loops(prose: str) -> list[str]` — Extracts bullet texts under `## Open loops` heading, strips markdown bold, strips provenance suffixes `(from chat)` and `(record:...)`
  - `page_summary(page: Page) -> dict` — Returns dict with title, updated timestamp, and open_loops list

- `apps/server/tests/test_slices_projection.py` — Test suite
  - `test_parse_open_loops_extracts_bullets_until_next_heading()` — Verifies extraction stops at next heading and provenance is stripped
  - `test_parse_open_loops_missing_section_is_empty()` — Verifies missing section returns empty list

## Implementation Details

The parser uses regex patterns to:
1. Detect the `## Open loops` heading (case-insensitive, any spacing)
2. Collect markdown bullets (`-` or `*` prefix) until the next heading
3. Strip markdown bold markers (`**text**` → `text`)
4. Strip provenance suffixes: `(from chat)` and `(record:...)` patterns

## Test Results

```
tests/test_slices_projection.py::test_parse_open_loops_extracts_bullets_until_next_heading PASSED
tests/test_slices_projection.py::test_parse_open_loops_missing_section_is_empty PASSED

======================== 2 passed in 1.35s ========================
```

## Commit

`4b6c4ac0` — feat(slices): open-loop projection from topic-page prose

---

## Post-Implementation Fix: Indented Heading Termination

**Issue:** The `parse_open_loops()` function had an inconsistency in line stripping:
- Line 16 checked `_LOOP_HEADING.match(line.strip())` on the stripped line
- Line 19 checked `_HEADING.match(line)` on the UNstripped line
- Result: indented headings (e.g., `  ## Indented heading`) failed to terminate the Open loops section

**Fix:** Strip once per iteration and use the same variable for all three regex checks.

**Test Added:**
```python
def test_parse_open_loops_indented_heading_terminates_section():
    prose = "# T\n\n## Open loops\n- Loop one.\n  ## Indented heading\n- Not a loop.\n"
    assert parse_open_loops(prose) == ["Loop one."]
```

**Test Results:**
```
tests/test_slices_projection.py::test_parse_open_loops_extracts_bullets_until_next_heading PASSED [ 33%]
tests/test_slices_projection.py::test_parse_open_loops_missing_section_is_empty PASSED [ 66%]
tests/test_slices_projection.py::test_parse_open_loops_indented_heading_terminates_section PASSED [100%]

======================== 3 passed in 0.97s ========================
```

**Commit:** `a46e937c` — fix(slices): terminate open-loops section on indented headings too

---

**STATUS:** COMPLETE | Commit: `a46e937c` | All 3 tests passing (2 original + 1 new)
