<!-- development-ledger:v2 -->

# Slow server startup and API readiness

## Status

| Field | Value |
| --- | --- |
| State | implementing |
| Active phase | I-01-I-06 and I-08 verified; I-07 partial; I-09/I-10 remain |
| Created | 2026-08-03T18:19:19+04:00 |
| Last updated | 2026-08-03T19:51:03+04:00 |
| Last consolidated | 2026-08-03T19:51:03+04:00 |
| Codebase branch | codex/slow-startup-readiness |
| Codebase revision | 22c37b28928c660c1cd1950cb7fcc4c55f280bf3 |
| Sources checked through | code: 22c37b28928c660c1cd1950cb7fcc4c55f280bf3; web: 2026-08-03 |

## Original task — verbatim

server starting really slow
also, sometimes some active automations are enlarge loading time (i need to wait until api will be available)

i want to understand how to fix it
use [$development-ledger](/Users/escept1co/.agents/skills/development-ledger/SKILL.md)

## Amendments — verbatim

thanks!

additional research points regarding to memory:
- outbox is out poor man's redis, so 1 month retention is too much there
- check how other agents (~/src/letta-code, ~/src/codex, ~/src/hermes-agent, ~/src/opencode, etc) work with transcriptions. i want to understand how to optimize the space
  - also we had a convo about this before, worth to search across chats

after that we will fix all one by one

---

thanks!

also, a few small points worth to add to the ledger (after research):
- worth to add control of the space taken my the arden (simple retention policy, like "max space" or something)
- research this format for transcriptions: https://www.harborframework.com/docs/agents/trajectory-format
  - if possible, we might store old transcripts in this format
- i saw some ideas like
  - old transcripts might omit detailed tool calls
  - store big tool calls results as blobs

please proceed with research and update ledger accordingly

---

also add this to your research list: https://www.letta.com/blog/trajectory/

---

alright!
would be nice to verify this angle (trajectories and stuff) in other harnesses (i sent a list of them before)
i think it's the latest item i need for now to cover

---

good. create some branch and proceed with changes then, please

---

commit + proceed with other changes please. use [$development-ledger](/Users/escept1co/.agents/skills/development-ledger/SKILL.md) as well

## Current synthesis

Two problems compound:

1. **Baseline startup is structurally blocking.** FastAPI cannot serve until the lifespan reaches `yield`, but Arden performs database/schema work, wiki/fact index sync, health projection, recovery, scheduler seeding, and another wiki projection first. Recent starts took **55s**, **107s**, and **235s**.
2. **Due/active automations amplify it through an ordering race.** Arden starts the scheduler/outbox, then awaits wiki projection before `yield`. Catch-up automations can write the wiki while startup is projecting it, causing retries/lock contention. The 235s start spent 108s after the scheduler started; due maintenance automations fired in that window.

The live `sessions.db` is **8.0 GB**: `session_events` 5.0 GiB, `session_messages` 1.2 GiB, and `outbox_events` 0.7 GiB. Historical retention was not backfilled, completed outbox rows duplicate full transcripts, and the next boot may perform a one-time rewrite of the 1.2 GiB message table to remove a legacy FTS column.

The cross-agent review supports a three-tier storage contract: canonical transcript rows; bounded model-facing tool previews; and large raw artifacts stored once behind references with independent expiry. The outbox is transport/replay, not transcript history: after successful delivery, scrub its large payload immediately and retain only a compact receipt for a short grace window. Keep payloads only for pending/running/retry/dead work.

Arden needs one user-facing storage budget, enforced asynchronously with hysteresis and a protected-data floor. It should report all `~/.arden` bytes but automatically reclaim only managed data; active/protected sessions and explicit backup archives are never silently deleted. The current footprint is 12 GiB, including 7.5 GiB `sessions.db` and 3.4 GiB explicit archives.

Harbor ATIF is a good full-fidelity interchange/export format, not a space-efficient archive by itself. Letta Trajectory is designed for agent-readable old experience and defaults to bounded tool arguments/results. Before choosing the cold format, benchmark representative Arden sessions as ATIF-v1.7 JSON+zstd versus Letta-v1 JSON+zstd. In both, full tool bodies should remain content-addressed blobs, not inline transcript text.

The harness verification fixes the role boundary: only Letta Code exposes normalized trajectories as a first-class feature, and it writes replaceable, uncompressed snapshots under `/tmp` for memory review. Codex keeps native rollout JSONL, Hermes exports full session JSONL or human-readable Markdown, and OpenCode exports/imports full session JSON. None uses ATIF or a normalized trajectory as its authoritative cold store. Arden should therefore pilot trajectory bundles beside canonical rows; deletion or replacement needs separate restore proof.

**Recommended fix:** expose the core API before recoverable/rebuildable warmup work; run recovery and projections in supervised background phases; start due automations only after recovery is scheduled; move large schema/data migrations out of normal boot; then losslessly migrate/prune/compact the legacy database.

## Decisions

- Core HTTP readiness now precedes model-catalog refresh, MCP connection, recovery, indexing/health projection, scheduler startup, and storage maintenance.
- Recovery durably enqueues the current wiki projection before starting catch-up automations.
- Completed outbox payloads become compact hash receipts immediately and receipts retain for 24 hours.
- `max_space_gb` is optional. Arden reports all bytes, deletes only stale unreferenced tool-result blobs, targets 85% of the cap, and reports `quota_blocked` instead of deleting canonical history or backups.
- Trajectory bundles remain derived data. I-09/I-10 are intentionally not folded into normal server startup.

## Open questions

- The user's desired `max_space_gb` value remains unset by default.
- The final cold transcript format remains gated on I-09's side-by-side benchmark.
- Legacy inline-result migration and SQLite compaction require an approved offline maintenance window with enough temporary disk space.

## Next action

Finish I-09's non-destructive trajectory benchmark, then prepare I-10 for a separately approved offline maintenance window.

## Details

- [Research](research.md)
- [Transcript storage comparison](transcript-storage-comparison.md)
- [Implementation](implementation.md)
- [Verification](verification.md)
