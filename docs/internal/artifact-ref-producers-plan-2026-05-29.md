# artifact_ref producers — plan

Date: 2026-05-29 (updated)
Status: Gap A (capture) SHIPPED; Gap B (typing) deferred — see §4
Extends: `arden-memory-redesign-spec.md` §2.5 (`artifact_ref`), §3.3 (pattern finder)

## 0. History / what shipped

**Reverted (2026-05-29):** an earlier attempt added an `artifact_ref` pointer
param to `remember()`. Pulled back out — `remember()` is agent-invoked and
sparse, the agent doesn't naturally "bookmark," and when a tool fetches a
resource it already holds the pointer, so re-entering it via `remember` is
redundant. The artifact_ref *kind* still exists; it should be populated by the
two paths with a real firing story: Gap A capture (automatic) and a future UI
"save" action (manual, direct insert — not via the LLM tool).

**Gap A capture (2026-05-29):** tools that fetch an external resource now
self-report a `source_ref` `{kind, ref, title}` on their `ToolResult`. The run
collects these (`RunState.source_refs`, deduped), `RunCompleted` carries them,
and `ChatConnector` folds them into the episode buffer alongside the `chat_msg`
ref — so episodes now record what the turn actually touched. Emitters wired:
`read_file` (skips offloaded-result reads) and `web_fetch`. The episode-side
rollup (`buffer.source_refs_so_far` → `episode.source_refs[]`) already existed.

Retrieval is kind-agnostic: `recall()` surfaces artifact_refs via their caption
like any item; no kind change was needed.

## 1. The problem

`remember()` is agent-invoked and sparse, so on its own it will not populate
`artifact_ref` meaningfully. We need higher-volume producers — but NOT by
sprinkling `insert(kind="artifact_ref")` across every file/web/email tool.
That reproduces the noise dex explicitly warns against: "Do NOT create resources
for generic websites or one-time browsing." One-time refs are noise; refs you
keep returning to are artifacts.

## 2. The two real gaps (corrected 2026-05-29)

There are TWO gaps, not one. Earlier framing only addressed the second and got the
precondition wrong (see §4).

### Gap A — CAPTURE (the actual first piece of work)

The user constantly fetches external resources via tools (Obsidian notes, web
pages, Linear issues, files). But the chat connector (`connectors/chat.py:69-73`)
records **only the user's message text** + a `run_id` as the episode's source_ref.
Everything the agent touched — the note path, the URL, the Linear id — is
**discarded**. So there are no external pointers in memory, not because the user
isn't touching external things, but because the connector throws them away.

Fix: tools that fetch an external resource **self-report a structured ref**
`{kind, id|url|path, title}`. The episode connector collects these into the
episode's `source_refs[]` (alongside the existing chat run_id). Self-report beats
regex-parsing the message stream — reliable, and the tool already knows what it
fetched.

### Gap B — TYPING / PROMOTION

Once captured, decide which refs become typed `artifact_ref` rows. **Source-aware,
not blanket recurrence-gated** — dex's "don't bookmark one-time browsing" rule was
about *generic web pages*, not named resources:

| Tool touched | → artifact_ref? |
|---|---|
| Obsidian note / Linear issue / named file or doc | **yes, on first deliberate fetch** — it's an identifiable resource |
| Generic web search result / one-off page | recurrence-gated (only if it recurs across N episodes) |

- Structured/named refs → emit `kind="artifact_ref"` directly:
  - `artifact_ref` = the external pointer
  - `content` = caption (tool-provided title, or LLM summary)
  - `role=evidence` parent edges → the episodes that referenced it (DAG-native)
  - dedup: existing artifact_ref for that pointer → link, don't duplicate
- Generic refs → the recurrence-gated consolidation pass (sibling to pattern
  finder): promote a pointer only once it recurs across ≥ N distinct episodes.
- **Manual path (UI)**: a "save this" bookmark action, independent of the above.

## 3. SOPs (dex's `sop`) — sibling wiring, NOT a new field

Decision (2026-05-29): model "how the user uses this artifact" as a **`skill`
linked to the artifact_ref via a DAG edge**, not a new `sop`/usage column.

- The schema already allows a `skill → artifact_ref` edge (edge table has no
  kind-pair constraint). No migration needed.
- BUT nothing currently *creates* that edge — so using SOPs needs one small
  producer (or a manual UI link), same shape as the artifact_ref gap.
- Granularity rule:
  - reusable, multi-step procedure → real `skill` (file in `~/.arden/skills/` +
    lifecycle), linked to the artifact. Worth the weight.
  - throwaway one-liner ("always filter by quarter") → a `skill` node is overkill;
    ONLY this case justifies a future inline caption field on the artifact. Add it
    then, if data shows most hints are this trivial. Not now.

Naming note: do not call it `sop` (borrowed jargon). `usage`/`feedback` columns are
already taken (telemetry dicts) and must not be reused for this.

## 4. Preconditions — corrected

EARLIER (wrong): "no doc-ingesting connectors exist; wait for gmail/obsidian/web."
That misdiagnosed it. The agent ALREADY ingests these via tools (read_file, web
fetch, Linear, etc.). The real blocker is Gap A: the connector **discards** what
the tools touched. So:

1. **Gap A (capture) is the first dependency, and it's buildable now** — it does
   not require new source connectors, only tool self-reporting + connector
   collection into `source_refs`. This is the concrete next piece of work.
2. **Gap B (recurrence promotion) still needs episode volume** to tune the
   threshold — but the *source-aware direct path* (named resources → artifact_ref
   on first fetch) does NOT need recurrence data and can ship with Gap A.
3. Live DB today: 38 backfilled claims (no episodes/observations/skills), so the
   whole derivation layer is dormant regardless.

Net: Gap A + the source-aware direct typing can be built without waiting. Only the
generic-web recurrence gate needs to wait for episode volume.

## 5. Near-term vs. later

- Near-term (works today): manual `remember(artifact_ref=...)` + a UI "save"
  button. The agent is currently the only thing that sees external files/URLs
  (via read_file / web tools), and we deliberately do NOT auto-mint from those.
- Later (this plan): recurrence-gated promoter + SOP-as-skill linker, once
  doc-ingesting connectors and episode volume exist.

## 6. Open questions

- Recurrence threshold N, and the window (per scope? global?).
- Caption source: LLM summary of citing episodes vs. connector-provided title.
- Should the promoter live inside pattern_finder or be its own scheduled pass?
- SOP edge role: reuse `evidence`/`similar_to`, or add a dedicated `applies_to`?
