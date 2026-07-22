# Transcript FTS-Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove transcript embedding requests while preserving SQLite FTS5 transcript search.

**Architecture:** `SessionStore` owns durable transcript rows and their FTS5 projection only. The shared `SearchIndex` remains available to memory stores, but is no longer attached to sessions and contains no transcript-specific write or query path.

**Tech Stack:** Python 3.13, asyncio, aiosqlite, SQLite FTS5, pytest

## Global Constraints

- Preserve transcript word search, ranking, snippets, pagination, and filters.
- Preserve memory and record embedding behavior.
- Do not migrate or delete existing `search.db` rows in this change.

---

### Task 1: Remove transcript semantic indexing

**Files:**
- Modify: `apps/server/ntrp/context/store.py`
- Modify: `apps/server/ntrp/server/runtime/knowledge.py`
- Modify: `apps/server/tests/test_transcript_search.py`
- Delete: `apps/server/tests/test_transcript_hybrid_search.py`

**Interfaces:**
- Consumes: `SessionStore.save_session(...)` and `SessionStore.search_messages(...)`
- Produces: FTS-only transcript persistence and search with no `SearchIndex` calls

- [ ] **Step 1: Write the failing test**

Add a recording index to `test_transcript_search.py`, attach it to a session store using the current API, save and search a transcript, and assert that no upsert, delete, or embedding call occurs.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --project apps/server pytest apps/server/tests/test_transcript_search.py -q`

Expected: the new assertion fails because current session saves schedule transcript vector upserts.

- [ ] **Step 3: Write minimal implementation**

Remove `SessionStore`'s search-index field and attachment method, transcript index scheduling, hybrid transcript retrieval, and session-store attachment in `KnowledgeRuntime`. Keep the existing FTS query path as the only `search_messages` implementation.

- [ ] **Step 4: Remove obsolete tests**

Delete `test_transcript_hybrid_search.py`; its expected vector behavior is intentionally removed. Keep the new no-embedding regression in the FTS suite.

- [ ] **Step 5: Verify**

Run: `uv run --project apps/server pytest apps/server/tests/test_transcript_search.py apps/server/tests/test_search_index.py apps/server/tests/test_memory_records.py -q`

Expected: all selected tests pass.

Run: `git diff --check`

Expected: no output and exit code 0.
