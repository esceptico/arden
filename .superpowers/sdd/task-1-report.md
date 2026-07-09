# Task 1: Slice + Ask Models and Slice Registry - Report

## Summary
Completed foundational data models for the Slices feature, including Slice/Ask dataclasses and a file-backed SliceRegistry. All tests passing.

## Implementation Details

### Files Created
1. **apps/server/ntrp/slices/__init__.py** - Empty module init
2. **apps/server/ntrp/slices/models.py** - Data models:
   - `AskKind` type alias: `Literal["review", "decide", "act", "drift"]`
   - `AskState` type alias: `Literal["active", "done", "dismissed", "snoozed"]`
   - `Autonomy` type alias: `Literal["observe", "act"]`
   - `Slice` dataclass with: `key`, `title`, `page_path`, `autonomy`, `related` (list, default factory)
   - `Ask` dataclass with: `id`, `slice_key`, `text`, `kind`, `source`, `actions`, `state`, `created_at`, optional `snoozed_until` and `provenance`

3. **apps/server/ntrp/slices/registry.py** - SliceRegistry class:
   - `__init__(path: Path)` - stores path to JSON file
   - `load() -> list[Slice]` - reads from JSON, returns empty list if file missing
   - `save(slices: list[Slice])` - writes slices to JSON with indentation
   - `get(key: str) -> Slice` - retrieves slice by key, raises KeyError with valid keys list on miss

4. **apps/server/tests/test_slices_registry.py** - Two tests:
   - `test_registry_roundtrip` - verifies save/load roundtrip with dataclass equality and JSON structure
   - `test_registry_get_unknown_lists_valid_keys` - verifies self-correcting interface on KeyError

### Files Modified
- **apps/server/ntrp/constants.py** - Added two constants:
  - `SLICES_FILE = "slices.json"` - under ~/.ntrp dir
  - `SLICES_STATE_FILE = "slices-state.json"`

## Test Results
```
============================= test session starts ==============================
tests/test_slices_registry.py::test_registry_roundtrip PASSED            [ 50%]
tests/test_slices_registry.py::test_registry_get_unknown_lists_valid_keys PASSED [100%]

============================== 2 passed in 0.04s ======================
```

## Process
1. ✅ Wrote failing test - confirmed ModuleNotFoundError
2. ✅ Implemented models and registry per brief verbatim
3. ✅ Added constants following existing NTRP_DIR-style pattern
4. ✅ All tests pass
5. ✅ Committed: `70a99a2c feat(slices): Slice/Ask models + file-backed registry`

## No Deviations
- All code matches brief specification exactly
- No defensive fallbacks or backward compatibility hacks
- Minimal docstrings (code speaks for itself per project style)
- Imports at top, dataclasses for models, no inheritance
- Registry's self-correcting error message implemented as specified

## Notes
- Constants added to ntrp/constants.py in new "--- Slices ---" section at file end
- All implementations are straightforward TDD: test → code → verify
- No external dependencies beyond stdlib (json, dataclasses, pathlib, typing)
