# Slice 02 — live smoke test

Run AFTER `codex exec` succeeds, after `pytest` is 13/13 green, and after PM review checklist passes. This is the final DoD item: a real `RunCompleted` event in the live server produces an `episode_buffers` row.

## Pre-conditions

- Slice 2 code merged (or applied locally — server picked it up after restart).
- Live server running. (Server restart NOT done by arden per directives — tim restarts.)
- `~/.arden/memory.db` has schema_version=31.

## Procedure

1. **Snapshot baseline:**
   ```bash
   sqlite3 ~/.arden/memory.db "SELECT COUNT(*) FROM episode_buffers;"
   ```
   Expected: 0 (or N if previous smoke tests ran).

2. **Trigger one real chat turn.** Open any session in the Arden desktop app, send one message, wait for the assistant reply to land.

3. **Wait for outbox to flush.** The outbox worker polls every few seconds; give it 10s.

4. **Verify episode_buffers grew:**
   ```bash
   sqlite3 ~/.arden/memory.db "SELECT id, scope, source_kind, turn_count, tokens, started_at, last_activity_at, closed_at FROM episode_buffers ORDER BY started_at DESC LIMIT 3;"
   ```
   Expected: at least one new row with `source_kind='chat_msg'`, `turn_count=1`, `closed_at IS NULL`.

5. **Verify FTS is in sync (sanity, not a DoD requirement):**
   ```bash
   sqlite3 ~/.arden/memory.db "SELECT COUNT(*) FROM memory_items;"
   sqlite3 ~/.arden/memory.db "SELECT COUNT(*) FROM memory_items_fts;"
   ```
   Counts should match (zero for now until a buffer closes).

6. **Force a close (optional, exercises full path):** send 50 chat turns OR send a message containing "switching topic" OR wait 10 minutes idle. Then:
   ```bash
   sqlite3 ~/.arden/memory.db "SELECT id, kind, confidence, status FROM memory_items WHERE kind='episode' ORDER BY created_at DESC LIMIT 3;"
   ```
   Expected: a new `kind='episode'` row, confidence ≈ 0.319, status='active'.

## Failure modes

- **Zero new buffer rows** → outbox handler not wired up. Check server log for `ChatConnector` log lines. Check `outbox/event` table for pending/dead events.
- **IntegrityError noise in log** → centroid concurrency case fired; verify the retry-once path didn't infinite-loop.
- **Vec dim mismatch error** → `Embedder.config.dim != meta.embedding_dim`. Should never happen post slice 1.
- **Buffer created but never closes after 50 turns** → trigger evaluation order bug; check `evaluate_triggers` in `connectors/chat.py`.

## Reporting back

If smoke test passes: PM marks DoD item ✓, tim confirms, slice 2 done.
If smoke test fails: PM does NOT mark complete; writes a follow-up brief or correction to slice 2.
