# Research

## Surface research

- **Scope**: four independently reported desktop issues — (1) dither working-animation
  overlapping the composer border, (2) "file archival" broken from UI to functionality,
  (3) add opt-in reasoning rendering, (4) composer text placement.
- **Method**: four parallel subagent passes, one per issue, evidence-required.
- **Revision**: all `path:line` below observed at `31dff5a4`.

## Consolidated findings

### Issue 1 — dither animation overlaps the border

- The dither field is a 2D canvas, ordered dither, painted per `requestAnimationFrame`:
  `apps/desktop/src/features/chat/components/WorkingStrip.tsx:9-14` (constants),
  `:106` (`fillRect`), `:122-127` (raf loop). It is the repo's only dither renderer;
  `.dp-running-text` (`apps/desktop/src/design/base.css:495-503`) is a
  `background-clip: text` shimmer and cannot overflow.
- Rendered from exactly one call site: `apps/desktop/src/features/chat/components/Composer.tsx:325`,
  gated on `showWorking = running || serverThinking` debounced 350 ms (`Composer.tsx:139-153`).
- DOM chain: `form.board-composer` (`overflow:hidden`, `border-radius: var(--r-panel)` —
  `apps/desktop/src/design/chat.css:321-329`) → `div.board-composer__strip`
  (`height:0→1.9375rem`, `overflow:hidden`, **no `border-radius`** — `chat.css:357-361`) →
  `div.board-composer__strip-inner` (`isolation:isolate` — `chat.css:365-377`) →
  `canvas.board-composer__strip-field` (`position:absolute; inset:0; width/height:100%`,
  **no radius, no mask, no clip-path** — `chat.css:393-402`).
- **Root cause A (primary).** The composer's working "border" is an *inset* box-shadow:
  `chat.css:351-353` — `box-shadow: var(--shadow-3), inset 0 0 0 1px color-mix(… var(--accent) var(--strip-rim-strength) …)`.
  Per CSS paint order an inset shadow paints with the element's background box, i.e.
  *below* in-flow descendants. The canvas is the first child at `inset: 0`, so its top
  dot row paints straight over the 1 px ring. At `devicePixelRatio 2`: `cell=8`, `dot=5`,
  `inset = floor((8-5)/2) = 1` (`WorkingStrip.tsx:71-73`) → first dot row occupies device
  y 1–6 = CSS 0.5–3 px, across the 0–1 px ring. At `--strip-field-strength: 1`
  ("strong", `chat.css:337`) the dots are fully opaque.
- **Root cause B (corner geometry).** `--r-panel` → `--r-shell` → `--r-shell-round: 23px`
  (`base.css:398-410`). The strip is ~30 px tall, so almost its whole height sits inside
  the corner arc, yet neither the strip nor the canvas carries a radius — clipping relies
  solely on the ancestor `overflow:hidden` (`chat.css:324`), which leaves an anti-aliased
  fringe on the curve.
- `:focus-within` replaces the whole box-shadow (`chat.css:341-346`) but `.is-working`
  (`chat.css:351`) is later at equal specificity, so the ring — and the overlap — persists
  while typing.
- Prior art for clipping animated layers: `border-radius: inherit` on an `inset:0`
  overlay at `base.css:1191` (`.dp-page-skeleton`), `apps/desktop/src/design/memory.css:706`,
  `apps/desktop/src/design/settings.css:461`; derived radius for inset children at
  `memory.css:129,586,665`, `apps/desktop/src/design/foundation.css:185`, `base.css:1166`;
  animated `clip-path: inset(… round calc(…))` at `memory.css:727,743`. Tokens available:
  `--r-panel`/`--r-shell` (`base.css:398-410`), `--border-width: 1px` (`base.css:133`).

### Issue 2 — "file archival" is broken

**What the feature actually is.** There is no file-archival module. The **Archived** tab
(`apps/desktop/src/features/settings/components/SettingsModal.tsx:154-157`) stacks two
unrelated features in one component,
`apps/desktop/src/features/settings/components/ArchiveTab.tsx`:

1. A **disk-space budget + cleanup planner** (lines 181-349). Backend:
   `apps/server/arden/storage_budget.py` (inventory/plan/execute over the whole
   `~/.arden` tree), driven by `apps/server/arden/server/runtime/core.py:869-1080`,
   exposed at `apps/server/arden/server/routers/ops.py:76-107`. Config knobs:
   `apps/server/arden/config.py:224-229`, persisted via `PERSIST_KEYS` (`config.py:117-122`).
2. A **list of archived chat sessions** with Restore / Delete (lines 351-390;
   `ArchivedRow` 458-539).

"Archive" carries two meanings on one screen: archived *chat sessions*, and
`~/.arden/archive/` = *backup files* (`storage_budget.py:131-136,223`).
Session semantics: `cold_convert_session` compresses an archived session into a
restorable zstd bundle (`apps/server/arden/context/store.py:5226`,
`apps/server/arden/services/session.py:673`); `delete_cold_session` /
`delete_current_session` delete permanently (`services/session.py:751`).

**Confirmed-broken switches.**

1. *All three retention switches are preview-only inputs with no save affordance.*
   `ArchiveTab.tsx:255,263,271` — `SwitchControl onChange` only calls
   `setAllowArchived` / `setDeleteCold` / `setAllowCurrent` (local React state).
   The only persisting button, "Save & preview" (`ArchiveTab.tsx:196`), is rendered
   inside the *previous* section ("Storage budget", 181-240), above the "Retention
   policy" section (242-281) that owns the switches.
2. *Unsaved switch state is silently reverted by routine events.* The resync effect
   `ArchiveTab.tsx:70-77` reassigns every control whenever `serverConfig` identity
   changes; `apps/desktop/src/actions/server.ts:15,23` always sets a fresh object.
   Triggers: reopening Settings (`SettingsModal.tsx:216-219` calls `fetchServerConfig()`
   on every open), any SSE reconnect (`apps/desktop/src/actions/bootstrap.ts:38`),
   and tab-switching (`SettingsModal.tsx:412` conditionally renders `ArchiveTab`, so it
   unmounts and local state is destroyed).
3. *`deleteCold` has no persistence path at all.* `ArchiveTab.tsx:44` initialises it to a
   hardcoded `false` (not from `serverConfig`); `saveAndPreview` (96-103) omits it from
   the config patch; no server field exists — `config.py:224-229` and
   `apps/server/arden/server/schemas.py:623-628` have no
   `storage_allow_delete_cold_chats`. It exists only as a per-request plan parameter
   (`schemas.py:239`). It renders only when `allowArchived` is true (`ArchiveTab.tsx:258`)
   but is never reset when `allowArchived` goes false, so a stale `true` rides along in
   later plan requests (currently harmless — gated at `storage_budget.py:597`).
4. *`storage_allow_archived_cleanup` / `storage_allow_current_cleanup` are effectively
   write-only.* `core.py:936-945` reads stored config *only* when the request omits the
   field, and `ArchiveTab.tsx:106-108` always sends both explicitly. The automatic
   maintenance loop never reads them either: `run_storage_maintenance_once`
   (`core.py:869-899`) → `enforce_storage_budget`, which only acts on
   `stale_tool_result` and `expired_backup` (`storage_budget.py:472-492`).
5. *"Clean up" will 409 in practice.* `core.py:961-964` recomputes the plan and rejects
   on `plan_id` mismatch; `plan_id` hashes `before_bytes` = total bytes of the whole
   `~/.arden` tree (`storage_budget.py:660,395`), which includes the server's own rotating
   log (`apps/server/arden/logging.py:21`, counted at `storage_budget.py:167,235`) and
   `sessions.db`/`-wal` (`storage_budget.py:227`) — both change continuously while the
   server runs. The desktop surfaces the raw 409 string (`ArchiveTab.tsx:145`) and leaves
   the stale plan on screen (`setPlan(null)` only runs on success, line 140). No test
   covers this: `apps/server/tests/test_storage_runtime.py:30-79` mocks
   `_storage_cleanup_plan`; `test_storage_routes.py` uses a fake runtime.
6. *Max-space field gives no saved/dirty signal*, unlike `ContextTab.tsx:49` which
   computes `dirty` and disables save. The write itself works —
   `apps/server/arden/server/routers/settings.py:249` uses `exclude_unset=True`.

Working control, for contrast: backup **Keep / Release** (`ArchiveTab.tsx:341-343` →
`PUT /storage/backups/keep` → `core.py:1078` → `storage_budget.py:261`) persists
immediately and refetches.

**Design-system violations.** Canonical primitives:
`apps/desktop/src/features/settings/components/SettingsPage.tsx`, `.../Field.tsx`,
`apps/desktop/src/components/ui/*`, material in `apps/desktop/src/design/foundation.css`.

| Hand-rolled | file:line | Correct replacement |
| --- | --- | --- |
| `PolicyRow` (title/hint/control row) | `ArchiveTab.tsx:395-413` | `SettingsSettingRow` (`SettingsPage.tsx:100-120`); CSS already at `design/settings.css:322-357` |
| Local `NumberField` — **shadows the canonical export of the same name** | `ArchiveTab.tsx:415-454` | `NumberField` from `Field.tsx:53-88`; `SliderComfortable` idiom per `ContextTab.tsx:87-113` |
| Raw `<input type="number">` with inline border/bg/height | `ArchiveTab.tsx:186-194` | `Input` (`components/ui/Input.tsx`) wrapping `.arden-field` (`foundation.css:814-845`) |
| `RowAction` bespoke `h-6` button | `ArchiveTab.tsx:541-576` | `IconButton` (`size="xs"`) or `ConfirmDeleteButton` |
| Inline `<p role="alert" class="…text-bad">` | `ArchiveTab.tsx:238` | `SettingsInlineError` (`SettingsNotice.tsx:19-32`), per `ContextTab.tsx:115` |
| Bare `div.settings-empty-note` empty state | `ArchiveTab.tsx:372-377` | `EmptyState` (`components/ui/EmptyState.tsx:17`) or `SettingsDataSection`'s `empty` prop (`SettingsPage.tsx:122-143`) |
| Category rows: flex + `border-b border-line-soft` per item | `ArchiveTab.tsx:200-228` | `SettingsSurface` + `SettingsSettingRow` / `SettingsSummary` (`SettingsPage.tsx:145`) |
| Action bar `div.mt-3.flex.justify-end` | `ArchiveTab.tsx:314` | `.settings-field-actions` (`design/settings.css:238`) |
| Backup list `<ul class="divide-y divide-line-soft">` | `ArchiveTab.tsx:331-346` | `SettingsDataSection` + `SettingsSettingRow` |
| Tooltip wrapping a `<button>` with no `onClick` | `ArchiveTab.tsx:206-214` | `IconButton` or non-interactive `HoverPopover` trigger |
| Search toolbar outside any `SettingsSection` | `ArchiveTab.tsx:352-363` | move into the Sessions `SettingsSection` via its `action` prop (`SettingsPage.tsx:74,89`) |

5 of the 8 sections in the file already use `SettingsSection`, which is why the tab reads
as inconsistent rather than uniformly off-system.

**Clarity gaps.**

1. Tab intro copy (`SettingsModal.tsx:157`) promises archived-session semantics; the first
   three sections are a global disk-quota planner over tool-result blobs, search indexes,
   logs and backups. The archived-session list is last, below the fold.
2. "Archive" means both backup files and archived chats on the same screen, undistinguished.
3. Switch copy reads as standing policy ("Convert old archived chats to … bundles before
   permanent deletion is considered", `ArchiveTab.tsx:253`) but nothing recurring consults it.
4. "Save & preview" conflates two verbs: it persists config *and* computes a dry run.
5. "Maximum Arden space" implies a hard quota; automatic reclaim covers only orphaned
   tool-result blobs and expired backups (`storage_budget.py:472-492`).
6. Dead code: `list_kept_backups` (`storage_budget.py:257`) has no callers;
   `maintainStorageApi` (`apps/desktop/src/api/settings.ts:407`) is exported and unused,
   so `POST /storage/maintain` is unreachable from the UI.

### Issue 3 — opt-in reasoning rendering

**The pipeline already exists end to end; only the opt-in gate is missing.** It existed
and was deliberately removed in commit `d887d16c` "Hide reasoning in desktop chat"
(2026-05-20), which hardcoded reasoning to hidden. `ReasoningMessage.tsx` is currently
dead code.

- *LLM layer — complete.* Anthropic `thinking` param at
  `apps/server/arden/llm/anthropic.py:225` (builder `:235`, adaptive/budget branch `:238`,
  `output_config` `:251`), `thinking_delta` → `ReasoningContentDelta` at `:169-170`,
  non-stream block at `:455-456`. OpenAI Responses `reasoning` at
  `apps/server/arden/llm/openai_responses.py:97` (encrypted CoT include `:29`, deltas
  `:225-226`, replay `:571-572`); chat-completions `reasoning_effort` at
  `apps/server/arden/llm/openai.py:163-176`, streamed `reasoning_content` `:292-293`.
  Per-model config: `apps/server/arden/llm/models.py:65` (`reasoning_efforts`), values
  `:120-259`, custom models `:456`. User config: `config.py:209`
  (`model_reasoning_efforts`), resolver `:408-425`, per-role override `:96`.
  Anthropic emits thinking **only when `reasoning_effort` is set** — `_thinking_config`
  returns `None` if `effort is None` (`anthropic.py:236-237`); the user already sets it
  via the composer chip (`ComposerToolbar.tsx:72`) and Settings → Models
  (`ModelsTab.tsx:123-148`).
- *Agent loop + events — complete.* `apps/server/arden/agent/agent.py:602-618`
  (`ReasoningContentDelta` → `ReasoningStarted`/`ReasoningDelta`), `:719-722`
  (`ReasoningEnded` / `ReasoningBlock`). Types at
  `apps/server/arden/agent/types/events.py:49`. SSE conversion at
  `apps/server/arden/events/sse.py:734-756` (five event types). Subagent forwarding
  allowlist `apps/server/arden/core/spawner.py:122`. Model context preservation:
  `apps/server/arden/agent/llm/parsing.py:34-39` keeps `reasoning_content`,
  `reasoning_encrypted_content` and full `anthropic_content` (signed thinking blocks).
- *Persistence — complete, with an intentional delta gap.* Reasoning persists on the
  assistant message row and is re-served by history
  (`apps/server/arden/server/routers/session.py:445-446`).
  `REASONING_MESSAGE_CONTENT` is deliberately not durably persisted
  (`events/sse.py:59-65` `EPHEMERAL_EVENT_TYPES`; rationale `chat.py:110-118,189-196`);
  the text is re-derived from `reasoning_content` on history load
  (`apps/desktop/src/stores/transcript-projection.ts:582-591`).
- *Renderer — exists but unreachable.* Discriminator
  `apps/desktop/src/stores/types.ts:47` (`| "reasoning"`); dispatch
  `apps/desktop/src/features/chat/components/Message.tsx:34`; renderer
  `.../ReasoningMessage.tsx` (collapsible, Brain01 + chevron, `Reveal` body,
  smooth-stream on expand, `is-streaming` head), CSS `design/chat.css:186-205`.
  Live ingest `transcript-projection.ts:262-293`; history ingest `:582-591`; SSE types
  `apps/desktop/src/api/events.ts:138-143`.
- **The single blocker:** `apps/desktop/src/lib/messageVisibility.ts:16-26` sets
  `hiddenInTranscript: true` for every reasoning message, so `Messages.tsx:176`
  (`visibleMessageIds`) filters it out and `Message.tsx:34` never fires.
- *Not a `tool_presentation` concern.* `apps/server/arden/agent/types/tool_presentation.py:1-8`
  maps tool names to icon/noun grouping hints for the activity timeline; reasoning never
  passes through it (`sse.py:749` emits `ReasoningMessageContentEvent` with no
  presentation fields, unlike `ToolInputStarted` at `sse.py:757-770`). Reasoning is
  already its own message role with its own collapsible renderer; `TurnGroup.tsx:61-65`
  explicitly reasons about "a turn with just reasoning + a final reply".

**Insertion points (client-only; zero backend work).**

| # | File:line | Change |
| --- | --- | --- |
| 1 | `apps/desktop/src/stores/types.ts:125` | add `showReasoning: boolean` to `Prefs`, next to `fontSmoothing` |
| 2 | `apps/desktop/src/stores/prefs.ts:46` | add `showReasoning: false` to `DEFAULT_PREFS` |
| 3 | `apps/desktop/src/lib/messageVisibility.ts:16-26` | gate **only** `hiddenInTranscript`; keep `isReasoning` inside `isContinuation` for `breaksActivity` (depended on by `stores/index.ts:217`, `transcript-projection-helpers.ts:65,341`, `session-cache.ts:169`) |
| 4 | `apps/desktop/src/features/chat/components/Messages.tsx:174-178` | read the pref, pass to `visibleMessageIds`, extend the `useMemo` deps; mirror at `apps/desktop/src/features/sources/lib/sourceInspector.ts:54` |
| 5 | `apps/desktop/src/features/settings/components/AppearanceTab.tsx:173-183` | `SettingsSettingRow` + `SwitchControl`; the "Font smoothing" row is a byte-for-byte template (selector `:62`, writer `:179`) |

Also `apps/desktop/tests/messageVisibility.test.ts:4-9` asserts the current unconditional
hiding and needs the new param. No `PREFS_VERSION` bump needed (`prefs.ts:24`, currently
13) — a new key falls through via `{ ...DEFAULT_PREFS, ...parsed }` at `prefs.ts:116`.
Avoid the legacy name `showReasoningInChat`, actively deleted at `prefs.ts:53,85`.
Template to mirror: **`fontSmoothing`** — `types.ts:125`, `prefs.ts:46`,
`AppearanceTab.tsx:173-183`, consumer `apps/desktop/src/lib/typography.ts:27-40`.

### Issue 4 — composer text placement

**Component tree.**

| Element | file:line | Notes |
| --- | --- | --- |
| `div.board-composer-stack` | `Composer.tsx:281` | `padding: 0 var(--content-gutter) var(--sidebar-edge)` (`chat.css:315`) |
| `div.board-composer-wrap … max-w-[760px] mx-auto` | `Composer.tsx:290` | `container-type: inline-size` (`chat.css:319`) |
| `form.board-composer.surface-panel` | `Composer.tsx:317-321` | `overflow:hidden`, `border-radius: var(--r-panel)`, `background: var(--surface-3)` (`chat.css:321-329`) |
| `WorkingStrip` → `.board-composer__strip` | `Composer.tsx:325`, `chat.css:357-389` | inner padding `0 var(--space-4)` = 16 px |
| `ComposerEditingBanner` | `ComposerEditingBanner.tsx:15` | `px-3 py-1.5` = 12 px |
| `ComposerImageStrip` | `ComposerImageStrip.tsx:22` | `px-3 pt-2` = 12 px |
| `div.board-composer__input-row` | `Composer.tsx:352` | `flex min-h-14 items-start gap-2 px-4 pt-2 pb-1` |
| the input — `div[contenteditable][role=textbox]` | `Composer.tsx:353-511` | **not** a textarea; `min-h-[44px] max-h-[220px] … p-0` |
| `ComposerToolbar` | `ComposerToolbar.tsx:40` | `flex items-center gap-1.5 px-2 py-1` = 8 px |

The input row has exactly one child — the hidden `<input type=file>` is a sibling outside
it (`Composer.tsx:341-351`) — so the row's `gap-2` is inert.

**Metrics.**

| Property | Value | Source |
| --- | --- | --- |
| font-size | `var(--text-body)` → 14 px | `Composer.tsx:510`; `chat.css:443`; `base.css:180,168,156` |
| line-height | `var(--leading-body)` = 1.43 → 20.02 px | `Composer.tsx:510`; `chat.css:444`; `base.css:203` |
| input padding | `p-0` (all sides 0) | `Composer.tsx:510` |
| input min-height | **44 px**, hardcoded, matches no token | `Composer.tsx:510` |
| input max-height | 220 px, hardcoded | `Composer.tsx:510` |
| auto-grow JS | none — natural contenteditable growth | — |
| row min-height | **56 px**, declared twice (`min-h-14` and `min-height:3.5rem`) | `Composer.tsx:352`; `chat.css:431` |
| row padding-top / bottom | **8 px / 4 px** (asymmetric) | `Composer.tsx:352` |
| row padding-inline | 16 px | `Composer.tsx:352` |
| toolbar | 40 px tall, `py-1`/`px-2`, controls at `--control-size-large` = 30 px | `chat.css:503-512` |
| send button | **28 px** (`--icon-button-size`), not 30 px | `chat.css:519-522`; `base.css:248` |

Placeholder is **not** the problem: `chat.css:463-469` pins `[data-empty="true"]::before`
at `inset:0` on a `position:relative` editor (`chat.css:457`) inheriting the same
14 px / 1.43 metrics with zero padding, so its first line box coincides exactly with the
typed text's.

**Diagnosis.**

1. *`min-h-[44px]` on the editor + `items-start` on the row* (`Composer.tsx:510`,
   `Composer.tsx:352`) — dominant cause. Row content box = 56 − 8 − 4 = 44 px, the same
   constraint as the editor's `min-height`, so they lock. The 20.02 px line box is
   top-pinned: text occupies y 8→28 within a 56 px row, leaving 8 px above and 28 px
   below — roughly **10 px above optical center**. 44 px is 2.2× the line box and matches
   no token (control family is 28/30 px, row family 27 px `--interactive-row-height`).
2. *Asymmetric `pt-2` / `pb-1`* (`Composer.tsx:352`) pushes further in the same direction.
3. *`px-4` (16 px) against a 28 px lane gutter.* Message lane text left edge =
   `center − 352` (`chat.css:80-83`, `Messages.tsx:199`, `--content-gutter` =
   `clamp(1rem, 2.5vw, 1.75rem)` → 28 px, `base.css:273`); composer text left edge =
   `center − 364`. **Draft text starts 12 px left of every message body**, and visibly
   jumps 12 px right on send. Inside the panel there are four different left insets —
   toolbar 8, banner 12, image strip 12, input row 16, working strip 16 — while the repo's
   settled inner-row inset token `--interactive-row-inline-padding` (= `--space-3` = 12 px,
   `base.css:131`) is used by none of them.

**Secondary cleanups surfaced.**

- Duplicated `min-height` (`chat.css:431` vs `min-h-14`) and duplicated font-size /
  line-height (`chat.css:443-444` vs `Composer.tsx:510`).
- Dead `.board-composer__input::placeholder` rule (`chat.css:453`) — can never match a
  contenteditable.
- `--composer-max-input-height: 10rem` declared at `base.css:355`, consumed nowhere;
  the composer hardcodes `max-h-[220px]`.
- Send button 28 px vs the 30 px control family it sits in (`chat.css:519-522`;
  `chat.css:510-512` already fixes exactly this class of bug for the attach button).

## Conflicts and gaps

- Issue 4 point 3 has two mutually exclusive targets: align the composer's inner insets
  to the panel's own axis (12 px `--interactive-row-inline-padding`), or align the draft
  text to the transcript's text axis (28 px `--content-gutter`). Cannot satisfy both.
- Issue 2 is not one bug but a scope question: functional repair of the existing tab vs
  splitting the two conflated features apart. Recorded as an open question in README.
- Issue 2 finding 5 (409 on Clean up) is derived from reading the hash inputs, not from a
  reproduced failure. Needs runtime confirmation before it is treated as fixed.

## Supporting material

- Subagent passes: dither (Explore), archival (general-purpose), reasoning
  (general-purpose), composer (Explore). Raw transcripts not retained; all material
  claims consolidated above with `path:line`.
