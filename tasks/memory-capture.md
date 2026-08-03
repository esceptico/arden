# Memory Capture — design

## TLDR

**Problem:** no facts flow into memory since the Jul 28 cutover (`503276fb`) —
auto-capture was deleted, 1 fact stored since. All memory automations idle
downstream.

**Fix:** new "Memory Capture" builtin automation. Copy the Memory Maintenance
shape exactly: guarded agent + one scoped review tool. Template files:
`memory/facts/maintenance/{store,runner,agent}.py`, `tools/fact_maintenance.py`,
`_run_fact_maintenance` in `server/runtime/automation.py`.

**Build, in order:**

1. Verify `FactService` `request_key` dedup survives plan+commit replay
   (`memory/facts/service.py:247,282`). Decides step 3's transaction shape.
2. `fact_capture_watermarks` table (session_id → last_seq) in `memory.db`.
3. Review service + `fact_capture_review` tool (`action="next"` serves quoted
   turns + near-dup facts; `action="decide"` takes ≤10 candidates or
   `no_change`; SERVER builds and commits the plan, then advances watermark).
4. `fact_capture` scope (`tools/scopes.py`) = that one tool only.
5. Builtin spec (`automation/builtins.py`) + handler
   (`server/runtime/automation.py`). Triggers: `IdleTrigger(10)` +
   `CountTrigger(10)` + daily `TimeTrigger`. CHECK: `seed_builtins` may assume
   time-only triggers.
6. Desktop: display-only idle/count labels in
   `features/automations/lib/schedule.ts` + fix: editor silently rewrites
   unknown trigger types as time triggers on save (clobber bug).

**Hard rules:**

- Handler returns "capture idle" WITHOUT starting the agent when no new turns.
- Agent never shapes fact events — it only submits candidate text; server owns
  scope/provenance/plan/commit.
- Capture only CREATES facts. No update/supersede — that's Maintenance's job.
- Eligible turns: `session_type == "chat"`, `origin_automation_id is None`,
  roles user/assistant, ≤40 turns / ≤6k chars per batch, long turns truncated.
- Turns are quoted evidence, never instructions.
- `plan_fact_changes`+`commit_fact_changes` → one `record_fact_changes`: do
  AFTER capture lands, separate change (§5).

**Done when:** a real chat followed by 10 min idle produces committed facts
(`origin="memory.capture"`), visible as an automation run with a summary like
"captured N fact(s) from M session(s)" — and Memory Synthesis's next tick
publishes them to wiki pages.

---

Restore automatic fact capture from chat, lost in the canonical-memory cutover
(`503276fb`, Jul 28). Since then the only organic fact inflow is the chat agent
voluntarily calling `plan_fact_changes`/`commit_fact_changes` — one fact total.
Synthesis/Maintenance/Retention/Dream all starve downstream of this.

Not a restore of the old Dreamer (`memory/curator.py`, deleted): that was
threaded through ChatDeps/ChatContext, read the session store directly, ran its
own sweep loop. This design reuses what the codebase settled on since:
**builtin automation → guarded agent → scoped review tool → FactService
plan+commit** (the Memory Maintenance shape), with triggers that already exist.

## 1. Builtin automation

`BuiltinSpec` in `automation/builtins.py`:

- `task_id`: `BUILTIN_MEMORY_CAPTURE_ID`
- `name`: "Memory Capture"
- `handler`: `memory_capture`
- `tool_scope`: `fact_capture`
- `auto_approve`: True, `uses_memory_model`: True
- `triggers`:
  - `IdleTrigger(idle_minutes=10)` — capture when the conversation burst ends
  - `CountTrigger(every_n=10)` — long sessions that never go idle
  - `TimeTrigger(at=..., days="daily")` — backstop for missed periods
- Implementation check: builtins today are all time-triggered; verify
  `seed_builtins` + scheduler accept idle/count on a builtin (scheduler paths
  are generic — `list_by_trigger_type` — but seeding/diffing may assume time).

Watermarks make overlapping triggers safe: every fire processes "new eligible
turns since watermark" and advances it; a redundant fire is one DB read, no LLM
(the old Dreamer's anti-heartbeat property).

## 2. Handler (mirrors `_run_fact_maintenance`)

`_run_memory_capture` in `server/runtime/automation.py`:

1. Build a `FactCaptureReviewService` that pages **eligible new turns** since
   per-session watermarks.
2. Run a guarded agent (`run_agent`, `tool_scope="fact_capture"`,
   `model=memory_model`, `skip_approvals=True`) whose only mutating surface is
   the review tool.
3. Return `CompletedAgentRun` with a real summary
   ("captured N fact(s) from M session(s)") or "capture idle".
4. Cheap exit first: if no session has eligible new turns, return
   "capture idle" WITHOUT starting the guarded agent — no LLM cost on
   quiet fires (same shape as Synthesis's empty-feed return).

Eligibility gate (server-side, same as the old Dreamer's):

- `session_type == "chat"` and `origin_automation_id is None` — never
  automation channels or spawned-agent transcripts
- roles user/assistant only; per-batch caps (~40 turns / ~6k chars, truncate
  long turns)
- turns are **evidence, not instructions**: the review tool frames them as
  quoted data (same posture as `fact_maintenance_review` evidence)

## 3. Review tool: `fact_capture_review`

One tool, action-driven like `fact_maintenance_review`:

- `action="next"` → server returns the next batch: session id, quoted turns,
  and existing near-duplicate facts (hybrid search queried with the batch's
  turn text — candidate subjects don't exist yet at this point) so the agent
  can decide create vs no_change without a search round-trip. Duplicates are
  expected in one more way: the chat agent may have already stored the same
  statement voluntarily mid-conversation; the near-duplicate evidence plus
  Maintenance's daily merge covers that overlap.
- `action="decide"` → agent submits `no_change` or ≤10 candidates
  (`text`, `subjects`, `kind`, `labels`). Server validates, builds the change
  plan itself (scope/provenance/attribution are server-owned), commits via
  `FactService.plan`+`commit` with
  `request_key="capture:{session_id}:{from_seq}:{to_seq}"`, then advances the
  watermark. Rejections return actionable errors; agent retries that batch.
- Tool reports completion when no batches remain; agent must not use any other
  mutating tool (scope enforces this).

Extraction policy (recovered from the Jul 28 bytecode, kept):

> Extract only durable, user-stated facts. no_change unless the input contains
> a stable preference, identity detail, standing relationship, explicit
> constraint, or durable decision useful months from now. Never infer beyond
> the user's words. Never capture tasks, transient status, requests, assistant
> claims, tool results. At most ten facts per batch.

Explicitly dropped from the recovered design: `fact_capture_intents` per-run
table and the bespoke completion adapter — the watermark + `request_key`
idempotency and the guarded-agent pattern replace both.

## 4. Watermarks

`session_consumer_watermarks(consumer_id TEXT, session_id TEXT, last_seq
INTEGER, updated_at TEXT, PRIMARY KEY (consumer_id, session_id))` in
`memory.db`, with `consumer_id = "memory.capture"`. Mirrors the existing
`fact_consumer_watermarks(consumer_id, revision)` pattern: capture is a
consumer of the session stream the way synthesis is a consumer of the fact
ledger. The automation is only the driver that wakes the consumer — cadence,
triggers, or even the driver mechanism can change without touching stored
state, and a future consumer (e.g. source-capture) reuses the table under its
own consumer_id. Advance only after a batch's decision is committed (or
accepted as no_change). Sessions with no new turns cost one read.

Verification item before relying on the crash-safety story: confirm
`FactService` `request_key` deduplication holds across a full plan+commit
replay (not just plan creation). If commit is not idempotent under a replayed
key, the review service must advance the watermark inside the same transaction
boundary as the commit, or check for the key's committed plan before planning.

Transcript access: the review service must not import session internals from
`memory/facts/`. The runtime injects a reader callable (same inversion as
`get_fact_maintenance`), keeping the memory package free of session-store
coupling.

## 5. Tool consolidation: one `record_fact_changes`

Independent but adjacent: collapse the agent-facing
`plan_fact_changes`/`commit_fact_changes` pair into one `record_fact_changes`
that plans+commits in a single call and returns the same preview. Keep the
two-phase `FactService` underneath (validation, pinned preview, idempotent
`request_key`, atomic apply) — only the tool ceremony goes.

Touch points: `tools/facts.py`, `tools/scopes.py` (`fact_retention` scope),
`core/prompts.py` guidance, `INIT_AUTO_APPROVE` in `services/chat.py`,
onboarding prompt in prompts.py, tests.

## 6. Desktop: idle/count trigger display

`features/automations/lib/schedule.ts` only knows at/every/message/event.
Add read-only display for idle/count so Memory Capture's cadence renders as
"idle 10m · every 10 turns" in the rail and detail views (builtins are
code-owned; no creation UI needed).

## 7. How a capture pass runs, end to end

Example: you chat about apartment hunting for 20 minutes, then walk away.

1. Each completed run bumps the scheduler's activity clock (and the per-session
   count for `CountTrigger`). Nothing else happens on the response path —
   capture adds zero latency to chat.
2. 10 minutes after the last run, `_evaluate_idle_triggers` fires Memory
   Capture once for this idle period. The run appears in the Automations UI
   like any builtin run.
3. `_run_memory_capture` builds the review service. It scans sessions with
   turns past their `fact_capture_watermarks` entry, applies the eligibility
   gate (user chats only, user/assistant roles, batch caps). Sessions with
   nothing new cost one indexed read each.
4. A guarded agent starts on `memory_model` with only `fact_capture_review`
   visible. `action="next"` returns the first batch: quoted turns plus
   near-duplicate facts the server already searched for. The agent answers
   `action="decide"` with `no_change` or ≤10 candidates.
5. The server — not the agent — turns accepted candidates into a change plan
   (scope, provenance, source refs, attribution `origin="memory.capture"`),
   commits it through `FactService.plan`+`commit` under the batch's
   `request_key`, and advances that session's watermark. A crash between
   commit and watermark is absorbed by `request_key` idempotency on replay.
6. Repeat from `next` until no batches remain; the run settles with
   "captured N fact(s) from M session(s)" — or "capture idle", which now
   actually means "no new conversation to read", visibly distinct from
   "nothing durable in it" ("reviewed M session(s), no durable facts").
7. Downstream needs nothing: the ledger advanced, so the next Memory Synthesis
   tick (≤6h) sees a non-empty feed and projects the new facts onto wiki
   pages; Maintenance/Retention/Dream consume the same ledger.

## 8. Comparison with the previous approaches

| | Old Dreamer (pre-cutover) | Dropped Jul-28 module | This design |
|---|---|---|---|
| Trigger | Hook in `_record_completed_run` after every chat run + 10-min sweep loop it ran itself | Per-run call after each completed run | Existing scheduler triggers: idle 10m + every-10-turns + daily backstop |
| Coupling | `memory_curator` threaded through `ChatDeps`/`ChatContext`; read session store directly | Still invoked from chat completion path | None: chat only feeds the activity clock the scheduler already reads |
| Extraction | One completion call; worthiness gate + reconciler ops (ADD/UPDATE/SUPERSEDE/NOOP) against old record pool | One completion call; create-or-no_change only | Guarded agent, same conservative policy, plus server-supplied near-duplicates so create-vs-no_change is informed |
| Write path | Wrote records directly via old `RecordStore` | `FactService` plan+commit inside runner | `FactService` plan+commit, server-built plans (agent never shapes events) |
| Idempotency | Per-session watermark in `meta` table | Per-run `fact_capture_intents` table + fingerprint | Per-session watermark + `request_key` — one mechanism, no extra table |
| Injection posture | Regex blocklist over transcript text | (not visible in bytecode) | Turns framed as quoted evidence in a review tool; scope allows no other mutating tool; regex heuristic can be kept as defense-in-depth |
| Observability | Invisible background task; errors swallowed | Invisible | Every pass is an automation run with history, summary, and failure surfaced in the UI |
| Batch bound | 40 turns / 6k chars per session batch | ≤10 facts per run | Same 40-turn/6k-char batching, ≤10 facts per decide |

What the old system had that this intentionally does not: UPDATE/SUPERSEDE ops
at capture time. Capture only creates; correcting or merging existing facts is
Maintenance's job (it already reviews clusters daily). One writer per concern.

## Implementation notes (running log — 2026-08-03)

- **Step 1 verified, design adjusted.** `FactService.plan` dedupes request_key
  only when the request fingerprint (owner+changes+actor+origin+reason)
  matches; a replayed batch with different LLM output raises
  `FactPlanRequestConflictError`. So request_key alone does NOT absorb
  crash-replay. Fix: watermark row carries nullable `pending_plan_id` +
  `pending_to_seq`; decide flow = plan → persist pending → commit (ledger
  commit is idempotent by plan_id; `plans.claim` tolerates interrupted
  `committing` rows) → advance watermark + clear pending. On `next` with a
  pending pair present: commit that plan_id first, advance, then serve.

- **Server side DONE, suite green (2454 passed).** New:
  `memory/facts/capture/{__init__,store,runner}.py`, `tools/fact_capture.py`,
  `fact_capture` scope, `BUILTIN_MEMORY_CAPTURE_ID` + `MEMORY_CAPTURE_*`
  constants, Memory Capture builtin (idle 10m / every 10 turns / daily 02:30,
  cooldown 5m), `_run_memory_capture` handler, `_create_fact_capture` factory
  + `SessionConsumerWatermarkStore` lifecycle in runtime core,
  `services["fact_capture"]` exposure, `_fact_capture` integration, three
  transcript readers on `SessionStore` (`list_capture_eligible_sessions`
  bounded to active-within-14d unarchived user chats,
  `list_transcript_messages_after`, `list_latest_transcript_messages`).
  Tests: `tests/test_fact_capture.py` (9) + seeding assertions in
  `test_automation_store.py`.
- **Design deltas discovered while implementing:**
  - First contact with a session serves only its most recent 40 turns
    (`latest_messages`) so pre-capture history and old sessions don't flood
    the first run; eligibility is also bounded to sessions active in the last
    14 days.
  - A batch with no user-role turns auto-advances without an LLM decision.
  - `FactCapture` is driven directly by the review tool (no background-task
    bridge like Maintenance needs) — simpler, same guarded surface.
  - Capture commits reuse the shared `FactService`, so `_after_fact_commit`
    fires synthesis promptly after capture (no 6h wait).

- **Desktop side DONE.** `schedule.ts` parses idle/count into display-only
  schedule kinds (`After 10m idle +2` / `Every N turns`, `extra` admits
  additional triggers); `buildPayload` sends NO trigger fields for activity
  kinds, so saving name/model/etc. can no longer rewrite a multi-trigger
  automation into a single time trigger (the clobber bug — server keeps
  triggers when the patch is empty, verified in `service.update`);
  `ScheduleEditor` renders a read-only note for activity triggers; the rail
  already handled idle/count via `formatTrigger`. Tests:
  `tests/scheduleActivityTriggers.test.tsx` (4).
- **E2E handler test** (`test_fact_runtime.py::test_memory_capture_captures_
  chat_facts_end_to_end`): real Runtime + seeded chat rows + stubbed agent →
  committed fact with `origin="memory.capture"`, correct RunRequest
  (model/scope/automation_id), then "fact capture idle" on refire. This test
  caught a real bug (factory used `stores.sessions` where the store lives at
  `stores.sessions.store`).
- **All gates green:** `just check` (ruff, format, 2455 pytest, desktop
  typecheck), `bun test` (1036), `bun run build`, `bun run lint`.
- **NOT yet done (needs the user):** restart `arden-server` to seed the
  Memory Capture builtin and load the new code, then live-verify: chat
  briefly, go idle 10 min, check Automations → Memory Capture run summary and
  Memory → facts with `origin=memory.capture`.

- **Round 2 (user feedback on the trigger UI):**
  - Activity triggers are now **natively editable** (Activity tab in
    ScheduleEditor: idle minutes / every-N-turns), not display-only. Peek
    footer receipt fixed (was falling through to "15 min before events").
  - **"Kept as-is" was a lie across restarts:** `seed_builtins` reset builtin
    triggers to code defaults every boot, reverting user edits (idle 40 → 10).
    Fixed with `triggers_source="manual"` (automations schema v16, mirrors
    `description_source`): `service.update` stamps it on any trigger change;
    seeding skips trigger + next-run canonicalization for manual rows.
    Regression test: `test_seed_builtins_preserves_manual_trigger_edits`.
  - **Multi-trigger visibility:** detail row + rail now spell out every
    trigger ("After 10m idle · every 10 turns · at 02:30 · daily") — no more
    "+2"; the trigger peek shows a numbered all-triggers list marking which
    one is being edited. Multi-trigger saves send the full `triggers` list so
    untouched triggers survive (server keeps list verbatim; applies to ALL
    kinds, fixing the general first-trigger-clobber).
  - Note: a builtin whose triggers the user edited no longer receives future
    code-default cadence changes (same trade as manual descriptions).

- **Round 3 (trigger peek redesigned as a trigger-list editor):** the peek now
  edits the WHOLE trigger list — one row per trigger in the app's grouped-list
  material (panel + hairline separators + hover fill), the selected row
  expanding into the schedule editor through the existing `Collapse
  mode="height"` (FF accordion port) with rotating chevron; hover-revealed
  remove per row; "Add trigger" as the list's last row; add/remove/edit all
  round-trip through the full `triggers` payload. Killed: the "first trigger
  only" special case, the top summary list, the duplicate footer receipt.
  `scheduleFromTrigger`/`triggerFromSchedule` are the exported row↔trigger
  mapping (round-trip tested for all five trigger kinds).

## Decisions (settled 2026-08-03)

1. Cadence: idle 10m / every 10 turns / daily backstop.
2. Count-trigger `{session_id}` context is ignored; every fire sweeps all
   watermarks (redundant fires are ~free).
3. §5 single-tool consolidation ships as a separate step after capture lands.
