# Memory (internal)

The single living description of how Arden memory works today. The substrate is
**file-canonical**: the markdown files on disk ARE the memory, not a projection
of a database. Earlier designs (claims, lenses, derivation graphs, a SQLite
record pool with a generated markdown view) are gone.

The design target is dex-quality personal memory: a small set of human-readable
markdown pages that an agent and a human can both read and trust, kept current
by background maintenance, with one flow that authors net-new cross-domain
knowledge (the dream).

## Substrate: two-zone markdown pages

Memory lives under the vault's memory root as a folder of markdown pages. Each
page has two zones separated by a sentinel comment:

1. **Synthesized prose** (above the sentinel) — LLM-written narrative with inline
   provenance cites `(record:<8hex>)`. Regenerable; never hand-authoritative.
2. **Append-only timeline** (below the sentinel `<!-- timeline ... -->`) — the
   canonical atomic records, one per line, oldest-first, never rewritten in
   place. Each line:

   ```
   - {date} ^{id} [{kind}]{tags} (src:{src}) {text}
   ```

   `^{id}` is an 8-hex record id. `[{kind}]` is the function-kind. `{tags}` are
   optional `[imp:N]` salience and `[ent:slug]` entity tags. `(src:…)` is
   provenance. Example:

   ```
   - 2026-06-21 ^a1b2c3d4 [fact] [imp:6] (src:curator) Tim rides a gravel bike.
   ```

The timeline is the source of truth. Prose is a cache: delete it and the next
synthesis pass rebuilds it from the records below.

### Record kinds (`models.py::Kind`)

Typed by **function, not subject**:

- `directive` — a rule/procedure that should steer the agent's behaviour (user-stated).
- `fact` — a durable statement about the user or their world.
- `source` — a captured receipt/reference (re-findable pointer from a tool/integration).
- `changelog` — a dated record of something that happened/changed.
- `observation` — low-trust raw integration item (gmail/slack/calendar). The dream mines these; they decay fast (90d).
- `lesson` — a continual-learning playbook item the agent **distilled** from experience (vs `directive`, which the user stated). Permanent.

Lifecycle is a single axis: a record is `superseded` by a successor (the only
active/inactive transition). Freshness is `last_confirmed_at`. `pinned` records
survive every decay pass.

### Page taxonomy

- `me.md` — the user profile; also parks sub-threshold entity records.
- `directives.md`, `lessons.md`, `references.md` — record-list pages (rendered as their timeline, not synthesized prose).
- `active-work.md` — synthesized current-work summary.
- `topics/<slug>.md` — one page per recurring subject (people, orgs, projects — entities and projects are folded into one `topics/` folder). Scope comes from frontmatter `scope_key`.
- `observations/<source>.md` — raw integration items per source.
- `insights/<month>.md` — cross-domain dream output, one page per month.
- `daily/<date>.md` — per-day synthesized log.
- `AGENTS.md` — the operating manual prepended to every maintenance LLM pass.
- `health.md` — self-audit output.

A subject only gets its own `topics/` page once it has
`MEMORY_MIN_ENTITY_RECORDS = 2` active records (per-line `[ent:slug]` tags);
below that it parks on `me.md` and promotes when it crosses the threshold
(`file_store._reconcile_entity`).

## Write path

Three writers, all appending records to a page's timeline:

1. **Curator** (`curator.py`) — the background writer. After a chat run (and on a
   periodic backstop sweep) it reads new transcript turns since a per-session
   watermark, reconciles them against existing similar records, and emits
   `ADD`/`UPDATE`/`SUPERSEDE`/`NOOP`. Dedup happens inside the LLM op choice.
   It also extracts entity tags for named subjects (including relationship facts
   — "worked at Replika" tags `Replika`) and never tags the user as an entity.
   `backfill_entity_labels` retro-tags untagged records newest-first.
2. **`remember` tool** (`tools/memory.py`) — the agent writes directly (`fact`,
   `directive`, `lesson`). The one path that bypasses curator dedup; consolidate
   cleans the overlap.
3. **Observation ingest** (`curator.store_observations`) — integrations append
   low-trust `observation` records to `observations/<source>.md` with no LLM and
   no worthiness gate (the chat gate starved breadth — only ~1/200 admitted).
   The dream mines these; retention expires them at 90d.

## Maintenance automations

Six background passes (`automation/builtins.py`). Each runs nightly on a
`TimeTrigger`; the heavier two also fire on a `CountTrigger` after conversation
bursts (`every_n = MEMORY_SYNTHESIZE_EVERY_N_RUNS = 25`, `cooldown = 30 min`) so
memory stays current within a busy day rather than only overnight. The operating
manual (`load_conventions()`) is prepended to all LLM passes.

| Pass | When | What |
| --- | --- | --- |
| `integration_sync` | 02:30 | pull integration items → observations |
| `consolidate` | 03:00 + burst | dedup/merge/supersede/retype/label-fold the timeline |
| `synthesize` | 03:30 + burst | (re)write prose zones from records + backfill |
| `retention` | 03:45 | expire records past their TTL |
| `dream` | 04:00 | author cross-domain insights |
| `suggester` | 07:00 | surface suggestions to the user |

### Consolidate (`consolidate.py`)

Demote/merge-only, never authors a fact, never raises trust. Operates over
vector neighborhoods; **fingerprint-cached** (keyed by member id/pinned/
last_confirmed_at/text) so an idle night with unchanged neighborhoods costs zero
LLM calls. Caps at `JUDGES_PER_SWEEP = 200`. Skips `observation`/`lesson` kinds.
Can MERGE near-duplicates, SUPERSEDE stale/contradicted records, RETYPE
mis-classified records, DROP orphans, and fold near-duplicate labels. Pinned
records are inviolable. Prunes tombstones against the true active pool.

### Synthesize (`synthesize.py`)

Rewrites each page's prose zone from its records, with `(record:<id>)` cites
verified against live ids. A page re-synthesizes when it is stale **or** when a
cite has gone dangling (a cited record was consolidated away). A frozen page
that can't re-synthesize (too few records left, e.g. an old daily log) instead
has its dead cites **stripped** in place (`_prune_dead_cites`) so provenance
never rots on disk. Record-list pages (`directives.md`, `lessons.md`,
`references.md`, `insights/`) render their timeline directly and are never
"synthesis pending".

### Retention (`retention.py`) — TTLs

- durable (`fact`/`changelog`): `730d`
- transient (`source`): `180d`
- observation: `90d` (`MEMORY_RETENTION_TTL_OBSERVATION_DAYS`)
- dream insights: `90d` — aligned to observations so an insight can't outlive
  the evidence it cites.

`directive`/`lesson` and anything pinned are permanent.

### Dream (`dreamer.py`) — the differentiator

The only flow that authors net-new knowledge. Generative-Agents 3-step over the
file store: (1) infer the 3 most salient questions that span MORE THAN ONE
topic, (2) retrieve cross-topic evidence per question, (3) write up to 5 cited
cross-domain insights as `src:dreamer` records into `insights/<month>.md`. The
question-seeding catalog is the durable backbone (facts/directives/sources,
excluding prior dream output so it reflects on raw memory) plus a **bounded**
slice of recent observations (`OBS_CATALOG_CAP = 40`) — the cap stops a
high-volume integration day from evicting durable facts. An insight is only
written if its cites resolve to ≥2 LIVE records spanning ≥2 different subjects
(a tombstoned cite doesn't count toward the cross-domain gate). The pass can emit
a `LEARNINGS:` trailer of operational gotchas for future runs (never ingested as
a fact).

## Read path

### Resident profile (always-on)

`profile.py::resident_profile()` renders a small char-bounded `## Profile` block
into the system prompt for both chat and automation runs. Pure file I/O, no LLM.
Directives first (so behaviour rules can't be evicted by a flood of facts):

- `DIRECTIVE_CHAR_BUDGET = 3000`
- `FACT_CHAR_BUDGET = 2000`

### Recall (pull-only)

Deeper retrieval is on demand via the **`recall` tool**: hybrid lexical (FTS,
phrase-matched on the normalized query) ⊕ semantic (vector) fused with
`rrf_merge`, then reranked by salience as a **soft** tiebreaker
(`final = rrf * (0.6 + 0.4 * salience(imp, date))`) — so an exact-but-old match
isn't buried by recency. Recall returns `[fact, source]` pairs. `recall` defaults
to `directive`+`fact`; `source`/`observation` are opt-in. `forget` searches the
same way and deletes the best hit, listing other near-matches instead of
dead-ending. Observations are excluded from system-prompt injection.

The current recall eval (`scripts/memory_eval.py`, ~20 probes) sits at ~80%.

### Browse UI / fs tools

The desktop Memory view and the agent's `memory_tree`/`memory_read`/
`memory_search` tools read the same canonical markdown files directly. There is
no separate projection to rebuild — editing a file edits memory.

## Key files

| Concern | File |
| --- | --- |
| Canonical file store / search / records | `apps/server/arden/memory/file_store.py` |
| Record + Kind + SourceRef models | `apps/server/arden/memory/models.py` |
| Page taxonomy / record-list rendering | `apps/server/arden/memory/artifacts.py`, `pages.py` |
| Scope resolution | `apps/server/arden/memory/scopes.py` |
| Background writer + observation ingest | `apps/server/arden/memory/curator.py` |
| Consolidate | `apps/server/arden/memory/consolidate.py` |
| Synthesize (prose zones + cite integrity) | `apps/server/arden/memory/synthesize.py` |
| Retention / TTLs | `apps/server/arden/memory/retention.py` |
| Cross-domain dream | `apps/server/arden/memory/dreamer.py` |
| Resident profile injection | `apps/server/arden/memory/profile.py` |
| Salience scoring | `apps/server/arden/memory/scorer.py` |
| Agent tools (remember/recall/forget + fs) | `apps/server/arden/tools/memory.py` |
| Automation wiring + triggers | `apps/server/arden/automation/builtins.py` |
| Constants (TTLs, thresholds, schedule) | `apps/server/arden/constants.py` |
| Recall eval harness | `apps/server/scripts/memory_eval.py` |
</content>
</invoke>
