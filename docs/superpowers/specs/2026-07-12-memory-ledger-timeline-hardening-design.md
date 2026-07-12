# Memory Ledger and Timeline Hardening Design

## Goal

Make ntrp memory trustworthy as a local, file-canonical personal knowledge system. Every durable memory must preserve its identity, scope, time, and evidence while remaining understandable and editable as Markdown.

Dex is only a source of design observations. This design does not copy its schema, prompts, storage model, or implementation.

## Success Criteria

- Markdown remains the canonical knowledge store.
- Every engine-authored record has stable identity, scope, temporal metadata, and exact evidence references.
- Moving a record between pages never changes its scope or identity.
- Canonical writes are recoverable and watermarks advance only after they commit.
- User-created files and directories are allowed and discoverable through managed indexes.
- Daily Markdown provides a useful, time-granular activity view without becoming canonical evidence.
- Derived prose, search indexes, profiles, and daily summaries can be deleted and rebuilt.
- Existing local vaults migrate automatically with a backup and without invented timestamps.

## Approaches Considered

1. **Layered Markdown ledger — selected.** Keep durable records in page timelines, retain exact source references, and generate daily summaries as projections. This preserves human readability without confusing summaries with evidence.
2. **Patch the current record lines only.** Smaller, but leaves page placement, provenance, update history, and daily summaries too tightly coupled.
3. **Mirror every raw source event into Markdown.** Maximally transparent, but duplicates source stores, creates large noisy vaults, and makes human editing impractical.

## Vault Contract

```text
memory/
  index.md
  AGENTS.md
  me.md
  daily/YYYY-MM-DD.md
  topics/...
  <any user-created files and directories>
  .ntrp/
    backups/
    journal/
    indexes/
    maintenance/
```

Only `.ntrp/` is an engine-only namespace. Conventional paths such as `topics/` and `daily/` are defaults, not a restriction on user organization. Engine-created pages declare their purpose in frontmatter so behavior does not depend only on their path.

`index.md`, `AGENTS.md`, and directory `README.md` files may contain user prose. The engine owns only explicitly marked sections inside them and never rewrites content outside those markers.

### Directory indexes

The root `index.md` lists its immediate children. Every nested directory has a `README.md` section listing its immediate children. A scan adds missing entries and removes entries for paths that no longer exist.

Descriptions are resolved in this order:

1. explicit `summary` frontmatter;
2. an existing user-edited description in the managed index;
3. the first meaningful sentence or heading from the file;
4. `Needs description`, also reported by memory health.

A compact root map is available to the resident agent context. Deeper directory contents remain pull-based through memory browsing and search.

Arbitrary Markdown and text files are searchable resources. They do not become atomic facts merely because they exist. A record may cite them as evidence using a path plus an optional line or content anchor.

## Page and Record Format

A memory page has optional human or synthesized prose followed by a managed record ledger. The readable record line carries the fields a human commonly needs:

```md
<!-- ntrp:records schema=2 -->
- 2026-07-12T14:23:41.582+04:00 ^a1b2c3d4 [fact] [imp:8] User prefers concise replies.
  <!-- ntrp:meta {"recorded_at":"2026-07-12T10:23:42.014Z","sequence":42,"scope":{"kind":"user"},"sources":[{"kind":"chat_message","ref":"session-id:message-id","role":"user","occurred_at":"2026-07-12T14:23:41.582+04:00"}]} -->
```

The visible line is authoritative for occurrence time, record ID, kind, importance, entity labels, and text. The adjacent JSON comment stores only structured fields that cannot be represented safely in the readable line. It must not duplicate authoritative line fields.

User-authored lines without metadata are valid. On the next canonical write, the engine adds metadata conservatively: page frontmatter supplies scope when present; otherwise the record becomes user-scoped with `scope_origin: default`. Missing evidence is marked `source: unknown`, and date-only text remains date-precision rather than receiving an invented time.

### Identity and lifecycle

- Record IDs are stable and globally unique inside the vault.
- Page placement is presentation, not identity or scope.
- Corrections append a successor with `supersedes` references.
- Merges append one successor referencing every predecessor.
- Forget, retention, and rejection append lifecycle entries rather than silently rewriting or pruning prior history.
- Active state is derived from the append-only relationship graph.
- Direct user deletion or editing of a ledger line is authoritative because the file is canonical.

Lifecycle entries are internal ledger operations, not new user-facing memory kinds. Existing kinds such as `fact`, `directive`, `source`, `changelog`, `observation`, and `lesson` remain semantic classifications.

## Temporal Model

New records use RFC 3339 timestamps with millisecond precision and an explicit offset.

- `occurred_at`: when the remembered event or statement occurred, when known.
- `recorded_at`: when ntrp committed the record.
- source `occurred_at`: when each evidence item occurred.
- `sequence`: a monotonic local tie-breaker when timestamps are equal.
- `time_precision`: `millisecond`, `second`, `minute`, `day`, or `unknown`.

The original source offset is retained. Comparisons normalize to UTC. Calendar grouping uses the user's configured timezone, including daylight-saving transitions.

Legacy date-only records remain date-only with `time_precision: day`; migration does not assign a fake midnight. Facts with no meaningful occurrence time may have only `recorded_at`.

## Source and Evidence Model

Raw chats, tool results, and integration payloads remain in their source stores. Markdown records carry stable references to those sources rather than duplicating their full contents.

A source reference contains:

- source kind;
- stable source-specific reference;
- actor role where relevant (`user`, `assistant`, `tool`, `integration`, or `agent`);
- source occurrence timestamp and precision when available;
- capture timestamp;
- optional immutable excerpt hash for drift detection.

Curated records may contain multiple evidence references. Updates and merges union evidence instead of replacing it. Dreamed or otherwise derived records additionally cite their parent record IDs, preserving an inspectable evidence chain.

Assistant statements are never represented as user statements. Direct `remember` calls cite the tool call and, when available, the triggering user message.

## Scope and Placement

Scope is stored in record metadata as either user-wide or area-specific. The curator receives the session's area explicitly and selects scope as part of a validated operation.

Page frontmatter may provide a default scope for newly hand-authored records, but moving a page or changing its directory never mutates existing record scope. A topic page may contain records from more than one scope; retrieval applies record metadata, not path inference.

## Write Path

```text
source envelope
  -> extractor/reconciler
  -> validated record operation
  -> recoverable canonical commit
  -> derived updates
```

### Source envelope

The curator receives role-separated messages, stable message IDs, source timestamps, session ID, and area ID. Tool and integration adapters use the same envelope contract.

### Reconciliation

The reconciler emits typed `ADD`, `SUPERSEDE`, `MERGE`, `RETRACT`, or `NOOP` operations. Code validates IDs, scope, evidence, timestamps, and relationship targets before persistence. No substring heuristic decides whether contradictory memories are duplicates.

### Canonical commit

Multi-file changes use a journaled commit under `.ntrp/journal/`: prepare temporary files, validate the complete result, atomically replace targets, and write a commit marker. Startup completes or rolls back an interrupted journal before accepting new writes.

The curator watermark advances only after the canonical commit marker exists. A failed write remains retryable. Derived work never participates in this commit.

## Derived Views

Search indexes, resident profile text, synthesized page prose, health reports, and daily pages are projections. Their failure must not roll back or hide a successful canonical write. Each projection tracks a precise canonical revision, not a calendar date.

### Daily timeline

`daily/YYYY-MM-DD.md` is a user-facing chronological summary grouped in the user's local timezone. It draws from source evidence and record changes, includes exact references, and may be regenerated whenever its source revision changes.

It should preserve useful event granularity: one meaningful action or change per event. Closely related source events may be grouped by a structured model decision, but the output must retain all contributing source references. There are no keyword or regex grouping rules.

Daily summaries are not an audit log and are never used as evidence for new memory. Deleting a daily page only removes a projection; maintenance can rebuild it.

## Automatic Migration

Migration runs before the server accepts memory writes:

1. Detect the legacy ledger schema.
2. Copy the complete vault to `.ntrp/backups/<timestamp>/`.
3. Parse and validate all pages into a staged result.
4. Preserve text, IDs, kinds, labels, pins, and known dates.
5. Collapse byte-equivalent duplicate IDs; assign new IDs to conflicting duplicates and update their internal references.
6. Add conservative metadata and explicit legacy time precision.
7. Commit the staged result only after full validation.
8. Rebuild every derived projection and emit a migration report in memory health.

If staging or validation fails, the original vault remains untouched and startup reports the blocking file and reason. Migration is idempotent.

## Correctness Fixes Included

1. Round-trip complete source references instead of reconstructing fake line-ID references.
2. Persist scope independently from page placement.
3. Pass session area and role-separated content into curation.
4. Replace substring-based `remember` deduplication with typed reconciliation.
5. Advance watermarks only after canonical persistence commits.
6. Replace day-only synthesis freshness with canonical revision tracking.
7. Store consolidation fingerprints only after a successful judgment and application.
8. Preserve evidence across updates and merges.
9. Detect duplicate IDs and invalid relationship targets in health checks.
10. Make all derived work safely retryable.

## Trust UI and Tools

The memory UI and tools must expose the same canonical information:

- full source type, role, timestamp, and stable reference;
- record scope and current page placement as separate fields;
- occurrence and recording time with their precision;
- predecessor, successor, merge, and derivation relationships;
- correction, forget/retract, archive, and dispute actions;
- paginated records and real scope filtering;
- every supported kind, including `lesson`.

Selecting a source opens the exact local source when available. Missing or changed evidence is shown explicitly rather than replaced with a synthetic reference.

## Failure Invariants

- No watermark without a committed canonical write.
- No agent-authored durable claim without evidence or an explicit `source: unknown` marker.
- No scope or identity change caused by moving a file.
- No derived failure may delete or invalidate canonical knowledge.
- No consolidation cache hit may suppress retry of a failed or absent judgment.
- No migration may partially rewrite the only vault copy.
- No timestamp may claim more precision than its source provides.
- No engine process may overwrite user prose outside managed markers.

## Verification

- Parser/serializer round-trip tests for every record field and unknown metadata.
- Property tests for stable identity and scope across page moves.
- Failure injection before, during, and after journal commit and watermark update.
- Migration fixtures for duplicate IDs, conflicting IDs, missing metadata, malformed lines, and interrupted migration.
- Time tests for equal timestamps, source offsets, date-only legacy data, midnight boundaries, and daylight-saving changes.
- Reconciliation tests for duplicate, complementary, and contradictory statements.
- Same-day projection freshness and rebuild tests.
- Index tests for arbitrary nested files, existing README prose, moves, deletes, and missing descriptions.
- UI tests for exact-source navigation, lifecycle actions, kinds, scopes, and pagination.
- Existing recall evaluation plus provenance, scope, contradiction, and temporal probes.

## Implementation Boundaries

The work should be delivered in narrow phases:

1. record schema, parser, serializer, health checks, backup, and migration;
2. journaled writes, provenance, scope, reconciliation, and watermark correctness;
3. revision-based maintenance and granular daily timelines;
4. open filesystem indexes and resident root map;
5. trust UI, tools, documentation, and end-to-end evaluation.

Each phase must preserve a readable vault and finish with focused regression verification. Broader memory-product redesign, graph visualization, cloud synchronization, and copying all raw payloads into Markdown are out of scope.
