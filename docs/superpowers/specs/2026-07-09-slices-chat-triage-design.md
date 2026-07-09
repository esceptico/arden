# Chat → Slice/Project Auto-Triage — Design

**Goal:** When a fresh, unfiled chat has its first exchange, quietly propose a
home for it — move it into an existing slice/project, or start a new project —
without ever gating the conversation or auto-moving anything.

**Context:** Slices are life-domain buckets backed by memory topic pages; a
slice is also backed by a project (`_project_for_slice` matches the project
whose name slugs to the slice key). Sessions already carry both `slice_key`
and `project_id`, set only at creation today. A new top-level chat starts
unfiled (both null) — the "Inbox" state. This feature adds the missing
loop: after the first assistant reply, classify the chat against existing
homes and surface a one-click proposal.

This is the entropy-reduction thesis applied to conversations: the system
files your chats so you don't have to.

## Non-Goals (v1)

- **No auto-move.** Every outcome is a proposal the user accepts or dismisses.
- **Create mints a project, not a page-backed slice.** Born as the lighter
  primitive; graduates to a real slice later via the existing suggester. The
  "chat becomes the seed of a new topic page" version is a deliberate next pass.
- **Proposals are ephemeral.** No cross-restart persistence; the trigger is a
  live event, not a stored suggestion.
- **No re-triage** of already-filed chats or historical chats reopened later.
- **No batch triage** of the existing Inbox backlog.

## Architecture

Three moving parts, most of it reuse:

1. **`POST /sessions/{id}/triage`** (server, new) — loads the transcript plus
   the combined list of existing homes, makes one cheap-tier LLM call, returns
   a decision. Stateless: it classifies, it does not mutate.
2. **`POST /sessions/{id}/slice`** (server, new, small) — sets `slice_key` on
   an existing session and attaches its backing project. Mirrors the existing
   `POST /sessions/{id}/project`. This is the one real backend gap (move into a
   *slice*; move into a *project* already exists).
3. **Proposal chip** (client, new) — a quiet, dismissible chip above the
   composer. Fired once, client-side, when the first assistant turn of an
   unfiled top-level chat completes. Accept runs the corresponding action;
   dismiss silences it for the app-session.

No schema changes: `slice_key` and `project_id` already exist on the session
record and in the client `SessionListItem` type.

### Component 1 — Triage classify endpoint

**Route:** `POST /sessions/{id}/triage` → `TriageDecision`

**Request:** none (session id in path).

**Response (`TriageDecision`, Pydantic — structured output):**
```
decision:   "move" | "create" | "none"
target:     { kind: "slice" | "project", key: str, title: str } | null   # move only
new_title:  str | null                                                    # create only
rationale:  str                                                           # always
```

**Homes list.** Combine existing slices and projects into one candidate set of
`{kind, key, title}`. Dedup: a project that backs a slice (name-slug ==
slice key) is the *same* home — present it once, as the slice (richer). So
candidates = all slices + projects that do not back a slice.

**Transcript.** Load the session's messages (cap to the first N — e.g. the
opening user message + first assistant reply is enough context; a small cap
keeps the call cheap and bounded). Strip tool noise; classify on the
human-visible content.

**LLM call.** Mirror `SliceSuggester`: inject `cheap_llm` + `model`, call
`cheap_llm.completion(..., model=self.model)` with `TriageDecision` as the
response model. Prompt: *given this conversation and this list of existing
homes, pick the single best existing home if one clearly fits; otherwise
propose a short new title (2–4 words) for a new home; otherwise return none.*
Bias toward `none` — a weak or generic fit is not a fit. Empty candidate list
→ `create` (or `none` for a throwaway); never `move`.

**Failure:** any error (LLM, parse) → treat as `none`. The feature is additive;
a failed triage silently shows nothing.

### Component 2 — Move-into-slice endpoint

**Route:** `POST /sessions/{id}/slice`, body `{ slice_key: str }`.

Sets the session's `slice_key` and resolves + attaches its backing project via
the existing `_project_for_slice`. Returns the updated session summary (same
shape as the `/project` move). 404 if the session or slice is unknown.

This is symmetric with `POST /sessions/{id}/project` and is the only new
mutation. Reuse everything else:
- move → project: existing `moveSessionToProject`.
- create: existing `createProject(new_title)` then `moveSessionToProject`.

### Component 3 — Client trigger

Fires the triage call **once** per chat when **all** hold:
- an assistant turn just reached a terminal state (chat-stream.ts completion),
- it is the **first** assistant turn of this chat,
- the session is unfiled: `slice_key == null && project_id == null`,
- it is a top-level chat (not an agent/child session),
- no proposal has been shown or dismissed for this session this app-session.

Live-trigger only: reopening an old unfiled chat produces no new first-reply,
so it never re-nags, and cost is bounded to chats actively happening.

On fire: call `/triage`. `decision: "none"` → nothing. `move` / `create` →
stash the decision in client state keyed by session id and render the chip.

### Component 4 — Proposal chip UI

**Placement:** directly above the composer, inside the chat's bottom stack
(`Chat.tsx`), so it rides the existing `--chat-bottom-h` measurement and sits
in the message flow where it is discoverable.

**Idiom:** the ghost-chip idiom from `SlicesStrip` — tonal, low-weight, not a
banner, not a modal. No status dots (text + motion only, per house rule).

**Copy:**
- move → `Move to <b>{target.title}</b>?` — accept + dismiss (✕).
- create → `New: <b>{new_title}</b>?` — accept + dismiss (✕). Neutral wording
  on purpose: v1 mints a project, not a page-backed slice, so "New slice"
  would mislead.
- `rationale` rides the `title`/tooltip so accepting is informed.

**Accept:** run the branch action (move-to-slice / move-to-project /
create-then-move), animate the chip out (RISE/ROW_EXIT vocabulary), clear the
stashed decision. The existing `ChatHeader` breadcrumb then renders the new
home automatically (it already reads `slice_key`).

**Dismiss:** clear the stashed decision and mark this session dismissed for the
app-session so it never re-proposes.

## Data Flow

```
user sends first message ─▶ assistant replies ─▶ stream terminal (chat-stream)
        │
        ▼  (unfiled + first reply + top-level + not-yet-proposed)
  POST /sessions/{id}/triage ─▶ cheap LLM over transcript + homes
        │
        ├─ none    ─▶ (silent)
        └─ move/create ─▶ stash decision ─▶ chip above composer
                                 │
              ┌──────────────────┼───────────────────┐
           accept-move-slice  accept-move-project  accept-create
        POST /sessions/{id}/slice   .../project    createProject → .../project
                                 │
                          chip exits, breadcrumb shows home
                          (dismiss ✕ → silent for app-session)
```

## Error Handling

- Triage endpoint errors → client treats as `none`; no chip, no toast.
- Accept action fails → surface the existing session-error toast, keep the chip
  so the user can retry; do not lose the proposal.
- Concurrent: if the user files the chat manually (via existing UI) before
  accepting, the next render sees `slice_key`/`project_id` set and drops the
  stashed proposal.

## Testing

- **Server, `/triage`:** unit-test the classifier wrapper with a fake
  `cheap_llm` returning each decision shape; assert the homes list is built and
  deduped correctly (a slice-backing project appears once, as the slice); assert
  LLM/parse failure maps to `none`; assert empty candidates never yields `move`.
- **Server, `/sessions/{id}/slice`:** sets `slice_key` + backing project;
  404s on unknown session/slice.
- **Client trigger:** the fire predicate — asserts it fires exactly once, only
  for unfiled top-level first-replies, and never after dismiss.
- **Client chip:** renders correct copy per decision; accept calls the right
  action; dismiss silences.

## Backend Delta Summary

- **New:** `POST /sessions/{id}/triage` (classify) + a `TriageService` wrapping
  `cheap_llm` (modeled on `SliceSuggester`).
- **New (small):** `POST /sessions/{id}/slice` (set slice_key + backing project).
- **Reused:** `createProject`, `moveSessionToProject`, `ChatHeader` breadcrumb,
  ghost-chip idiom, `cheap_llm`/model wiring, structured-output pattern.
