# Memory Artifact Addressing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make generated memory artifacts behave like a small, tool-addressable context wiki without making markdown canonical.

**Architecture:** SQLite records remain canonical. The exporter writes deterministic entry points and link maps; tools and UI resolve human labels to artifact paths. Desktop browsing defaults to useful context surfaces instead of expanding every generated branch.

**Tech Stack:** Python/FastAPI artifact exporter and tools; TypeScript/React desktop memory browser; pytest and bun tests.

## Global Constraints

- Keep SQLite `memory.db` records canonical; markdown artifacts are generated read surfaces.
- Do not reintroduce generated skill drafts or compatibility folders `sources/`, `files/`, `docs/`.
- Avoid Dex-style file-first truth and typed CRM memory ontology.
- Prefer explicit path/title/link resolution over keyword heuristics.
- Stage only files changed for this memory artifact work; leave unrelated dirty files untouched.

---

### Task 1: Context Link Map And Tool Addressing

**Files:**
- Modify: `apps/server/arden/memory/artifacts.py`
- Modify: `apps/server/arden/tools/memory.py`
- Test: `apps/server/tests/test_memory_artifacts.py`
- Test: `apps/server/tests/test_memory_filesystem_tools.py`

**Interfaces:**
- Produces: generated `context/links.md`.
- Produces: `memory_read(path=...)` accepts exact artifact paths, directory names with `index.md`, title labels, file stems, and `[[wikilink]]` text when unique.

- [ ] **Step 1: Add failing tests**
- [ ] **Step 2: Generate `context/links.md` after all artifact pages are written**
- [ ] **Step 3: Add unique artifact-reference resolution in `memory_read`**
- [ ] **Step 4: Run focused server tests**

### Task 2: Desktop Artifact Browser Cleanup

**Files:**
- Modify: `apps/desktop/src/components/memory/ArtifactMemoryView.tsx`
- Test: `apps/desktop/tests/memorySimplified.test.tsx`

**Interfaces:**
- Consumes: flat artifact list from `/admin/memory/artifacts`.
- Produces: current directory order, expanded-default set, visible stale-note notice, and broad wiki-link alias resolution.

- [ ] **Step 1: Add test assertions for current directories and no old folder taxonomy**
- [ ] **Step 2: Collapse noisy branches by default**
- [ ] **Step 3: Resolve wiki links by exact path, directory index, title, slug, and file stem**
- [ ] **Step 4: Show a visible notice after a selected generated note disappears**
- [ ] **Step 5: Run desktop focused test**

### Task 3: Verification And Commit

**Files:**
- Modify only files from Tasks 1 and 2 plus this plan if kept.

- [ ] **Step 1: Run focused server tests**
- [ ] **Step 2: Run focused desktop tests**
- [ ] **Step 3: Regenerate live memory artifacts**
- [ ] **Step 4: Verify `context/links.md` exists and old folders/schema do not**
- [ ] **Step 5: Commit directly to `main`**
