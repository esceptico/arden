# Contextual Automation Suggestions

**Date:** 2026-06-02
**Status:** Approved design, pre-implementation

## Problem

The Automations modal has a **Templates** tab backed by a static, hardcoded list of 5
starter automations ([`apps/desktop/src/components/automations/templates.ts`](../../../apps/desktop/src/components/automations/templates.ts)).
They are generic and identical for every user. We want **contextual** suggestions —
ready-made automations derived from the user's own memory, chats, and actions — so the
tab proposes automations that actually fit how this user works.

The static templates are not a field on the `Automation` model; they are a frontend-only
UI affordance. A recently added guard test
([`apps/desktop/tests/automationTemplates.test.ts`](../../../apps/desktop/tests/automationTemplates.test.ts))
bans `TEMPLATE_SIGNALS` / `suggestTemplatesForContext` / `RegExp` in `templates.ts` —
i.e. keyword/regex heuristics on the client are explicitly out of bounds. Contextual
generation must be server-side and grounded in structured signal.

## Decisions (resolved with the user)

1. **Augment, not replace.** A "Suggested for you" section sits **above** the static
   templates in the Templates tab. The static 5 remain as a reliable fallback (cold-start).
2. **Ready-made automations.** Each suggestion is a complete automation — name, prompt,
   schedule, and a one-line *"why this fits you"* rationale — one click to create or tweak.
3. **LLM synthesis.** A cheap LLM reads retrieved memory + recent chats/actions and drafts
   tailored automations.
4. **Background precompute.** A daily builtin handler (like the existing `knowledge`/
   pattern-finder handlers) refreshes suggestions and caches them, so opening the tab is
   instant.

### Judgment calls (approved)

- Suggestions live in the **automation store** (new table), not the memory store — they are
  automation-shaped.
- **LLM dedup via prompt exclusions** (feed existing + dismissed/accepted signatures into the
  prompt) rather than brittle key-matching.
- Include a **manual `refresh` endpoint** for a Refresh affordance and tests, even though the
  primary cadence is background.
- **Scope = USER** (one global suggestion set) for v1, with project-awareness baked into the
  prompt. Not per-project sets.

## Existing patterns this builds on

- **Builtin reflector precedent:** [`automation/builtins.py`](../../../apps/server/arden/automation/builtins.py)
  defines `BUILTINS: list[BuiltinSpec]` and `seed_builtins(store)`. The scheduler registers
  handler callables in [`server/runtime/automation.py`](../../../apps/server/arden/server/runtime/automation.py)
  `start_scheduler()` via `scheduler.register_handler(name, callable)`. Current builtins:
  `pattern_finder_daily` (04:00) and `skill_inducer_daily` (06:00). `skill_inducer_daily`
  already reflects over memory to draft *skill* proposals — the direct analogue for drafting
  *automation* proposals.
- **Handler shape:** `async def handler(context: dict | None) -> str | None`, returning a
  short summary string for `last_result`. Dispatched by
  [`automation/scheduler.py`](../../../apps/server/arden/automation/scheduler.py)
  `_run_handler` via `self._handlers.get(automation.handler)`.
- **Structured LLM output:** `await cheap_llm.completion(..., response_format=PydanticModel)`,
  parsed like [`memory/pipeline/retrieve.py`](../../../apps/server/arden/memory/pipeline/retrieve.py)
  `CompressionResult` (`_parse_compression`). `cheap_llm = get_completion_client(config.memory_model)`
  (see [`server/runtime/knowledge.py`](../../../apps/server/arden/server/runtime/knowledge.py)).
- **Memory retrieval/queries:** `MemoryStore.distinct_subjects(scope)`, `query(scope, status, limit)`,
  `lens_registry.list()`, and `Retriever.retrieve(Retrieval(...))`.
- **Editor seed path (reused as-is):** clicking a template calls
  `setEditor({ kind: "create", preset })` where `preset` is a `CreateAutomationPayload`;
  `formFromPreset` hydrates the form. Suggestions convert to the same `CreateAutomationPayload`.
- **Tab classification:** `isInternalAutomation` keys off `automation.builtin` first, so the
  new builtin lands in the **System** tab automatically — no `automationFilters.ts` change.

## Architecture & data flow

```
daily builtin  automation_suggester_daily (~07:00)
      │
      ▼
AutomationSuggester.run()
  gather signal (memory + chats + actions + exclusions)
  → cheap_llm.completion(response_format=SuggestionSet)
  → validate (schedule maps to a supported trigger) + cap N
      │
      ▼
automation_suggestions table  (replace active set)
      │
      ▼
emit SSE  automation_suggestions_updated
      │
      ▼
desktop  GET /automations/suggestions   (on modal open + on SSE)
      │
      ▼
"Suggested for you" cards  →  click → setEditor({kind:"create", preset})
      │
      ▼
POST /automations  (from_suggestion_id) → suggestion flips to "accepted"
```

## Data model

### Server: `AutomationSuggestion` dataclass (`automation/suggestions.py`)

```
id: str                     # uuid
name: str
description: str            # the automation prompt
triggers: list[Trigger]     # validated, supported trigger types only
rationale: str              # one-line "why this fits you"
evidence: list[str]         # optional short grounding notes / source hints
category: str               # for grouping in the UI (e.g. "Status reports")
icon: str | None            # lucide icon name hint, optional
status: Literal["active", "dismissed", "accepted"]
created_at: datetime
source_automation_id: str | None   # set when accepted → created automation
```

### Server: `automation_suggestions` table (store migration v11)

```
id TEXT PRIMARY KEY
name TEXT NOT NULL
description TEXT NOT NULL
triggers TEXT NOT NULL              -- JSON array, same shape as scheduled_tasks.triggers
rationale TEXT NOT NULL
evidence TEXT                       -- JSON array of strings, nullable
category TEXT NOT NULL
icon TEXT                           -- nullable
status TEXT NOT NULL DEFAULT 'active'
created_at TEXT NOT NULL
source_automation_id TEXT           -- nullable
```

Index: `(status, created_at)`. Migration is additive (new table only); no change to
`scheduled_tasks`.

CRUD on `AutomationStore`:
- `replace_active_suggestions(items: list[AutomationSuggestion])` — delete all `active`
  rows, insert the new set in one transaction. Leaves `dismissed`/`accepted` rows intact.
- `list_active_suggestions() -> list[AutomationSuggestion]`
- `mark_suggestion_dismissed(id)`
- `mark_suggestion_accepted(id, source_automation_id)`
- `list_excluded_signatures() -> list[str]` — names/descriptions of `dismissed` +
  `accepted` suggestions, for the dedup prompt.

### Server: Pydantic synthesis schema (`response_format`)

```
class SuggestionDraft(BaseModel):
    name: str
    prompt: str
    schedule: ScheduleDraft        # trigger_type + at/days/every/event_type/lead_minutes
    rationale: str
    evidence: list[str] = []
    category: str
    icon: str | None = None

class SuggestionSet(BaseModel):
    suggestions: list[SuggestionDraft]
```

`ScheduleDraft` mirrors the supported subset of `CreateAutomationRequest` trigger fields.
Validation rejects drafts whose schedule does not map to a `TimeTrigger` (`at`+`days` or
`every`) or an event trigger (`event_type` [+ `lead_minutes`]); rejected drafts are dropped
and logged (no silent bad data). Final set capped (e.g. `MAX_SUGGESTIONS = 6`).

### Desktop: `AutomationSuggestion` (api.ts)

```
interface AutomationSuggestion {
  id: string
  name: string
  description: string
  triggers: AutomationTrigger[]
  rationale: string
  evidence: string[]
  category: string
  icon: string | null
}
```

A `suggestionToPayload(s)` helper builds a `CreateAutomationPayload` (name, description,
triggers) for the editor seed.

## Suggester service (`automation/suggestions.py`)

`AutomationSuggester(memory, sessions, automations, cheap_llm, model)`:

1. **Gather** (each step token-bounded, all best-effort / degrade-empty):
   - Memory (USER scope, plus active projects): `distinct_subjects` (top N), recent active
     claims via `query(...)`, active lenses via `lens_registry.list()`.
   - Chats/actions: recent session names + `session_goals`; recurring external `tool_calls`
     (email/calendar/web); recently created/run automations.
   - Exclusions: `automations.list_all()` (existing) + `list_excluded_signatures()`.
2. **Synthesize:** build the system prompt (new entry in
   [`automation/prompts.py`](../../../apps/server/arden/automation/prompts.py)) describing what
   the user works on, recent activity, existing automations, and the exclusion list; instruct
   the model to propose up to N **new** automations grounded in the evidence. Call
   `cheap_llm.completion(response_format=SuggestionSet)`.
3. **Validate + dedupe:** drop invalid schedules; cap to `MAX_SUGGESTIONS`.
4. **Persist:** `replace_active_suggestions(...)`; emit `automation_suggestions_updated`.
5. **Return** a summary string: `f"suggestions={n}; dropped={d}"` for `last_result`.

If memory is unavailable or there is no signal, produce **zero** suggestions (the UI hides
the section and the static templates remain).

## Background handler & builtin

- New constant `BUILTIN_AUTOMATION_SUGGESTER_DAILY_ID` in `constants.py`.
- New `BuiltinSpec` in `BUILTINS`: `handler="automation_suggester_daily"`,
  `TimeTrigger(at="07:00", days="daily")`, `auto_approve=True`. Runs after pattern_finder
  (04:00) and skill_inducer (06:00) so it can leverage fresh observations/claims.
- `server/runtime/automation.py` `start_scheduler()`: register
  `"automation_suggester_daily"` → `_build_automation_suggester_handler()`, which constructs
  the `AutomationSuggester` from the runtime's memory/sessions/automations stores and
  `cheap_llm`, then calls `.run()`.

## API (`server/routers/automation.py` + `schemas.py`)

- `GET /automations/suggestions` → `{ suggestions: AutomationSuggestionResponse[] }`
  (reads `list_active_suggestions`; instant, no compute).
- `POST /automations/suggestions/{id}/dismiss` → `mark_suggestion_dismissed`; 204.
- `POST /automations/suggestions/refresh` → runs the suggester now (await `.run()`); returns
  the refreshed list. For a Refresh affordance + tests.
- Extend `CreateAutomationRequest` with optional `from_suggestion_id`. On create, if present,
  call `mark_suggestion_accepted(id, new_task_id)` so the suggestion drops out of the active
  set and feeds future dedup.

## SSE

New event type `automation_suggestions_updated` (in the automation/SSE event enum), emitted
after each successful refresh (background or manual). The desktop subscribes and re-fetches.
Follows the existing automation event-emission path used by the scheduler.

## Desktop

- `api.ts`: `AutomationSuggestion` type, `suggestionToPayload`, and
  `listAutomationSuggestionsApi`, `dismissAutomationSuggestionApi`,
  `refreshAutomationSuggestionsApi`. Thread `from_suggestion_id` through
  `createAutomationApi` / `CreateAutomationPayload`.
- `actions/automations.ts` + store: `automationSuggestions` state;
  `fetchAutomationSuggestions`, `dismissSuggestion`, `refreshSuggestions`.
- `AutomationsModal.tsx`: in the Templates tab, render a **"Suggested for you"** section
  above `templatesByCategory()`. Fetch suggestions on modal open (next to `fetchAutomations`)
  and on the `automation_suggestions_updated` SSE event. Each suggestion renders via a card
  (extend/reuse `TemplateCard`) showing name, rationale, a schedule chip, and a dismiss `×`.
  Click → `setEditor({ kind: "create", preset: suggestionToPayload(s) })`. When that create
  succeeds, pass `from_suggestion_id`. The suggestion's `icon` string is mapped to a Lucide
  icon via a small name→component lookup, falling back to a default (e.g. `Sparkles`) for
  unknown/null names — so `TemplateCard`'s `LucideIcon` prop is always satisfied.
- `templates.ts` stays **100% static** → the guard test stays green.

## Cold-start / empty states

No active suggestions → the "Suggested for you" section is not rendered → the static 5
templates are the whole tab. New users see exactly today's behavior until signal accrues.

## Testing

**Server**
- `AutomationSuggester`: gather→synth with a stubbed `cheap_llm`; validation drops invalid
  schedules; dedup excludes existing automations + dismissed/accepted signatures; empty
  signal → zero suggestions.
- Store: v11 migration applies cleanly; `replace_active_suggestions` only touches `active`
  rows; dismiss/accept transitions; `list_excluded_signatures`.
- Router: list / dismiss / refresh; `from_suggestion_id` flips status on create.

**Desktop**
- Suggestions render in the Templates tab; dismiss removes a card and calls the API; click
  seeds the editor with the right payload; cold-start (empty list) hides the section.
- Keep `automationTemplates.test.ts` (static guard) green.

## Out of scope (v1)

- Per-project suggestion sets.
- Surfacing structured `evidence` as an expandable "see why" (kept in the model, shown only
  as the rationale line for now).
- Learning a relevance model from accept/dismiss rates (the signatures feed dedup, not ranking).

## Build sequence

Two largely independent tracks (good fit for a parallel workflow):

1. **Server track** — constants + `automation_suggestions` table/migration/CRUD →
   `suggestions.py` (dataclass + Pydantic + service) → prompt → builtin + handler wiring →
   SSE event → router endpoints + `from_suggestion_id` → server tests.
2. **Desktop track** — `api.ts` types/clients → store + actions → `AutomationsModal`
   "Suggested for you" section + suggestion card → SSE refresh → desktop tests.

Integration point: the `GET /automations/suggestions` response shape and the SSE event name
are the contract between tracks.
