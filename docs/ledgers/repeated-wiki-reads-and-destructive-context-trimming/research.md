# Research

## Surface research

- **Scope**: Canonical versus request-only message trimming; durable tool-result recovery; unchanged resource-read semantics; repeated-successful-call handling; compaction/read-receipt interaction.
- **Sources inspected**: Arden runtime source and persisted live transcript at revision `9090f69f96e5b75312543d3217c9a7e665d44fa0`.
- **Observations**: The limiter returns a copied list, but `Agent._prepare` clears and replaces the canonical `messages` list with that request view. Checkpoint persistence then saves the replaced list.
- **Negative evidence**: The run had no compaction and no wiki mutation before the 14 reads; therefore neither event explains the initial repetition.

## Consolidated findings

| ID | Type | Claim | Evidence | Implication | Confidence | Last checked |
| --- | --- | --- | --- | --- | --- | --- |
| F-01 | fact | Model-context limiting can destructively replace canonical run history. | `apps/server/arden/core/model_context_budget.py:19-27`; `apps/server/arden/agent/agent.py:489-510`; `apps/server/arden/services/chat.py:687-693` | Request-only optimization corrupts persisted evidence and future model context. | high | 2026-08-04 |
| F-02 | fact | A compacted placeholder is not recognized as already compacted. | `apps/server/arden/core/model_context_budget.py:37-76` | Repeated passes produce the observed `61 chars, 1 line` self-description. | high | 2026-08-04 |
| F-03 | fact | Recovery is present only when `find_result_file(tool_call_id)` finds an offloaded blob. | `apps/server/arden/core/model_context_budget.py:68-75` | Inline wiki content can become unrecoverable after destructive trimming. | high | 2026-08-04 |
| F-04 | fact | The no-progress counter only advances for all-nonretryable-failure steps. | `apps/server/arden/agent/agent.py:453-463` | Repeated successful reads are intentionally invisible to the generic failure fuse. | high | 2026-08-04 |
| F-05 | observation | Persisted messages in session `20260720_094827_005` contain many exact `[Older tool result cleared from context — 61 chars, 1 lines.]` values. | `sqlite3 -readonly /Users/escept1co/.arden/sessions.db` query over `session_messages`, observed 2026-08-04 | Confirms repeated placeholder compaction occurred in production data. | high | 2026-08-04 |
| F-06 | fact | Claude Code trims copied request history, persists large results with recovery paths, returns an unchanged-read stub, and clears/rebuilds file receipts on full compaction. | `/Users/escept1co/src/claude-code-leaked/src/query.ts:365`; `src/services/compact/microCompact.ts:295-303,446-492`; `src/utils/toolResultStorage.ts:130-198`; `src/tools/FileReadTool/FileReadTool.ts:523-568`; `src/services/compact/compact.ts:517-539,1398-1448` | Canonical/request separation plus visibility-aware receipts is the reference behavior. | high | 2026-08-04 |
| F-07 | fact | Hermes keeps request selection non-mutating, returns one unchanged-read stub, blocks the third identical visible read, and clears dedup state after full compression. | `/Users/escept1co/src/hermes-agent/agent/conversation_loop.py:839-883`; `tools/file_tools.py:1230-1265,1406-1430`; `tests/tools/test_file_read_guards.py:664-702` | A fuse is viable, but only when dedup state tracks effective visibility. | high | 2026-08-04 |
| F-08 | fact | OpenCode preserves stored outputs while projecting placeholders and has a third-identical-call doom-loop guard; Codex separately persists prepared items and truncates live context, but neither offers semantic read caching. | `/Users/escept1co/src/opencode/packages/opencode/src/session/message-v2.ts:290,521`; `session/processor.ts:29,331`; `/Users/escept1co/src/codex/codex-rs/core/src/context_manager/history.rs:203,344`; `session/mod.rs:2956` | Storage projection and loop protection are separate concerns. | high | 2026-08-04 |
| F-09 | fact | Letta persists compacted active-context IDs while retaining full DB history, but its model recall search excludes tool-result messages. | `/Users/escept1co/src/letta/letta/agents/letta_agent_v3.py:758,800`; `services/message_manager.py:895,939`; `services/tool_executor/core_tool_executor.py:140` | Durable storage alone is insufficient without a model-usable recovery surface. | high | 2026-08-04 |
| F-10 | fact | Anthropic context editing is request-only and leaves application history unmodified; OpenAI compaction defines a new canonical model-context projection. Neither provider supplies semantic duplicate-read correctness. | [Anthropic context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing); [OpenAI compaction](https://developers.openai.com/api/docs/guides/compaction), accessed 2026-08-04 | Arden must own recovery refs and visibility-aware read semantics. | high | 2026-08-04 |

## Conflicts and gaps

- The persisted transcript proves final corrupted state, not the exact model-visible content on every intermediate request. Code-path analysis is required for that reconstruction.
- Claude Code and Hermes both have a dangling-stub edge case in their lighter request-pruning paths because dedup receipts can outlive model-visible content. Arden explicitly couples eviction to content-receipt downgrade.

## Tool-harness audit

| Finding | Invariant | Severity | Resolution |
| --- | --- | --- | --- |
| Request-only limiter overwrote canonical history | #4 stable refs, #6 bounded output | high | Explicit `history_messages` commit channel; ordinary request projections no longer mutate history. |
| Inline evicted results had no recovery path | #4 stable refs, #12 recovery | high | Persist on first eviction and include exact `file_read` path. |
| Successful unchanged reads duplicated full bodies | #5 high signal, #6 bounded output, #10 idempotency | medium | Return a compact unchanged receipt while prior content remains visible. |
| Generic no-progress fuse ignores successful loops | #22 eval-driven ergonomics | low/deferred | Re-evaluate after live post-fix evidence; do not globally deduplicate stateful calls. |

## Supporting material

- None.
