# Memory Context Projection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve Arden memory by turning flat records into better generated markdown context without adding CRM-style typed memory.

**Architecture:** `RecordStore` remains canonical. Markdown remains a generated, read-only agent filesystem. This plan adds deterministic context pages and integration overview pages from existing records/source refs, then fixes docs to match the flat model.

**Tech Stack:** Python 3.13, `ArtifactMemoryStore`, pytest, FastAPI memory router docs.

---

## Guardrails

- Do not add new canonical memory kinds, facets, graph tables, lens tables, or derivation edges.
- Do not bring back `sources/`, `files/`, or `docs/`; keep `references/`.
- Do not edit unrelated dirty files in `apps/server/arden/agent/`, `apps/server/arden/core/spawner.py`, or `apps/server/tests/test_spawn_salvage.py`.
- Preserve generated-artifact symlink/FIFO safety patterns.

## File Structure

- Modify `apps/server/arden/memory/artifacts.py`: context directory registration, generated `context/` pages, integration grouping helpers.
- Modify `apps/server/tests/test_memory_artifacts.py`: deterministic artifact tests.
- Modify `apps/server/tests/test_memory_router.py`: route/browser contract expectations.
- Modify `docs/api-reference/memory.mdx`: current flat model and generated artifact layout.

---

### Task 1: Generated Context Index And Schema

**Files:**
- Modify: `apps/server/arden/memory/artifacts.py`
- Modify: `apps/server/tests/test_memory_artifacts.py`
- Modify: `apps/server/tests/test_memory_router.py`

- [ ] **Step 1: Write failing tests**

Add a test asserting an export creates `context/index.md` and `context/README.md`, both readable through `ArtifactMemoryStore`, and that legacy folders remain absent.

Expected assertions:

```python
context = artifacts.read_artifact("context/index.md")
readme = artifacts.read_artifact("context/README.md")
assert context.kind == "topic"
assert readme.kind == "topic"
assert "me.md" in context.content
assert "active-work.md" in context.content
assert "SQLite" in readme.content
assert "memory_tree" in readme.content
for old_path in ("sources/index.md", "files/index.md", "docs/index.md"):
    with pytest.raises(FileNotFoundError):
        artifacts.read_artifact(old_path)
```

Update router artifact shape test to expect `context/index.md` and `context/README.md`.

- [ ] **Step 2: Run tests and see failure**

Run from `apps/server`:

```bash
uv run pytest -q -p no:cacheprovider tests/test_memory_artifacts.py::test_context_index_and_readme_are_generated tests/test_memory_router.py::test_rebuild_artifacts_endpoint_shape_and_counts
```

Expected: fail because `context/` is not registered/generated yet.

- [ ] **Step 3: Implement minimal exporter support**

In `apps/server/arden/memory/artifacts.py`:

- Add `"context": "topic"` to `ARTIFACT_DIR_KINDS`.
- Include `context` in generated cleanup.
- Add `_write_context_docs()` called by `export_from_records()` after project/entity/reference writers.
- Generate:
  - `context/index.md`: links `context/README.md`, `me.md`, `active-work.md`, `entities/index.md`, `projects/index.md`, `references/index.md`, `changelog/index.md`, and `context/integrations/index.md`.
  - `context/README.md`: explains how agents/tools should use the generated memory artifact tree; this replaced the old `context/SCHEMA.md`.
- Update README/tooling directory maps to mention `context/`.

- [ ] **Step 4: Run tests**

```bash
uv run pytest -q -p no:cacheprovider tests/test_memory_artifacts.py tests/test_memory_router.py
```

Expected: pass.

- [ ] **Step 5: Commit only touched files**

```bash
git add apps/server/arden/memory/artifacts.py apps/server/tests/test_memory_artifacts.py apps/server/tests/test_memory_router.py
git commit -m "Add memory context artifact index"
```

---

### Task 2: Generated Integration Reference Pages

**Files:**
- Modify: `apps/server/arden/memory/artifacts.py`
- Modify: `apps/server/tests/test_memory_artifacts.py`
- Modify: `apps/server/tests/test_memory_router.py`

- [ ] **Step 1: Write failing tests**

Add a test with records carrying integration-like source refs and integration scope.

Expected setup:

```python
from arden.memory.models import SourceRef

await records.add(
    "Slack channel #eng discussed memory publish ordering",
    kind=Kind.FACT,
    source_ref=SourceRef(kind="slack", ref="channel:eng"),
)
await records.add(
    "Gmail receipt for a customer follow-up",
    kind=Kind.SOURCE,
    scope_kind="integration",
    scope_key="gmail",
    source_ref=SourceRef(kind="gmail", ref="message:abc"),
)
```

Expected assertions:

```python
index = artifacts.read_artifact("context/integrations/index.md")
slack = artifacts.read_artifact("context/integrations/slack.md")
gmail = artifacts.read_artifact("context/integrations/gmail.md")
assert "[[Slack]]" in index.content
assert "[[Gmail]]" in index.content
assert slack.record_count == 1
assert gmail.record_count == 1
assert "channel #eng" in slack.content
assert "message:abc" in gmail.content or "Gmail receipt" in gmail.content
```

Update router artifact shape test to include `context/integrations/index.md` when records include integration refs.

- [ ] **Step 2: Run tests and see failure**

```bash
uv run pytest -q -p no:cacheprovider tests/test_memory_artifacts.py::test_integration_reference_pages_are_generated_from_existing_records
```

Expected: fail because integration pages do not exist yet.

- [ ] **Step 3: Implement mechanical integration pages**

In `apps/server/arden/memory/artifacts.py`:

- Add helper `_integration_key(record)`:
  - Prefer known `source_ref.kind` values so per-message integration scopes do not fragment pages.
  - Reuse shared `INTEGRATION_SOURCE_KINDS` and add projection-only extras: `github`, `notion`, `obsidian`.
  - Else, when `scope_kind == "integration"`, derive a stable provider key from `scope_key`.
  - Return `None` for normal curator/chat/consolidate records.
- Add `_write_integration_context(rows)`.
- Generate `context/integrations/index.md`.
- Generate one page per integration with sections:
  - `# <Title>`
  - `## What this page is`
  - `## Recent records`
  - `## Source receipts`
- Use `_reference_snippet()` and existing sanitizers; no raw transcript dumps.
- Set page `record_count` to the number of grouped records.

- [ ] **Step 4: Run tests**

```bash
uv run pytest -q -p no:cacheprovider tests/test_memory_artifacts.py tests/test_memory_router.py
```

Expected: pass.

- [ ] **Step 5: Commit only touched files**

```bash
git add apps/server/arden/memory/artifacts.py apps/server/tests/test_memory_artifacts.py apps/server/tests/test_memory_router.py
git commit -m "Add memory integration context artifacts"
```

---

### Task 3: API Docs Alignment And Final Verification

**Files:**
- Modify: `docs/api-reference/memory.mdx`
- Optionally modify: `apps/server/arden/server/routers/memory.py`
- Optionally modify: `apps/server/tests/test_memory_router.py`

- [ ] **Step 1: Fix docs**

Update `docs/api-reference/memory.mdx`:

- Supported record kinds: `directive | fact | source`.
- Mention `changelog` is generated/audit-only, not a user create kind.
- Remove old non-create kind references.
- Explain `context/` as generated agent-facing context docs.
- Keep `references/` as the consolidated receipt/doc/file/integration pointer area.

- [ ] **Step 2: Fix search mode only if trivial**

If touching router search mode, keep existing degraded behavior:

```python
"mode": "hybrid" if store._search_index is not None else "fts"
```

Do not add new API concepts.

- [ ] **Step 3: Run final test set**

Run from `apps/server`:

```bash
uv run pytest -q -p no:cacheprovider tests/test_memory_artifacts.py tests/test_memory_router.py tests/test_memory_filesystem_tools.py tests/automation/test_memory_maintenance_handler.py tests/automation/test_scheduler_catchup.py
```

Expected: pass.

- [ ] **Step 4: Diff hygiene**

Run from repo root:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors. Dirty unrelated files may remain, but only task files should be staged/committed.

- [ ] **Step 5: Commit only docs/router test changes**

```bash
git add docs/api-reference/memory.mdx apps/server/arden/server/routers/memory.py apps/server/tests/test_memory_router.py
git commit -m "Document flat memory context artifacts"
```
