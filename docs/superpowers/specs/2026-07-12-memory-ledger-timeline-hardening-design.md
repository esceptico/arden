# Memory Ledger and Timeline Hardening Design

## Goal

Make Arden memory trustworthy as a local, file-canonical personal knowledge system. Every durable memory must preserve its identity, scope, time, and evidence while remaining understandable and editable as Markdown.

Dex is only a source of design observations. This design does not copy its schema, prompts, storage model, or implementation.

## Success Criteria

- Markdown remains the canonical knowledge store.
- Every engine-authored record has stable identity, scope, temporal metadata, and exact evidence references.
- Moving a record between pages never changes its scope or identity.
- Canonical writes are recoverable and watermarks advance only after they commit.
- User-created files and directories are allowed and discoverable through managed indexes.
- Daily Markdown provides a useful, time-granular activity view without becoming canonical evidence.
- Generated bases, search indexes, profiles, and daily summaries can be rebuilt without losing canonical records or user page-edit events.
- Existing local vaults migrate automatically with a backup and without invented timestamps.
- The desktop is a note-first notebook with fast navigation, backlinks, provenance, and editing rather than a database inspector.
- Editing a knowledge page creates a timestamped user event and an inspectable diff; ambiguous deletion never silently forgets memory.

## Approaches Considered

1. **Layered Markdown ledger — selected.** Keep human-readable pages separate from machine-owned Markdown sidecars containing durable records and edit events. Generate daily summaries as projections. This preserves notebook readability without confusing summaries with evidence.
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
  raw/
    <same path as record-backed pages>
    events/YYYY-MM-DD.md
  .arden/
    backups/
    journal/
    indexes/
    maintenance/
```

`raw/` and `.arden/` are engine-only namespaces. Users may otherwise create any files and directories. Conventional paths such as `topics/` and `daily/` are defaults, not a restriction on user organization. Engine-created pages declare their purpose in frontmatter so behavior does not depend only on their path.

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

A visible memory page contains human or synthesized prose. A record-backed page has a machine-owned Markdown ledger at `raw/<same-path>.md`. The readable record line carries the fields a human commonly needs:

```md
<!-- arden:records schema=2 page=topics/memory-engine.md -->
- 2026-07-12T14:23:41.582+04:00 ^a1b2c3d4 [fact] [imp:8] User prefers concise replies.
  <!-- arden:meta {"recorded_at":"2026-07-12T10:23:42.014Z","sequence":42,"scope":{"kind":"user"},"sources":[{"kind":"chat_message","ref":"session-id:message-id","role":"user","occurred_at":"2026-07-12T14:23:41.582+04:00"}]} -->
```

The visible line is authoritative for occurrence time, record ID, kind, importance, entity labels, and text. The adjacent JSON comment stores only structured fields that cannot be represented safely in the readable line. It must not duplicate authoritative line fields.

Raw sidecars are inspectable through the evidence UI but are not directly editable. User edits target the visible knowledge page and enter memory through the page-edit event pipeline below.

### Identity and lifecycle

- Record IDs are stable and globally unique inside the vault.
- Page placement is presentation, not identity or scope.
- Corrections append a successor with `supersedes` references.
- Merges append one successor referencing every predecessor.
- Forget, retention, and rejection append lifecycle entries rather than silently rewriting or pruning prior history.
- Active state is derived from the append-only relationship graph.
- A page edit never mutates or deletes raw records directly; reconciliation appends explicit lifecycle operations.

Lifecycle entries are internal ledger operations, not new user-facing memory kinds. Existing kinds such as `fact`, `directive`, `source`, `changelog`, `observation`, and `lesson` remain semantic classifications.

## Temporal Model

New records use RFC 3339 timestamps with millisecond precision and an explicit offset.

- `occurred_at`: when the remembered event or statement occurred, when known.
- `recorded_at`: when arden committed the record.
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

Page frontmatter may provide a default scope for record operations originating from a page edit, but moving a page or changing its directory never mutates existing record scope. A topic page may contain records from more than one scope; retrieval applies record metadata, not path inference.

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

The reconciler emits typed `ADD`, `SUPERSEDE`, `MERGE`, `RETRACT`, `NOOP`, or `ASK` operations. Code validates IDs, scope, evidence, timestamps, and relationship targets before persistence. No substring heuristic decides whether contradictory memories are duplicates.

`ASK` means the intended memory change is ambiguous and requires a user decision. It is an explicit operation, not a confidence threshold or keyword heuristic.

### Canonical commit

Multi-file changes use a journaled commit under `.arden/journal/`: prepare temporary files, validate the complete result, atomically replace targets, and write a commit marker. Startup completes or rolls back an interrupted journal before accepting new writes.

The curator watermark advances only after the canonical commit marker exists. A failed write remains retryable. Derived work never participates in this commit.

## User Page Edit Pipeline

Every visible knowledge page is editable, including synthesized topic and daily pages. Machine-only raw sidecars, health reports, and fully generated index sections remain protected. User prose outside managed index markers remains editable.

Saving requires the revision the editor started from. `Cmd/Ctrl+S` first requests a non-mutating preview containing the exact page patch and proposed memory operations. The server rejects a stale revision and returns the current page plus both revisions for review; it never overwrites a concurrent desktop, Obsidian, agent, or synthesis change.

An accepted save performs one journaled commit:

1. atomically write the visible page;
2. append a `PAGE_EDIT` event to `raw/events/YYYY-MM-DD.md`, grouped in the user's timezone, with millisecond timestamp, original offset, actor, page path, base and result revisions, and the exact patch;
3. append the previewed and user-resolved record operations, each citing the event as high-trust user evidence;
4. publish the new vault revision.

The reconciler compares the structural diff, not the full page as a new transcript:

- a new durable statement may produce `ADD`;
- a correction may produce `SUPERSEDE`;
- an explicit, unambiguous removal may produce `RETRACT`;
- formatting, reordering, and wording-only edits produce `NOOP` while remaining visible;
- an ambiguous deletion produces `ASK` and cannot retract memory until resolved.

One edit may produce multiple record operations, all committed together. An unresolved `ASK` disables Apply. If memory analysis is unavailable, the user may save the page and event with reconciliation marked pending; a retry must process the exact saved patch rather than current page contents.

The existing filesystem watcher routes external edits through the same event contract. Because the file is already changed, it appends the event and automatically applies unambiguous operations; an `ASK` appears as post-save review and cannot retract memory until resolved. The watcher stores the last observed revision so it can construct an exact patch. Engine-authored writes carry an origin marker and must not re-enter the pipeline as user edits.

## Derived Views

Search indexes, resident profile text, synthesized page prose, health reports, and daily pages are projections. Their failure must not roll back or hide a successful canonical write. Each projection tracks a precise canonical revision, not a calendar date.

### Preserving user prose

The current page is the user's visible revision. Synthesis keeps the last generated body as a rebuildable merge base under `.arden/maintenance/`. When records change, it generates a candidate and performs a three-way merge against the current page:

- non-overlapping generated changes merge automatically;
- user formatting and wording remain intact;
- overlapping changes produce a reviewable conflict and leave the current page untouched;
- if the merge base is missing, synthesis proposes a full diff instead of overwriting the page.

An accepted generated merge appends a `SYNTHESIS_MERGE` event with actor `synthesis`, retaining an exact history of what changed and why without masquerading as a user edit.

### Daily timeline

`daily/YYYY-MM-DD.md` is a user-facing chronological summary grouped in the user's local timezone. It draws from source evidence and record changes, includes exact references, and may be regenerated whenever its source revision changes.

It should preserve useful event granularity: one meaningful action or change per event. Closely related source events may be grouped by a structured model decision, but the output must retain all contributing source references. There are no keyword or regex grouping rules.

Daily summaries are not an audit log and are never used as evidence for new memory. If the user edits one, the `PAGE_EDIT` event—not the generated summary—is evidence. Deleting an untouched daily page only removes a projection; maintenance can rebuild it. A user-edited daily page is rebuilt by replaying its page events over the generated base.

## Automatic Migration

Migration runs before the server accepts memory writes:

1. Detect the legacy ledger schema.
2. Copy the complete vault to `.arden/backups/<timestamp>/`.
3. Parse and validate all visible pages and raw sidecars into a staged result.
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
11. Distinguish user, external-user, agent, and synthesis file writes so the watcher cannot create event loops.
12. Preserve user prose through revision checks and three-way synthesis merges.

## Notebook UI

The desktop memory surface is one notebook, not parallel Files and Records applications. It uses Arden's three-zone workbench shape:

```text
index rail | note workspace | optional trust inspector
```

### Index rail

- Use `index.md` and nested `README.md` descriptions as the primary meaning-based navigation.
- Keep search and a keyboard quick switcher at the top.
- Provide a collapsed Files utility for arbitrary paths; do not make the filesystem tree the product hierarchy.
- Hide `raw/`, `.arden/`, health reports, and other machine pages from normal navigation.
- Remove the top-level Files/Records toggle. A global raw-record inspector remains available through the command palette for diagnostics.

### Note workspace

- Make title, prose, headings, and wikilinks the dominant surface.
- Collapse properties to a quiet summary; move path, labels, and machine metadata to the overflow menu or footer.
- Support back/forward history and `Cmd/Ctrl+[` / `Cmd/Ctrl+]`.
- Show a hover or keyboard-focus preview for wikilinks after intent delay. Artifact list responses remain metadata-only; previews fetch and cache detail by revision.
- Use `Cmd/Ctrl+E` to enter editing and `Cmd/Ctrl+S` to open change review.
- Preserve drafts during SSE refresh. An external change creates a conflict review; it never discards typed text.

### Trust inspector

The optional right inspector contains backlinks, outgoing links, evidence, scope, lifecycle history, pending questions, and exact source navigation. Backlinks come from a rebuildable server-side link index and include context snippets.

A local graph is not part of the first release. Links and backlinks provide useful navigation without decorative graph noise.

## Trust UI and Tools

The memory UI and tools must expose the same canonical information:

- full source type, role, timestamp, and stable reference;
- record scope and current page placement as separate fields;
- occurrence and recording time with their precision;
- predecessor, successor, merge, and derivation relationships;
- correction, forget/retract, archive, and dispute actions;
- paginated records and real scope filtering;
- every supported kind, including `lesson`;
- page-edit events, revisions, actors, exact patches, and their resulting record operations.

Selecting a source opens the exact local source when available. Missing or changed evidence is shown explicitly rather than replaced with a synthetic reference.

## Change Review and Diff UI

`DiffReview` is a shared arden component used first by memory editing and then by tool approvals. It presents two coordinated views:

1. **Page diff:** rendered prose comparison by default, with a raw Markdown toggle.
2. **Memory effects:** the proposed `ADD`, `SUPERSEDE`, `RETRACT`, `NOOP`, and `ASK` operations with their evidence targets.

Wide panes use split before/after layout. Narrow panes use stacked layout. Changed lines use restrained vertical bars and word-level highlights; unchanged sections collapse into expandable hunks. Wrapping is enabled. Raw Markdown shows line numbers, frontmatter, wikilinks, and whitespace precisely.

An `ASK` annotation appears beside the affected hunk. For an ambiguous deletion, the minimum choices are `Note only` and `Forget memory`; neither is preselected. Apply is disabled until every question is resolved. Pre-save review shows the pending event; committed history shows its exact time, actor, and base/result revisions. Post-save external edits use `Resolve` rather than pretending the file has not already changed.

### Renderer boundary

The Apache-2.0 [`@pierre/diffs`](https://diffs.com/docs) package ([npm](https://www.npmjs.com/package/@pierre/diffs)) is the preferred low-level raw Markdown renderer. It is isolated behind `DiffReview`; package-specific types and styles do not leak into memory features. arden retains ownership of rendered-prose comparison, memory-effect annotations, event metadata, decisions, and actions. Adopting it does not replace the existing Markdown/code renderer elsewhere in the app.

Before adoption, a focused prototype must verify:

- Electron, React 19, Bun, and Vite production compatibility;
- dark/light arden theme adaptation across the package's Shadow DOM;
- keyboard navigation, screen-reader labels, selection, and reduced motion;
- lazy-loaded bundle and startup impact from Shiki;
- correct split/stacked responsive behavior on real Markdown pages.

The package loads only when a diff opens. Worker pools stay disabled unless profiling demonstrates a need for large files. If the prototype fails, the `DiffReview` contract remains and its low-level renderer is replaced without changing memory behavior.

## Failure Invariants

- No watermark without a committed canonical write.
- No agent-authored durable claim without evidence or an explicit `source: unknown` marker.
- No scope or identity change caused by moving a file.
- No derived failure may delete or invalidate canonical knowledge.
- No consolidation cache hit may suppress retry of a failed or absent judgment.
- No migration may partially rewrite the only vault copy.
- No timestamp may claim more precision than its source provides.
- No engine process may silently overwrite a user-authored change; it must cleanly merge or request review.
- No user page edit may be lost to an SSE refresh, stale save, synthesis pass, or external editor race.
- No engine-authored file write may be ingested again as a user page-edit event.
- No ambiguous deletion may produce `RETRACT` without a resolved `ASK` decision.
- No diff renderer may become the source of truth for memory operations; it renders server-validated revisions and operations.

## Verification

- Parser/serializer round-trip tests for every record field and unknown metadata.
- Property tests for stable identity and scope across page moves.
- Failure injection before, during, and after journal commit and watermark update.
- Migration fixtures for duplicate IDs, conflicting IDs, missing metadata, malformed lines, and interrupted migration.
- Time tests for equal timestamps, source offsets, date-only legacy data, midnight boundaries, and daylight-saving changes.
- Reconciliation tests for duplicate, complementary, and contradictory statements.
- Same-day projection freshness and rebuild tests.
- Index tests for arbitrary nested files, existing README prose, moves, deletes, and missing descriptions.
- Page-edit tests for exact patches, actor/origin classification, external edits, event-loop suppression, and atomic multi-operation reconciliation.
- Revision-conflict tests across desktop saves, Obsidian edits, agent writes, and synthesis writes.
- Three-way merge tests for clean merges, overlapping edits, missing bases, and preservation of formatting-only changes.
- UI tests for notebook navigation, history, wikilink previews, backlinks, drafts, exact-source navigation, lifecycle actions, kinds, scopes, and pagination.
- Diff-review tests for split/stacked breakpoints, raw/rendered modes, collapsed hunks, keyboard access, unresolved `ASK`, and operation application.
- Production smoke tests for the lazy `@pierre/diffs` chunk in packaged Electron.
- Existing recall evaluation plus provenance, scope, contradiction, and temporal probes.

## Implementation Boundaries

The work should be delivered in narrow phases:

1. record schema, parser, serializer, health checks, backup, and migration;
2. journaled writes, provenance, scope, reconciliation, and watermark correctness;
3. revision-based maintenance and granular daily timelines;
4. open filesystem indexes and resident root map;
5. notebook reading/navigation, backlinks, history, previews, and trust inspector;
6. page-edit events, revision conflicts, synthesis merging, `DiffReview`, correction actions, documentation, and end-to-end evaluation.

Each phase must preserve a readable vault and finish with focused regression verification. An Obsidian clone, local graph, cloud synchronization, and copying all raw payloads into Markdown are out of scope.
