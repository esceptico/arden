# Local Tool Execution

Status: protocol direction agreed; storage and tooling designs remain open.

## Outcome

Arden can run on a VPS while selected tools execute on a connected user
machine. The agent loop, automations, approvals, policy, and durable run state
remain server-side.

Tool authors use one Arden execution API and do not depend on HTTP, SSE,
WebSocket, cursors, leases, or protocol versions.

## Decided boundaries

```text
Arden server
  agent loop
  automation scheduler
  approvals and policy
  canonical run state
  tool routing
        |
        | SSE: execute_tool / cancel_tool
        | HTTP: tool_accepted / tool_progress / tool_completed
        v
Desktop executor
  local capabilities
  local tool implementations
```

- The server always owns the model/tool loop.
- The desktop is a thin executor, not a second Arden runtime.
- Server-to-desktop control uses SSE.
- Desktop-to-server responses use HTTP POST.
- Connections are long-lived per executor, not opened per tool.
- AG-UI/chat SSE remains a separate UI projection.
- Tool execution uses at-least-once delivery with idempotent processing.
- A durable invocation is the source of truth; SSE events are a bounded
  delivery projection.
- The execution abstraction is project-wide. Placement and required
  capabilities are declared per tool.

## Current Arden boundary

Today, `ArdenToolExecutor` directly calls the canonical registry:

```text
model tool call
  -> ToolRunner
  -> registry.execute(...)
  -> ToolResult
  -> model loop continues
```

The routing seam belongs immediately before `registry.execute(...)`.

Existing `TOOL_CALL_START`, `TOOL_CALL_RESULT`, and related AG-UI events are UI
events. They are not commands sent to an executor.

Existing `POST /tools/result` resolves approval or interactive input. It must
not be reused for remote execution results.

## Project-wide execution interface

Keep the existing developer-facing shape:

```python
async def execute(execution: ToolExecution, **arguments) -> ToolResult:
    ...
```

Add a runtime-owned routing layer:

```text
ExecutionRouter
  -> InProcessExecutionBackend
  -> DesktopExecutionBackend
```

The in-process backend preserves current behavior. The desktop backend creates
a durable invocation, delivers it, and waits for its terminal outcome.

Tool code must never:

- construct executor HTTP requests;
- inspect transport cursors or connection IDs;
- implement replay, authentication, or retry loops;
- branch on SSE versus WebSocket;
- contain separate server and desktop orchestration logic.

## Per-tool requirements

Execution requirements belong in canonical tool metadata, alongside the
existing policy:

```text
placement: server | desktop | either
capabilities: filesystem, shell, browser, computer_control, ...
interaction: one_shot | streaming | interactive
```

Examples:

| Tool family | Placement |
|---|---|
| Memory, automations, run management | Server |
| User filesystem and shell | Desktop |
| Portable read-only integrations | Either, when supported |
| Mouse, keyboard, screenshots | Desktop with `computer_control` |

The server selects a compatible connected executor per invocation. Tools never
select a transport.

Capability-specific APIs may be added to `ToolExecution`, for example a
`ComputerController`. Their implementations may be local or proxied, but their
developer-facing API remains stable.

## Executor identities

Do not overload chat `session_id`.

| Identity | Lifetime | Purpose |
|---|---|---|
| `executor_id` | Installation | Stable enrolled desktop identity |
| `lease_id` | Active assignment | Server-issued fencing token |
| `connection_id` | One SSE connection | Distinguishes reconnects and stale streams |
| `event_id` | Delivery event | Replay cursor, encoded as stream plus sequence |
| `invocation_id` | One logical execution | Idempotency and durable outcome |
| `tool_call_id` | Agent step | Links the invocation to the model tool call |

Chat session, run, automation, and tool-call IDs are invocation metadata. They
do not define the executor stream.

## Proposed HTTP surface

One authenticated path is the default:

```text
POST   /v1/executor   initialize, accepted, progress, completed
GET    /v1/executor   SSE control stream
DELETE /v1/executor   best-effort lease termination
```

Use Arden-specific headers and types. The lease is not authentication; every
request also carries the enrolled device credential over TLS.

The desktop executor connection should be owned by the Electron main process
or a dedicated sidecar, not by an individual renderer window.

## Message lifecycle

### Normal completion

```text
server                    desktop
  |                          |
  |-- execute_tool --------->|
  |<-- tool_accepted --------|
  |<-- tool_progress --------|  optional and coalescible
  |<-- tool_completed -------|
  |                          |
  `-> create ToolResult and continue the server-side loop
```

`execute_tool` contains:

- invocation and tool-call identity;
- tool name and validated arguments;
- deadline;
- required capability/version;
- approval grant reference when applicable;
- trace and originating run metadata.

`tool_accepted` means the executor durably claimed the invocation. It is not
user approval.

`tool_progress` is optional. It is for meaningful live state or bounded output,
not a mandatory log of every subprocess byte.

`tool_completed` is the single logical terminal outcome:

```text
success | failed | cancelled | uncertain
```

It carries the bounded `ToolResult` projection and references to any large
payloads or artifacts.

### Cancellation

```text
server -- cancel_tool(invocation_id) --> desktop
desktop -- tool_completed(...) -------> server
```

`cancel_tool` is a request, not proof of cancellation. If a state-changing
operation may already have taken effect, the terminal status is `uncertain`,
not `cancelled`.

Run cancellation and timeouts are still decided by the server. The server
propagates cancellation to every active local invocation belonging to that run.

### Approval

Approval remains entirely server-side:

1. The model requests a tool.
2. Arden applies canonical policy and records any required approval.
3. The user or automation policy resolves it.
4. Only an approved call becomes an executor invocation.
5. The executor cannot weaken or bypass the decision.

## Replay and idempotency

Every SSE control event has an opaque, ordered `event_id`. On reconnect, the
desktop supplies its last applied cursor and the server replays later events
before joining the live stream.

Required behavior:

- Delivery is at least once.
- The desktop persists enough state to deduplicate both `event_id` and
  `invocation_id` across process restarts.
- Duplicate `execute_tool` never starts a second execution.
- Duplicate terminal POSTs return the already accepted outcome when identical.
- Conflicting terminal POSTs fail with a typed conflict.
- A server-issued lease fences results from a stale or reassigned executor.
- A disconnect never means cancellation.
- State-changing work found in an uncertain executing state is never
  automatically retried.

## Execution-specific persistence

The broader storage design is still pending. The protocol requires these
conceptual records:

```text
executor_devices
executor_leases
tool_invocations
executor_outbox
```

Rules:

- `tool_invocations` is durable run state.
- `executor_outbox` is a bounded replay buffer retained until acknowledgement
  plus a short grace period.
- Arguments and results are stored once; delivery events reference them.
- Large bodies use the existing blob/reference approach.
- Progress is coalesced or transient unless it is part of the final audit.
- Multi-worker VPS deployments require shared invocation/outbox state or an
  explicit routing owner; the current process-local chat bus is insufficient.

Exact schema, retention, compaction, and migration belong to the storage
design.

## Protocol evolution

Domain messages and wire transport are separate:

```text
ToolInvocation / ToolOutcome
          |
ExecutorProtocol adapter
          |
SSE+HTTP v1 | future WebSocket/WebRTC adapter
```

The connection handshake advertises supported protocol versions, tool
implementations, and capabilities. During migrations, the server may run two
protocol adapters while keeping one execution router and one domain model.

Computer control may later add a high-bandwidth or interactive data channel.
The common control plane still owns invocation, approval, cancellation,
outcome, persistence, and audit semantics.

## Implementation phases

### 1. Extract the execution seam

- Introduce typed invocation/outcome domain models.
- Add `ExecutionRouter` and `ExecutionBackend`.
- Route every existing tool through `InProcessExecutionBackend`.
- Preserve current schemas, approvals, events, audit rows, and results.

Proof: existing server tool and automation suites pass without behavior
changes.

### 2. Finalize storage

- Design executor, lease, invocation, and outbox records.
- Define retention, compaction, blob references, and migration.
- Define compare-and-swap transitions and idempotency constraints.

Blocked on the storage discussion.

### 3. Finalize local tooling

- Choose the desktop executor host and packaging model.
- Define capability and tool-version advertisement.
- Define local tool discovery, registration, sandboxing, and updates.
- Preserve one canonical schema/policy registry.

Blocked on the tooling discussion.

### 4. Implement the server protocol

- Add enrollment/authentication and lease lifecycle.
- Add the SSE control stream with cursor replay.
- Add idempotent accepted/progress/completed POST handling.
- Integrate cancellation, deadlines, recovery, and tracing.

### 5. Implement the desktop executor

- Run one executor connection outside renderer windows.
- Advertise supported capabilities and versions.
- Persist cursor and invocation receipts.
- Execute, cancel, bound, and return local tool outcomes.

### 6. Pilot one safe tool

- Route one read-only local tool through the desktop backend.
- Keep all other tools in-process.
- Test reconnects, duplicate delivery, desktop restart, and server restart.

### 7. Expand deliberately

- Add filesystem and shell tools.
- Verify approvals and uncertain mutation recovery.
- Enable server-side automations to target connected executors.
- Add computer-control capabilities only after the common lifecycle is proven.

## Required end-to-end tests

- Existing in-process tools remain unchanged.
- An approved local call executes exactly once after duplicate delivery.
- A rejected call is never delivered.
- Cursor replay recovers a dropped connection without losing work.
- A stale lease cannot submit results.
- Desktop restart resends a completed result instead of re-executing.
- Run cancellation reaches the executor and produces a terminal outcome.
- Mutation timeout/cancellation becomes `uncertain` when completion is unknown.
- Offline executor behavior follows the server automation retry/failure policy.
- Large outputs use references rather than duplicating payloads in events.
- Two server workers observe the same invocation and outbox state.
- AG-UI/chat reconnect behavior remains independent from executor reconnects.

## Storage implementation tiers

Apply storage changes from simplest and most reversible to the deepest semantic
change. Measure again after each tier; later tiers are optional if earlier work
solves the practical problem.

### Tier 1A — Large merge backups

Archive the two July 10 pre-merge `sessions.db` snapshots, verify checksums and
SQLite integrity, then remove the uncompressed local originals.

Measured uncompressed footprint: **13.14 GiB**.

This has no runtime impact: Arden opens only `sessions.db`, and neither backup
is referenced or held open by the running server.

Completed 2026-07-27 using a local lossless archive because no suitable
external volume was available:

- originals: **14,109,900,800 bytes**;
- verified archives and manifest: **2,435,525,324 bytes**;
- net reclaimed: **11,674,375,476 bytes (10.87 GiB)**;
- both archives passed `zstd` integrity and streamed SHA-256 verification.

Manifest:
`~/.arden/archive/merge-backups-20260710/manifest.md`.

### Tier 1B — Remaining historical backups

Classify every remaining backup as:

- active automatic recovery;
- settings fallback;
- historical migration snapshot;
- obsolete legacy database;
- unknown and therefore retained.

Archive and remove only resolved historical/legacy snapshots. Preserve active
recovery files and introduce explicit count, age, and byte limits later.

Measured candidate population before classification: **2.01 GiB**.

Archive phase completed 2026-07-27:

- classified and archived: **28 files / 2,164,198,647 bytes**;
- verified archives: **1,266,625,683 bytes**;
- every archive passed `zstd` integrity and decompressed SHA-256 verification;
- net reclaimed after source removal:
  **897,572,964 bytes (approximately 856 MiB)**.

Manifest:
`~/.arden/archive/historical-backups-pre-20260727/manifest.tsv`.

Source removal completed after exact manifest approval. All 28 source paths
are absent and all 28 verified archives remain. Active databases,
`settings.json.bak`, Tier 1A/2 artifacts, and unclassified
`app.db`/`state.db` were excluded and remain present.

### Tier 2 — Compact `search.db`

Rebuild the derived FTS data into a fresh database, verify schema, row counts,
representative searches, and `PRAGMA quick_check`, then atomically replace the
old database.

Previous temporary-copy measurement: **661 MiB → approximately 6 MiB** with no
indexed records removed.

Completed 2026-07-27:

- file size: **693,149,696 → 6,520,832 bytes**;
- reclaimed: **686,628,864 bytes (654.8 MiB)**;
- freelist: **146,894 → 0 pages**;
- preserved: schema, triggers, metadata, 78 items, 78 FTS documents,
  78 vectors, source counts, canonical-item hash, and representative searches;
- `PRAGMA quick_check` and Arden `SearchStore` smoke tests passed.

The search implementation was not semantically broken. The database lacked a
compaction lifecycle: deleted rows and obsolete FTS segments stayed allocated
inside the SQLite file.

### Tier 3 — Prevent recurrence

- Apply the inline limit to the complete serialized event, not only `content`.
- Enforce session-event limits from database state across restarts.
- Replace completed outbox payloads with compact status/hash receipts.
- Keep large raw tool output out of FTS.
- Add global byte budgets and storage observability.

This tier may reclaim little immediately, but it must precede the legacy
backfill so migrated space does not grow back.

### Tier 4 — Backfill legacy tool results

- Find oversized inline `TOOL_CALL_RESULT` events.
- Move raw bodies into compressed content-addressed blobs.
- Replace inline bodies with standard verified references.
- Verify hashes, references, counts, and model-visible projections.
- Vacuum `sessions.db` only after the migration is proven.

Expected measurement from the audit:

- `sessions.db`: roughly **7.1 GiB → 2–3 GiB**;
- blobs: approximately **1.2 GiB** added;
- net local saving: approximately **3 GiB**;
- raw evidence remains recoverable.

### Tier 5 — Canonical transcript cleanup

Resolve duplication among `sessions.messages`, `session_events`, completed
outbox histories, and tool-result projections. Select one canonical durable
representation and make the others rebuildable projections.

This changes restart, replay, history, and search semantics and therefore needs
production-shaped recovery tests.

### Tier 6 — Long-term storage architecture

- Compress or archive immutable cold history.
- Apply short TTLs to full diagnostic/provider payloads.
- Garbage-collect blobs only after reference-aware reachability checks.
- Automate backup lifecycle and restore verification.
- Decide SQLite versus shared database/object storage for VPS and multi-worker
  deployment.
- Define online migration, rollback, and mixed-version behavior.

Recommended execution order:

```text
1A → 1B → 2 → 3 → 4 → measure again → 5/6 only if justified
```

## Remaining discussions

### Storage

- Canonical run, transcript, event, and invocation model.
- Final inline/blob thresholds and global byte budgets.
- Retention, cold compression, archival, and garbage-collection policy.
- Backup destinations, lifecycle, and restore-test cadence.
- SQLite versus future shared VPS storage requirements.

### Tooling

- Which tools remain server-side versus execute locally.
- Desktop executor runtime and language.
- Tool packaging, discovery, installation, and updates.
- Capability manifests and compatibility.
- Filesystem/process isolation and permission model.
- Local tools, MCP tools, and built-in tools behind one canonical registry.
