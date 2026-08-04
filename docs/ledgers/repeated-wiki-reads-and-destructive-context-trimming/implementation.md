# Implementation

> A checked item means implemented, not verified.

## Intended outcome

Preserve canonical tool results, provide recoverable bounded model context, and make redundant unchanged wiki reads self-extinguishing without blocking legitimate refreshes.

## Checklist

- [x] **I-01 — Separate canonical history from request-only context**
  - Outcome: Model-context middleware cannot overwrite `run.messages` or persisted session history.
  - Scope: Agent request preparation and middleware contract.
  - Required verification: Unit test showing repeated prepares preserve original tool content in canonical messages.
- [x] **I-02 — Make compacted results stable and recoverable**
  - Outcome: A placeholder is never compacted again and includes a valid recovery path when original content leaves the model request.
  - Scope: Tool-result storage and context-budget placeholder generation.
  - Required verification: Repeated-clamp and recovery tests.
- [x] **I-03 — Add unchanged wiki-read semantics**
  - Outcome: An identical current page read returns a compact receipt-valid response rather than the full body.
  - Scope: Wiki read tool and resource observation contract.
  - Required verification: First read full; unchanged reread compact; changed/downgraded read full.
- [x] **I-04 — Align prompt and compaction wording**
  - Outcome: The model is told that one successful read remains valid until the resource changes or its content receipt is downgraded.
  - Scope: Agent prompt and compaction handoff guidance.
  - Required verification: Prompt/summary unit tests.
- [ ] **I-05 — Evaluate repeated-read fuse**
  - Outcome: Ship only if primary fixes leave a demonstrated loop; otherwise record explicit deferral.
  - Scope: Tool-specific successful-call loop detection.
  - Required verification: Agent transcript/eval showing benefit without blocking legitimate rereads.
- [x] **I-06 — Remove silent request and loop trimming**
  - Outcome: Ordinary model history is append-only; only explicit compaction can replace it.
  - Scope: Root/child middleware wiring and scheduled-loop preparation.
  - Required verification: Large accumulated tool results and loop history reach the compactor/model unchanged.
- [x] **I-07 — Close ingestion offload gaps**
  - Outcome: Normal and recovered oversized tool results enter model history only as bounded previews with recovery refs.
  - Scope: Arden tool executor and durable-call recovery.
  - Required verification: Policy-off, structured-data, media, and recovered-result cases.
- [x] **I-08 — Persist reconstructable compaction checkpoints**
  - Outcome: Each handoff carries a stable checkpoint ID and ordered retained-tail IDs.
  - Scope: Compactor and session transcript persistence.
  - Required verification: One and repeated compactions preserve full raw rows and exact active ordering.
- [x] **I-09 — Recover active context on resume**
  - Outcome: A valid active projection is authoritative; a corrupt projection rebuilds `system + latest checkpoint + retained tail + appended messages` from `session_messages`.
  - Scope: Session loading and legacy compatibility.
  - Required verification: Reconstruction succeeds with missing/corrupt snapshot and safely falls back for legacy handoffs.
- [x] **I-10 — Provider and receipt regression coverage**
  - Outcome: OpenAI and Anthropic receive the same bounded logical result without mutating stored input; compaction still downgrades content-read receipts.
  - Scope: Offline provider conversion and wiki/compaction tests.
  - Required verification: Provider conversion and receipt tests.
- [x] **I-11 — Preserve provider-native continuation state**
  - Outcome: OpenAI stateless replay preserves ordered response items without duplicates; Anthropic opaque blocks remain unchanged until compaction.
  - Scope: Provider parsing, normalized assistant history, and short opaque-history compaction.
  - Required verification: OpenAI exact round trip, Anthropic deep replay, and short-history range tests.
- [x] **I-12 — Bound background-agent completion injection**
  - Outcome: Exact child output is durable; the parent receives at most a 24k head/tail and can page the full result with `agent_result_read`.
  - Scope: Background registry, always-loaded retrieval tool, and namespace wiring.
  - Required verification: Large completion, restart redelivery, Unicode paging, and missing/offset errors.
- [x] **I-13 — Harden destructive context operations and active refs**
  - Outcome: Clear/rewind are guarded atomic exceptions; rewind deletes exact active IDs and renews the checkpoint; active raw-result refs cannot expire underneath the model.
  - Scope: Session store/service and raw-result pruning.
  - Required verification: Compacted rewind, CAS conflict, rollback, clear, reconstruction, and active-expiry tests.
- [x] **I-14 — Fence destructive context generations**
  - Outcome: Clear/rewind cannot be undone by stale active-run saves, same-ID content changes fail exact-projection CAS, and malformed projections remain destructively recoverable.
  - Scope: Session schema v5, session store/service, and background-drain persistence.
  - Required verification: v4 migration, stale save/progress after clear/rewind, exact-etag conflict, malformed clear/rewind, and focused streaming regressions.
- [x] **I-15 — Isolate context transactions and cold restore**
  - Outcome: Multi-await context writes cannot be cross-committed by another session; failures roll back projection and transcript together; cold restore cannot resurrect cleared history or lower the generation.
  - Scope: Session context transaction connection, save/progress/create rollback, and cold clear/rehydration.
  - Required verification: Cross-session barrier test, forced mirror failures, full store suite, and cold clear-restore round trip.
- [x] **I-16 — Make branch recovery atomic**
  - Outcome: A branch snapshot, referenced tool manifests, and terminal background results commit or roll back together and remain readable after source deletion.
  - Scope: Session branch transaction, handoff refs, branch-local terminal background rows, and rebuildable file caches.
  - Required verification: Source-deletion, missing-ref rollback, no-resume/no-renotify, and cache-copy failure tests.
- [x] **I-17 — Preserve exact offloads through corrupt projections**
  - Outcome: Readable raw content and the exact `ToolResult` envelope survive independently; expiry cannot delete recovery manifests while the active projection is malformed.
  - Scope: Ingestion offload, raw manifest payload, active-ref pruning, and transcript recovery.
  - Required verification: Content-only offload recovery with continuation/source metadata plus malformed/non-array projection tests.

## Notes

- I-04 uses the existing global prompt's “read stays valid while version unchanged” rule plus the strengthened `wiki_read_page` description. No summary-specific superstition rule was added.
- I-05 is intentionally deferred: Claude Code ships no fuse; Hermes/OpenCode fuse only with additional state/permission behavior. Primary fixes should be observed first.
- I-06 supersedes I-01's temporary request-only limiting design; I-01 remains historical proof that request preparation no longer overwrites canonical history.
- I-09 follows Codex/Claude/OpenCode's projection/checkpoint split: the saved active projection is used normally, while immutable rows make checkpoint recovery possible.
- Explicit user clear and rewind remain destructive by product intent; ordinary saves are append-only.
