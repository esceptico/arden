# Contextual Automation Suggestions — Implementation Plan

> **For agentic workers:** This plan is executed via a parallel **Workflow** (server + desktop tracks). Steps use checkbox (`- [ ]`) syntax. Spec: [`docs/superpowers/specs/2026-06-02-contextual-automation-suggestions-design.md`](../specs/2026-06-02-contextual-automation-suggestions-design.md).

**Goal:** Add a "Suggested for you" section to the Automations Templates tab, backed by LLM-synthesized, background-precomputed automation suggestions grounded in the user's memory/chats/actions.

**Architecture:** A daily builtin handler (`automation_suggester_daily`) runs `AutomationSuggester`, which gathers memory/chat/action signal, calls a cheap LLM for structured drafts, validates schedules via the existing `build_trigger`, and stores an active suggestion set. The desktop fetches it (`GET /automations/suggestions`) on modal open + on an SSE event and renders cards that seed the existing automation editor.

**Tech Stack:** Python/FastAPI + aiosqlite (server), TypeScript/React (desktop). Cheap-LLM via `cheap_llm.completion(response_format=PydanticModel)`.

**House rules (all agents):** No defensive code for impossible states. Imports at top. Dataclasses over inheritance. Minimal docstrings. No fallbacks/back-compat hacks. **Do NOT git commit** — leave changes staged in the working tree for user review.

---

## Shared contract (server ⇄ desktop)

**`GET /automations/suggestions` → `200`**
```json
{ "suggestions": [
  { "id": "uuid", "name": "Weekly arden PR digest", "description": "Summarize merged PRs in arden this week...",
    "triggers": [ { "type": "time", "at": "09:00", "days": "mon" } ],
    "rationale": "You review arden PRs most mornings", "evidence": ["..."],
    "category": "Status reports", "icon": "GitPullRequest" } ] }
```
**`POST /automations/suggestions/{id}/dismiss` → `204`**
**`POST /automations/suggestions/refresh` → `200`** (same body as GET; recomputes synchronously)
**`POST /automations`** accepts optional `from_suggestion_id: string` → on success marks that suggestion `accepted`.
**SSE event** on the `automation:events` bus: `EventType` value `"automation_suggestions_updated"` (no payload fields needed beyond base).

Trigger object shape inside `triggers[]` matches `scheduled_tasks.triggers` JSON: `{type:"time", at?, days?, every?, start?, end?}` or `{type:"event", event_type, lead_minutes?}`.

---

# SERVER TRACK

### Task S1: Data layer (constants, migration v11, store CRUD, models)

**Files:**
- Modify: `apps/server/arden/constants.py`
- Modify: `apps/server/arden/automation/store.py` (migration tail after `_set_schema_version(conn, 10)` ~L967; `automation_meta` versioning)
- Create: `apps/server/arden/automation/suggestions.py` (dataclass + Pydantic only in this task)
- Test: `apps/server/tests/automation/test_suggestions_store.py`

- [ ] **S1.1 Constants.** Add:
  ```python
  BUILTIN_AUTOMATION_SUGGESTER_DAILY_ID = "builtin-automation-suggester-daily"
  AUTOMATION_SUGGESTER_DAILY_AT = "07:00"
  MAX_AUTOMATION_SUGGESTIONS = 6
  ```

- [ ] **S1.2 Models** in `suggestions.py`:
  ```python
  from dataclasses import dataclass, field
  from datetime import datetime
  from typing import Literal
  from pydantic import BaseModel
  from arden.automation.triggers import Trigger

  SuggestionStatus = Literal["active", "dismissed", "accepted"]

  @dataclass
  class AutomationSuggestion:
      id: str
      name: str
      description: str
      triggers: list[Trigger]
      rationale: str
      category: str
      evidence: list[str] = field(default_factory=list)
      icon: str | None = None
      status: SuggestionStatus = "active"
      created_at: datetime | None = None
      source_automation_id: str | None = None

  class ScheduleDraft(BaseModel):
      trigger_type: Literal["time", "event"]
      at: str | None = None
      days: str | None = None
      every: str | None = None
      event_type: str | None = None
      lead_minutes: int | None = None

  class SuggestionDraft(BaseModel):
      name: str
      prompt: str
      schedule: ScheduleDraft
      rationale: str
      category: str
      evidence: list[str] = []
      icon: str | None = None

  class SuggestionSet(BaseModel):
      suggestions: list[SuggestionDraft]
  ```

- [ ] **S1.3 Migration v11** — append to `_migrate()` after the v10 block, mirroring the existing `if version < N:` style and `_serialize_triggers`/JSON conventions already in `store.py`:
  ```sql
  CREATE TABLE IF NOT EXISTS automation_suggestions (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      description TEXT NOT NULL,
      triggers TEXT NOT NULL,
      rationale TEXT NOT NULL,
      evidence TEXT,
      category TEXT NOT NULL,
      icon TEXT,
      status TEXT NOT NULL DEFAULT 'active',
      created_at TEXT NOT NULL,
      source_automation_id TEXT
  );
  CREATE INDEX IF NOT EXISTS idx_suggestions_status ON automation_suggestions(status, created_at);
  ```
  Then `await _set_schema_version(conn, 11)`.

- [ ] **S1.4 Store CRUD** on `AutomationStore` (reuse its existing `_serialize_triggers`/`parse_triggers` + connection pattern):
  - `replace_active_suggestions(items: list[AutomationSuggestion]) -> None` — single transaction: `DELETE FROM automation_suggestions WHERE status='active'` then insert each (triggers + evidence JSON-encoded, `created_at` ISO).
  - `list_active_suggestions() -> list[AutomationSuggestion]` — `WHERE status='active' ORDER BY created_at DESC`.
  - `mark_suggestion_dismissed(suggestion_id: str) -> None`
  - `mark_suggestion_accepted(suggestion_id: str, source_automation_id: str) -> None`
  - `list_excluded_signatures() -> list[str]` — `SELECT name || ' — ' || description FROM automation_suggestions WHERE status IN ('dismissed','accepted')`.

- [ ] **S1.5 Tests** (`pytest`): migration creates the table; `replace_active_suggestions` replaces only `active` rows and leaves `dismissed`/`accepted` intact; dismiss/accept transitions; `list_excluded_signatures` returns dismissed+accepted. Run: `uv run pytest apps/server/tests/automation/test_suggestions_store.py -v` (expect PASS).

### Task S2: Suggester service + prompt + wiring

**Files:**
- Modify: `apps/server/arden/automation/suggestions.py` (add `AutomationSuggester`)
- Modify: `apps/server/arden/automation/prompts.py` (synthesis prompt)
- Modify: `apps/server/arden/automation/builtins.py` (BuiltinSpec)
- Modify: `apps/server/arden/events/sse.py` (EventType + event class + registry)
- Modify: `apps/server/arden/server/runtime/automation.py` (get_cheap_llm param, handler builder, register)
- Modify: `apps/server/arden/server/runtime/core.py` (wire `get_cheap_llm`)
- Test: `apps/server/tests/automation/test_suggester.py`

- [ ] **S2.1 Prompt** in `prompts.py`: `AUTOMATION_SUGGESTER_SYSTEM` — instructs the model to propose up to `MAX_AUTOMATION_SUGGESTIONS` NEW automations grounded in the provided memory/activity context, each with name, prompt, a schedule (time `at`+`days` or `every`, or `event` with `event_type`), a one-line rationale, category, and optional lucide icon name; must NOT duplicate the listed existing automations or excluded signatures.

- [ ] **S2.2 `AutomationSuggester`** in `suggestions.py`:
  ```python
  class AutomationSuggester:
      def __init__(self, *, memory, sessions, automations, cheap_llm, model): ...
      async def run(self) -> str:   # returns summary for last_result
  ```
  - `_gather()` → a context string, all best-effort: USER-scope memory subjects (`memory` store `distinct_subjects`), recent active claims (`query`), active lenses (`lens_registry.list()`); recent session names + goals (`sessions`); existing automations (`automations.list_all()`); `automations.list_excluded_signatures()`.
  - Call `await cheap_llm.completion(messages=[system, user], model=self._model, response_format=SuggestionSet)`; parse like `retrieve.py::_parse_compression` (instance or `model_validate_json`).
  - `_validate(draft)`: `build_trigger(draft.schedule.trigger_type, at=..., days=..., every=..., event_type=..., lead_minutes=...)` inside try/except `ValueError`; drop invalid (log). Cap to `MAX_AUTOMATION_SUGGESTIONS`.
  - Build `AutomationSuggestion`s (uuid id, `created_at=datetime.now(UTC)`), `automations.replace_active_suggestions(...)`.
  - Return `f"suggestions={kept}; dropped={dropped}"`.
  - Emitting the SSE event is done by the **handler** (it owns the scheduler), not the service.

- [ ] **S2.3 SSE** in `events/sse.py`: add `AUTOMATION_SUGGESTIONS_UPDATED = "automation_suggestions_updated"` to `EventType`; add `class AutomationSuggestionsUpdatedEvent(SSEEvent)` mirroring `AutomationFinishedEvent`; register in the `EventType.X.value -> class` registry dict (~L547).

- [ ] **S2.4 Builtin** in `builtins.py`: add to `BUILTINS`:
  ```python
  BuiltinSpec(
      task_id=BUILTIN_AUTOMATION_SUGGESTER_DAILY_ID,
      name="Automation Suggester Daily",
      description="Draft contextual automation suggestions from memory, chats, and actions",
      triggers=[TimeTrigger(at=AUTOMATION_SUGGESTER_DAILY_AT, days="daily")],
      handler="automation_suggester_daily",
      auto_approve=True,
  )
  ```

- [ ] **S2.5 Runtime wiring** in `server/runtime/automation.py`:
  - Add ctor param `get_cheap_llm: Callable[[], object | None]`; store it.
  - In `start_scheduler()`: `self.scheduler.register_handler("automation_suggester_daily", self._build_automation_suggester_handler())`.
  - `_build_automation_suggester_handler()` returns `async def handler(context)`: get `memory = self.get_memory()`, `cheap_llm = self.get_cheap_llm()`; if either missing → `return None`; build `AutomationSuggester(memory=memory, sessions=self.stores.sessions, automations=self.stores.automations, cheap_llm=cheap_llm, model=<memory_model>)`; `summary = await suggester.run()`; `await self.scheduler.emit_automation_event(AutomationSuggestionsUpdatedEvent(...))`; return summary.
  - The model id: pass it in via a `get_cheap_llm` companion or add `memory_model` param. Simplest: add ctor param `cheap_model: str | None` wired from `core.py`.
- [ ] **S2.6 core wiring** in `server/runtime/core.py` `_init_automation`: add
  ```python
  get_cheap_llm=lambda: get_completion_client(self.config.memory_model) if self.config.memory_model else None,
  cheap_model=self.config.memory_model,
  ```
  (import `get_completion_client` from `arden.llm.router`).

- [ ] **S2.7 Tests** (`test_suggester.py`): stub `cheap_llm` returning a `SuggestionSet` (2 valid + 1 invalid-schedule draft); assert invalid dropped, `replace_active_suggestions` called with valid set, summary string. Stub stores minimally. Run: `uv run pytest apps/server/tests/automation/test_suggester.py -v`.

### Task S3: API endpoints + schemas

**Files:**
- Modify: `apps/server/arden/server/schemas.py` (response models + `from_suggestion_id`)
- Modify: `apps/server/arden/server/routers/automation.py` (3 routes + accept-on-create)
- Test: `apps/server/tests/memory/test_memory_router.py` sibling → `apps/server/tests/automation/test_suggestions_router.py`

- [ ] **S3.1 Schemas:** `AutomationSuggestionResponse` (id, name, description, triggers (serialized via the existing trigger→dict helper used by `_automation_to_dict`), rationale, evidence, category, icon) and `AutomationSuggestionsResponse(suggestions: list[...])`. Add `from_suggestion_id: str | None = None` to `CreateAutomationRequest`.
- [ ] **S3.2 Routes** in `routers/automation.py` (reuse the router's automation-service/store access):
  - `GET /automations/suggestions` → `list_active_suggestions()` → response.
  - `POST /automations/suggestions/{id}/dismiss` → `mark_suggestion_dismissed(id)` → 204.
  - `POST /automations/suggestions/refresh` → call the suggester (via the automation runtime handler path or a service method) → return refreshed list. (If the runtime handler isn't reachable from the router, expose `AutomationRuntime.refresh_suggestions()` that runs the same builder and returns the list.)
  - In the existing `POST /automations` create handler: if `from_suggestion_id` set, after successful create call `mark_suggestion_accepted(from_suggestion_id, new.task_id)`.
- [ ] **S3.3 Tests:** GET returns active; dismiss flips status (no longer in GET); create with `from_suggestion_id` marks accepted. Run: `uv run pytest apps/server/tests/automation/test_suggestions_router.py -v`.

---

# DESKTOP TRACK

### Task D1: API client, types, state, actions

**Files:**
- Modify: `apps/desktop/src/api.ts` (type + 3 fns + `from_suggestion_id` + `suggestionToPayload`)
- Modify: `apps/desktop/src/store/*` (add `automationSuggestions` state — mirror `automations`)
- Modify: `apps/desktop/src/actions/automations.ts` (fetch/dismiss/refresh)
- Test: `apps/desktop/tests/automationSuggestions.test.ts`

- [ ] **D1.1 Types + client** in `api.ts` (mirror `listAutomationsApi` etc.):
  ```ts
  export interface AutomationSuggestion {
    id: string; name: string; description: string;
    triggers: AutomationTrigger[]; rationale: string;
    evidence: string[]; category: string; icon: string | null;
  }
  export async function listAutomationSuggestionsApi(): Promise<AutomationSuggestion[]>
  export async function dismissAutomationSuggestionApi(id: string): Promise<void>
  export async function refreshAutomationSuggestionsApi(): Promise<AutomationSuggestion[]>
  ```
  Add `from_suggestion_id?: string` to `CreateAutomationPayload` and pass it through `createAutomationApi`.
- [ ] **D1.2 `suggestionToPayload(s: AutomationSuggestion): CreateAutomationPayload`** — name, description, `from_suggestion_id: s.id`, and the flat schedule fields derived from `s.triggers[0]` (`trigger_type` from `.type`, plus `at/days/every` or `event_type/lead_minutes`) so the existing `formFromPreset` hydrates the editor unchanged.
- [ ] **D1.3 State + actions:** add `automationSuggestions: AutomationSuggestion[]` to the store (mirror how `automations` is held/updated); `fetchAutomationSuggestions()`, `dismissSuggestion(id)` (optimistic remove + API), `refreshSuggestions()`.
- [ ] **D1.4 Test:** `suggestionToPayload` maps a time trigger and an event trigger correctly; `dismissSuggestion` removes from state + calls API (mock). Run the desktop test command from `apps/desktop/package.json` (e.g. `bun test` / `vitest`).

### Task D2: "Suggested for you" UI + SSE refresh

**Files:**
- Modify: `apps/desktop/src/components/AutomationsModal.tsx` (new section + card)
- Modify: the SSE/event hook that handles `automation:events` (mirror existing automation event handling) to re-fetch on `automation_suggestions_updated`
- Test: `apps/desktop/tests/lensesView.test.tsx`-style component test → `apps/desktop/tests/automationSuggestions.view.test.tsx`

- [ ] **D2.1 Section:** in the Templates tab, above `templatesByCategory()`, render a "Suggested for you" group from `automationSuggestions`. Hidden entirely when the list is empty (cold-start → static templates only).
- [ ] **D2.2 Suggestion card:** reuse/extend `TemplateCard` — show `name`, `rationale` (as the blurb), a schedule chip (reuse the existing trigger formatter), an icon (map `icon` string → Lucide, default `Sparkles`), and a dismiss `×` (calls `dismissSuggestion`). Card click → `setEditor({ kind: "create", preset: suggestionToPayload(s) })`.
- [ ] **D2.3 Fetch triggers:** call `fetchAutomationSuggestions()` in the modal-open effect next to `fetchAutomations()`, and on the `automation_suggestions_updated` SSE event.
- [ ] **D2.4 Tests:** suggestions render; dismiss removes a card; click seeds the editor with the mapped payload; empty list hides the section. Keep `automationTemplates.test.ts` green (templates.ts untouched).

---

## Verification (after both tracks)

- [ ] Server: `uv run pytest apps/server/tests/automation -v` (+ touched memory/router tests) all green.
- [ ] Desktop: run the desktop test + typecheck (`tsc --noEmit` or the package script) green.
- [ ] Manual contract check: `GET /automations/suggestions` shape matches the desktop `AutomationSuggestion` type and `suggestionToPayload` output feeds `formFromPreset`.

## Self-review notes
- Schedule validation reuses `build_trigger` (no parallel validator).
- `replace_active_suggestions` only deletes `active`, preserving dismissed/accepted for dedup — matches spec.
- `from_suggestion_id` is the only change to an existing contract (additive/optional).
- `templates.ts` is never touched → static guard test stays green.
