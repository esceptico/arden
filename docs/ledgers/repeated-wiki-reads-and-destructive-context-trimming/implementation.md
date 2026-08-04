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

## Notes

- I-04 uses the existing global prompt's “read stays valid while version unchanged” rule plus the strengthened `wiki_read_page` description. No summary-specific superstition rule was added.
- I-05 is intentionally deferred: Claude Code ships no fuse; Hermes/OpenCode fuse only with additional state/permission behavior. Primary fixes should be observed first.
