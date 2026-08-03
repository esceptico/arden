# Transcript storage comparison

Checked 2026-08-03 against local repository HEADs.

| Harness | Canonical transcript | Large tool output | Compaction | Retention weakness |
| --- | --- | --- | --- | --- |
| Letta Code | Local backend: append-only JSONL | Bounded response; full file expires after 24h | Summary + first-kept pointer | Reflection transcript/payload copies and session index are unbounded |
| Codex | Append-only rollout JSONL with full response items | Full outputs retained inline | Appends full replacement-history snapshots | 7-day cold zstd exists but is disabled; 11.8 GiB plain locally |
| Hermes Agent | Normalized SQLite messages + FTS | Full text; binary image content stripped | Marks rows compacted | 90-day whole-session prune is opt-in; standard FTS still indexes tool text |
| OpenCode | Normalized SQLite message/part rows | 50 KiB/2,000-line preview; full file expires after 7d | Summary; optional model-context pruning | Pruning does not erase stored previews; media may remain inline |

## Verified trajectory and export behavior

| Harness | Native portable surface | Normalized trajectory role | Authoritative cold store? |
| --- | --- | --- | --- |
| Letta Code | `letta trajectories export`: trajectory-v1 JSON + manifest | First-class, replaceable `/tmp` dataset for memory review | No evidence; derived and rebuildable |
| Codex | Native rollout JSONL; external-history import | External adapter only; no native ATIF/trajectory export | No; native rollouts remain canonical |
| Hermes Agent | Full-session JSONL backup/import; Markdown/QMD view | External adapter only | No; export retains native/full messages |
| OpenCode | Full `{info,messages}` JSON export/import; optional redaction | External adapter consumes exported data | No; export retains native/full parts |

The shared pattern is **canonical native history plus derived views**. None of the four validates deleting canonical records after producing ATIF or Letta-v1. Arden's first trajectory implementation should therefore be reproducible and side-by-side; storage replacement is a later decision gated by restore proof.

## Recommended Arden contract

| Data class | Durable form | Lifecycle |
| --- | --- | --- |
| User/assistant transcript | Canonical normalized rows | User-controlled history; archive/compress separately |
| Tool result | Bounded text preview + metadata + artifact reference | Preview follows transcript; raw artifact has declared TTL/pin policy |
| Binary/media | Content-addressed artifact reference | Store once; never duplicate base64 into transcript/event/search rows |
| Session event stream | Compact structural replay events | Strict per-session row/byte cap reconciled after restart |
| Outbox pending/retry/dead | Full delivery payload or canonical-record reference | Keep while actionable; dead until repaired/quarantined |
| Outbox completed | Compact receipt: id/type/key/hash/timestamps/attempts | Scrub payload immediately; delete receipt after short grace (proposed 24h) |
| Diagnostics | Size and age bounded | Rotate/prune asynchronously; never own startup readiness |

## Why this optimizes space

1. Store each large body once.
2. Bound amplification at write time, before events, FTS, compaction, or outbox can copy it.
3. Keep replay durability independent from human transcript retention.
4. Compress/archive cold canonical history only after duplication is removed.

Whole-session deletion is optional policy, not the primary fix. The first wins are completed-outbox payload scrubbing, historical event-cap reconciliation, and bounded tool/media writes.

## Cold-format candidates

| Format | Designed for | Space characteristics | Arden fit |
| --- | --- | --- | --- |
| [Harbor ATIF-v1.7](https://www.harborframework.com/docs/agents/trajectory-format) | Full replay, evaluation, training interchange | Verbose JSON; full structured calls/results/metrics; no native compression or general blob refs | Export/full-fidelity archive; `extra` can describe Arden refs, but a lossy transcript is no longer ATIF's intended complete history |
| [Letta Trajectory v1](https://www.letta.com/blog/trajectory/) | Agent-readable cross-harness experience | Drops harness noise; 20k argument and 2.5k result defaults; tool results may be omitted | Strong compact-search candidate; strict schema needs a sidecar for blob refs, hashes, and omission metadata |

### Proposed archive bundle

```text
<session-id>/
├── manifest.json
├── trajectory.json.zst
└── refs.json
```

- `manifest.json`: schema/profile versions, source revision, counts, omissions, hashes, and restoration contract.
- `trajectory.json.zst`: selected upstream format, compressed. Preserve user/assistant prose; retain bounded tool name/status/preview; omit detailed arguments/results only under an explicit profile.
- `refs.json`: tool-call/result IDs to existing `sha256:` blobs. Blobs remain global and deduplicated, not copied into each bundle.

Initially keep this bundle beside canonical rows. Do not replace canonical rows until a representative pilot proves byte savings, validation, search quality, ordering, reconstruction limits, and blob-reference integrity. This stronger gate matches the reviewed harnesses; none uses trajectories as authoritative storage.

## Space-budget behavior

One user-facing `max_space_gb`; fixed 85% low-water hysteresis.

1. Measure all Arden-owned paths and classify them as reclaimable or protected.
2. Reclaim expired temp/log/outbox data, then unreferenced blobs, then eligible cold archived sessions.
3. Never silently delete active/pinned sessions or explicit backup archives.
4. If protected bytes exceed the cap, report `quota_blocked` and require a user decision.

The cap is therefore a managed-data budget, not a false promise that protected data can never exceed it. SQLite file shrinking remains explicit maintenance; row deletion alone does not return filesystem space.
