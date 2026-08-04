<!-- development-ledger:v2 -->

# Repeated wiki reads and destructive context trimming

## Status

| Field | Value |
| --- | --- |
| State | complete |
| Active phase | complete |
| Created | 2026-08-04T22:52:50+04:00 |
| Last updated | 2026-08-04T23:05:00+04:00 |
| Last consolidated | 2026-08-04T23:01:44+04:00 |
| Codebase branch | main |
| Codebase revision | 9090f69f96e5b75312543d3217c9a7e665d44fa0 |
| Sources checked through | Arden: `9090f69f`; Codex: `95637f70`; OpenCode: `17544802`; Claude leak: `4b9d30f7`; Hermes: `cb06017b`; Letta: `b76da909`; official provider docs: 2026-08-04 |

## Original task — verbatim

got it
let's use new[$development-ledger](/Users/escept1co/.agents/skills/development-ledger/SKILL.md) for this
also research how other harnesses behave there
also use [$tool-harness-audit](/Users/escept1co/.agents/skills/tool-harness-audit/SKILL.md)  as wel

proceed

## Amendments — verbatim

None.

## Current synthesis

Arden issued 14 successful wiki reads before the first mutation in one live run. The implemented fix separates durable/canonical history from request-only limiting, makes every evicted result recoverable, invalidates content-read receipts when their model-visible result is evicted, and returns a compact unchanged receipt for repeated visible full-page reads. This follows the strongest shared pattern from Claude Code, Hermes, OpenCode, Anthropic context editing, and Codex's separate persisted rollout.

## Decisions

- Fix canonical-history corruption before behavioral loop suppression.
- Prefer an unchanged-resource receipt response over a generic duplicate-call fuse.
- Defer a repeated-successful-read fuse until post-fix transcript/eval evidence justifies its false-positive risk.
- Bind content-read receipts to effective model visibility; preserve version receipts separately for mutation safety.

## Open questions

- Accepted live-observation gap: after deployment, does any transcript still exhibit three or more identical successful reads? This determines whether the deferred fuse is justified.

## Next action

Deploy normally and inspect the next naturally occurring repeated-read workflow before considering a fuse.

## Details

- [Research](research.md)
- [Implementation](implementation.md)
- [Verification](verification.md)
