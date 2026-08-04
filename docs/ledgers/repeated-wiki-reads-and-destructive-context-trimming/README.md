<!-- development-ledger:v2 -->

# Repeated wiki reads and destructive context trimming

## Status

| Field | Value |
| --- | --- |
| State | complete |
| Active phase | complete |
| Created | 2026-08-04T22:52:50+04:00 |
| Last updated | 2026-08-05T03:18:16+04:00 |
| Last consolidated | 2026-08-05T03:18:16+04:00 |
| Codebase branch | main |
| Codebase revision | 294f908e8ef5c4c31092439781bc49485d1e1e55 |
| Sources checked through | Arden: `9090f69f`; Codex: `95637f70`; OpenCode: `17544802`; Claude leak: `4b9d30f7`; Hermes: `cb06017b`; Letta: `b76da909`; official provider docs: 2026-08-04 |

## Original task — verbatim

got it
let's use new[$development-ledger](/Users/escept1co/.agents/skills/development-ledger/SKILL.md) for this
also research how other harnesses behave there
also use [$tool-harness-audit](/Users/escept1co/.agents/skills/tool-harness-audit/SKILL.md)  as wel

proceed

## Amendments — verbatim

> for large tool results we must use offloading ideally

> agree that we MUST NOT change the current context window, only append

> and what about additional table? you're saying we don't ned to use it right?

> you may proceed then
> make sure you will write proper tests for this functionality as well please.

## Current synthesis

Arden now bounds/offloads large results when they enter context, never by rewriting a later request. Normal model history is append-only; only explicit compaction replaces it with a durable handoff checkpoint. `sessions.messages` remains the authoritative active projection, while immutable `session_messages` rows recover a corrupt projection from the latest checkpoint and ordered retained-tail IDs. Schema v5 adds a generation fence and isolated context transactions. Branch snapshots and their recovery records commit atomically; malformed projections fail closed during result pruning; raw readable files and exact tool envelopes remain separately recoverable. No new current-context table was added.

## Decisions

- Fix canonical-history corruption before behavioral loop suppression.
- Prefer an unchanged-resource receipt response over a generic duplicate-call fuse.
- Defer a repeated-successful-read fuse until post-fix transcript/eval evidence justifies its false-positive risk.
- Bind content-read receipts to effective model visibility; preserve version receipts separately for mutation safety.
- Remove aggregate request-time tool-result trimming and scheduled-loop tail trimming.
- Bound normal and recovered tool results before they enter model history.
- Treat the handoff summary as the active-context checkpoint; persist its ordered retained-tail IDs.
- Treat `sessions.messages` as the authoritative active projection; use the transcript/checkpoint only when that projection is invalid.
- Keep existing transcript rows immutable during normal saves. Explicit user rewind/deletion remains an intentional exception.
- Preserve provider-native protocol state verbatim; bound only derived/event projections and retire opaque state through explicit compaction.
- Pin expiring raw-result manifests while their tool result remains in the active projection.
- Fence every normal save by its loaded context generation; advance the fence only for guarded clear/rewind.
- Run projection/transcript/checkpoint writes on an isolated transaction connection; never restore generation from a cold bundle.
- Commit a branch snapshot and its referenced tool/background recovery rows in one transaction; treat local cache files as rebuildable.
- Keep exact serialized tool envelopes durable even when readable content is offloaded first.
- Fail closed when expiry sees a malformed active projection rather than deleting manifests needed for transcript recovery.

## Open questions

- Accepted live-observation gap: after deployment, does any transcript still exhibit three or more identical successful reads? This determines whether the deferred fuse is justified.
- Legacy handoff rows without ordered-tail metadata use the valid active projection rather than guessed reconstruction.

## Next action

After deployment, observe whether successful identical reads still recur before considering the deferred fuse.

## Details

- [Research](research.md)
- [Implementation](implementation.md)
- [Verification](verification.md)
