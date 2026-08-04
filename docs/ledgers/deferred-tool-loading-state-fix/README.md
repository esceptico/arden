<!-- development-ledger:v2 -->

# Deferred tool loading state fix

## Status

| Field | Value |
| --- | --- |
| State | complete |
| Active phase | verification complete |
| Created | 2026-08-04T17:24:59+04:00 |
| Last updated | 2026-08-04 |
| Last consolidated | 2026-08-04 |
| Codebase branch | codex/storage-retention-policies |
| Codebase revision | 7ce714666ee689aa0720148015c5f588e7e95300 |
| Sources checked through | Arden code: base revision above plus this branch's implementation commit; comparison repos: revisions in research.md; web: 2026-08-04 |

## Original task — verbatim

how we can fix that? maybe worth to look at how other harnesses (~/src/letta, ~/src/codex, ~/src/claude-code-leaked, ~/src/hermes-agent, ~/src/opencode) working with tool loading
and worth to check openai and anthropic documentation about deferred tools as well
[$development-ledger](/Users/escept1co/.agents/skills/development-ledger/SKILL.md)

## Amendments — verbatim

you can use subagents to research over different directories / searches

let's do the following:
1. add this info from claude to the ledger (i want you to research it if you didn't)
2. start with tool loading
3. proceed with other fixes (including these ones from claude, all of them if you checked they are real)

also agents quite often just trying to edit before reading
why so?
also we must give them ability to edit if fire was not changed

maybe we need to adjust prompt as well?

sounds good (and patch) as well

you may proceed

## Current synthesis

The incident's primary cause was run-local wiki read evidence: all 9 failed edits crossed a run boundary, while both successful edits followed a read in the same run. The model could see the read but the edit gate could not, producing an unsatisfiable retry loop and eventually `noop`/`oops`. Cancellation then dispatched another run and prolonged the cascade.

The verified fixes are implemented. Native-capable requests now expose only provider-native search, loaded-tool state is reconstructed from structured history and preserved through compaction, wiki observations survive turns with content authority downgraded on compaction, cancellation does not create a narration run, failure recovery metadata reaches models, and workflow presets are deferred, approved, bounded, and validated before spawning.

Wiki mutation ergonomics now match enforcement: full-body replacement names and enforces its read prerequisite before approval; a prior read remains valid while unchanged; native mutation discovery exposes the reader too; and `wiki_patch_page` performs a unique exact-text compare-and-swap without requiring a full read.

## Decisions

- Research recommendation: native OpenAI/Anthropic paths use only their native deferred-tool protocol; fallback models use only Arden's `load_tools`.
- Discovery receipts are conversation evidence, not authorization. Every sampling step intersects them with the current registry, capabilities, permissions, and allowlist.
- Compaction/resume preserves a structured discovery baseline; every turn intersects it with the current allowed deferred-tool catalog.
- Wiki observations are session-scoped. Compaction removes content-read authority while preserving version/head evidence for optimistic concurrency.
- Generic repeated-call suppression was not added: identical calls can be valid in stateful workflows. The proven loops were removed at their state and cancellation boundaries instead.
- A prior wiki/file read remains valid across turns while the resource version is unchanged. Exact-text file edits are independently guarded by a unique current-content match.
- Server-placed mutation preflight may return only typed failures and always runs before approval; execution-time validation/CAS remains authoritative after approval.

## Accepted gap

- Wiki observation continuity is process-local. A restart safely requires another read; a durable explicit read token remains optional hardening.

## Open questions

None blocking completion.

## Next action

Review the working-tree diff and commit when desired.

## Details

- [Research](research.md)
- [Implementation](implementation.md)
- [Verification](verification.md)
