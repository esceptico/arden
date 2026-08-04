<!-- development-ledger:v2 -->

# Storage budget resource policies

## Status

| Field | Value |
| --- | --- |
| State | complete |
| Active phase | Implementation and regression verification complete |
| Created | 2026-08-03T23:35:29+04:00 |
| Last updated | 2026-08-04T01:34:31+04:00 |
| Last consolidated | 2026-08-04T01:34:31+04:00 |
| Codebase branch | codex/storage-retention-policies |
| Codebase revision | 4d97a12075b3c8303ec1863a0fddbe02d48f7871 |
| Sources checked through | code: 4d97a12075b3c8303ec1863a0fddbe02d48f7871; web: 2026-08-03 |

## Original task — verbatim

hmm
it would be nice to:
- have split by resources (like what takes how much space)
- some tooltip with explanations
- be able to set limit less than the current taken space (but with removal of data – start from the archives, finishing with current chats)

tell me wdyt here (do not change anything for now)

## Amendments — verbatim

looks good
use [$development-ledger](/Users/escept1co/.agents/skills/development-ledger/SKILL.md) for that

---

about questions:
- backups are not too critical, for most users (and me) they are just some old logs. as i change harness pretty often they are not so useful
- i think they might be converted
- idk
- i think not now
- up to you

the main direction is a good UX and common data retention policies
you can google how other agents / services handle relevant / similar cases and borrow their approach

---

proceed please

## Current synthesis

Arden now reports additive physical resource categories, exposes concise explanations, and plans cleanup before mutating data. The previously observed 6.2 GiB footprint was dominated by 3.4 GiB of explicit legacy/merge backup archives, a 2.3 GiB session database, and 406 MiB of blobs.

The accepted design is a category-level inventory plus a dry-run cleanup planner. A user may set a limit below current usage, but the numeric limit alone never silently authorizes history deletion. The planner shows the exact categories, item counts, estimated physical bytes, protections, and ordered actions needed to reach the target; increasingly destructive tiers require explicit opt-in.

Cleanup proceeds from rebuildable/expired data, to old explicit backups, to archived chats, and finally to inactive current chats. The current/open chat, pinned chats, active runs, unfinished goals, and automation channels remain ineligible. If eligible data cannot reach the target, Arden reports the remaining floor and why.

External precedents support this shape. Docker and macOS lead with category totals and reclaimable/recommendation views; Docker requires explicit prune consent and excludes high-risk volumes unless opted in. GitHub/GitLab use TTLs for artifact-like data and GitLab adds a manual Keep exception. ChatGPT keeps archived chats until explicit deletion. LangGraph separates `delete` from `keep_latest` TTL strategies, while LangSmith retains ordinary traces briefly and promotes valuable traces to longer retention.

Arden's proposed defaults reflect that evidence and the user's frequent harness changes: auto-created backup/log archives expire after 14 days, with a manual Keep escape and no immortal newest-backup rule. Archived chats are cold-converted to a full-fidelity, restorable compressed bundle before deletion becomes eligible. Current chats remain unlimited by default; the optional aggressive tier considers only chats inactive for 90 days, retains at least the 100 most recent, and runs interactively while pins remain desktop-owned.

The safety prerequisites are implemented. Permanent deletion dynamically removes every table row owned by the session in one transaction. Archived chats cold-convert to deterministic, verified tar+zstd bundles and rehydrate before restore. New databases use incremental auto-vacuum; the verified offline compactor migrates existing databases and reports when that one-time step remains necessary.

## Decisions

- Replace aggregate “protected” wording with additive resource categories, each showing total, reclaimable amount, policy, and a concise tooltip.
- Accept limits below current usage and preview the concrete cleanup plan before applying it.
- Use an ordered cleanup ladder: rebuildable/expired data → explicit backup archives → archived chats → inactive current chats.
- Require explicit opt-in for history tiers; setting a number is not consent to delete chats.
- Never auto-delete the current/open chat, pinned chats, active runs, unfinished goals, or automation channels.
- Report a truthful quota floor and blocking reasons when protected data prevents reaching the limit.
- Treat explicit files under `~/.arden/archive` separately from archived chat rows in `sessions.db`.
- Count physical filesystem bytes as the top-level budget; label logical/database estimates when exact physical attribution is impossible.
- Default auto-created backup/log archive retention to 14 days; allow manual Keep, but do not keep one backup forever merely because it is newest.
- Cold-convert archived chats to a full-fidelity, restorable compressed bundle before permanent deletion eligibility.
- Keep current chats indefinitely by default. An explicit aggressive tier may consider only chats inactive for 90 days and must retain at least the 100 most recent.
- Until pins become server-owned, current-chat cleanup is interactive-only and receives the desktop's current/pinned set; background maintenance cannot delete current chats.
- Use last meaningful access/activity for retention eligibility, not creation time, and refresh it when a chat is opened or used.
- Migrate the session database once to SQLite incremental auto-vacuum, then reclaim in bounded idle chunks; reserve full verified `VACUUM INTO` for the migration or exceptional fragmentation.

## Open questions

- No blocking product-policy or implementation question remains. The visible 14-day backup, 90-day current-chat inactivity, and 100-chat floor defaults should be tuned later from usage.

## Next action

Review the branch diff, then hand it back for commit/merge direction.

## Details

- [Research](research.md)
- [Implementation](implementation.md)
- [Verification](verification.md)
