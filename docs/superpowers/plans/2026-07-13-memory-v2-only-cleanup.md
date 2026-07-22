# Memory V2-Only Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete memory upgrade compatibility and retain only the current schema-v2 runtime.

**Architecture:** Startup initializes an empty schema-v2 vault, validates current files, then opens the store. Current validation lives in a small health module; old parsers, migrations, aliases, and import paths are deleted.

**Tech Stack:** Python 3.13, FastAPI, pytest, Ruff, React 19, Bun.

## Global Constraints

- Memory subsystem only.
- No compatibility switches or repair paths.
- Preserve current schema-v2 vault bytes and behavior.
- Execute inline; do not use subagents or worktrees.

---

### Task 1: V2-Only Startup and Validation

**Files:**
- Create: `apps/server/ntrp/memory/health.py`
- Delete: `apps/server/ntrp/memory/migrate_ledger_v2.py`
- Modify: `apps/server/ntrp/server/runtime/knowledge.py`
- Modify: `apps/server/ntrp/memory/file_store.py`
- Modify: `apps/server/ntrp/memory/artifacts.py`
- Test: `apps/server/tests/test_memory_health.py`
- Delete/replace: `apps/server/tests/test_memory_ledger_migration.py`

**Interfaces:**
- Produces: `validate_vault(root: Path) -> VaultHealth`, `initialize_empty_vault(root: Path) -> None`.

- [ ] Add tests proving empty initialization, v2 validation, and clear rejection of non-v2 sidecars.
- [ ] Run the tests and confirm the current migration startup fails the new contract.
- [ ] Extract current validation, add direct empty initialization, delete migration and SQLite import wiring.
- [ ] Run memory health/runtime tests and Ruff.
- [ ] Commit.

### Task 2: Delete Legacy Parsers and Aliases

**Files:**
- Modify: `apps/server/ntrp/memory/ledger.py`
- Modify: `apps/server/ntrp/memory/pages.py`
- Modify: `apps/server/ntrp/memory/file_store.py`
- Modify: `apps/server/ntrp/memory/scopes.py`
- Modify: `apps/server/ntrp/memory/curator.py`
- Modify: `apps/server/ntrp/memory/models.py`
- Modify corresponding `apps/server/tests/test_memory_*.py` files.

**Interfaces:**
- `parse_ledger_entry()` accepts schema-v2 two-line entries only.
- Scopes are `global`, `user`, or `area`; provenance is plural `sources`.

- [ ] Change contract tests to reject legacy lines, inline timelines, legacy directories/scopes, flat labels, and singular provenance.
- [ ] Run focused tests and confirm failures.
- [ ] Delete the compatibility branches and update current callers.
- [ ] Run record, scope, curator, page, filesystem, and router tests plus Ruff.
- [ ] Commit.

### Task 3: Delete Legacy Projection and Changelog Normalization

**Files:**
- Modify: `apps/server/ntrp/memory/artifacts.py`
- Modify: `apps/server/ntrp/memory/synthesize.py`
- Modify related artifact/synthesis tests.

**Interfaces:**
- Changelog and synthesis consume only current generated formats and current citation tags.

- [ ] Replace compatibility expectations with current-format-only tests.
- [ ] Run focused tests and confirm failures.
- [ ] Delete old changelog, observation, and citation dialect handling.
- [ ] Run projection, synthesis, artifact, and notebook API tests plus Ruff.
- [ ] Commit.

### Task 4: Integrated Verification

**Files:**
- Modify: `docs/superpowers/specs/2026-07-12-memory-ledger-timeline-hardening-design.md`
- Modify: `docs/superpowers/specs/2026-07-13-memory-v2-only-design.md` only if implementation reveals a contract correction.

- [ ] Remove obsolete migration promises from current memory documentation.
- [ ] Run the full server suite and scoped/full Ruff.
- [ ] Run desktop tests, typecheck, lint, and build.
- [ ] Validate a temporary copy of `~/.ntrp/memory` without modifying the original.
- [ ] Confirm `git diff --check` and commit the cleanup.
