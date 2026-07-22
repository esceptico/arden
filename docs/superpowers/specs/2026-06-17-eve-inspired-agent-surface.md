# Eve-inspired Arden agent surface

Date: 2026-06-17
Status: proposal
Branch: `eve-agent-surface-spec`

## Summary

Vercel Eve's useful contribution is not a runtime we should adopt. It is a clean authoring and inspection shape for production agents: agents as directories, capabilities as files, durable sessions/runs as explicit objects, and evals/traces over the real event stream.

Arden should steal Eve's shape while keeping Arden's existing runtime: deferred tools, approvals, memory, automations, SSE events, skills, desktop UI, and workflow orchestration.

## Sources

- Vercel launch post: https://vercel.com/blog/introducing-eve
- Eve repository: https://github.com/vercel/eve
- Eve project layout docs: `docs/reference/project-layout.md`
- Eve sessions/runs/streaming docs: `docs/concepts/sessions-runs-and-streaming.md`
- Eve execution/durability docs: `docs/concepts/execution-model-and-durability.md`
- Eve tools and approval docs: `docs/tools/overview.mdx`, `docs/tools/human-in-the-loop.md`
- Eve schedules docs: `docs/schedules.mdx`
- Eve evals docs: `docs/evals/overview.mdx`, `docs/evals/assertions.mdx`, `docs/evals/judge.mdx`
- Eve channels docs: `docs/channels/overview.mdx`, `docs/channels/slack.mdx`

## Goals

1. Make Arden's runtime inspectable from one stable API and CLI command.
2. Add a filesystem-authored agent surface that compiles into existing arden primitives.
3. Make parked workflow states first-class across UI, automations, tools, and evals.
4. Let evals assert against real arden event streams instead of only final text.
5. Preserve Arden's current safety model: deferred tools, approvals, scoped memory, and explicit user confirmation for mutations.

## Non-goals

- Do not depend on Eve as a runtime package.
- Do not copy Vercel-specific hosting, dashboards, or deployment assumptions.
- Do not make the full repo available to sandboxed/agent-written code by default.
- Do not replace existing DB-backed automations or skills; add a Git-friendly authoring overlay.
- Do not silently weaken current tool approval behavior.

## Proposed filesystem shape

```txt
agent/
  instructions.md
  tools/
  skills/
  schedules/
  hooks/
  channels/
  subagents/
  sandbox/
  workspace/
  lib/

evals/
  *.eval.py

.arden/
  manifest.json
  discovery.json
  warnings.json
```

This directory is an optional project overlay. Built-in arden capabilities still come from the server, integration registries, memory system, and configured tool providers.

## Runtime inspection

Add:

```http
GET /runtime/info
```

and:

```bash
arden info
```

The response should expose the active runtime surface:

```json
{
  "version": "...",
  "agent_surface": {
    "root": "agent/",
    "manifest_path": ".arden/manifest.json"
  },
  "tools": [],
  "deferred_tool_groups": [],
  "skills": [],
  "automations": [],
  "schedules": [],
  "channels": [],
  "hooks": [],
  "subagents": [],
  "sandbox": {},
  "event_types": [],
  "warnings": []
}
```

This is the highest-leverage first step because arden already has registries and event types, but developers cannot see the complete runtime shape in one place.

## Path-derived discovery

Capabilities authored on disk should have stable IDs derived from paths.

Examples:

```txt
agent/skills/revenue-definitions/SKILL.md -> skill: revenue-definitions
agent/schedules/daily-digest.md -> schedule: daily-digest
agent/schedules/memory/rebuild.yaml -> schedule: memory/rebuild
agent/tools/slack/summarize_thread.py -> tool: slack/summarize_thread
```

Open question: model-facing tool names may need normalization from `slack/summarize_thread` to `slack_summarize_thread`. The manifest should preserve both the stable path ID and model-safe name.

## Schedules as files

Support filesystem-authored schedules that compile into the existing automation system:

```txt
agent/schedules/daily_digest.yaml
agent/schedules/memory_sweep.md
agent/schedules/custom_handler.py
```

Initial supported formats:

### Markdown prompt schedule

```md
---
cron: "0 9 * * 1"
timezone: "America/Los_Angeles"
channel: "chat"
---

Summarize last week's high-signal memory changes and post a concise report.
```

### YAML schedule

```yaml
id: daily-digest
cron: "0 9 * * *"
timezone: "Asia/Yerevan"
prompt: "Prepare my daily arden digest."
channel: "chat"
```

Add dev dispatch:

```http
POST /runtime/dev/schedules/{schedule_id}/dispatch
```

This should start a real session/run and return identifiers.

## Skills as progressive disclosure

Keep Arden's current skill model: advertise only skill names/descriptions by default, then load full bodies via `use_skill` when relevant.

Filesystem skills should remain package-like:

```txt
agent/skills/my-skill/SKILL.md
agent/skills/my-skill/templates/example.md
agent/skills/my-skill/scripts/helper.py
```

The manifest should report invalid skills and skipped paths rather than failing the whole runtime.

## Canonical workflow states

Define a normalized workflow state model:

```txt
running
waiting_for_approval
waiting_for_input
waiting_for_auth
waiting_for_subagent
completed
failed
cancelled
```

Existing events should map into this model without breaking current event names:

```txt
approval_needed -> waiting_for_approval
input_needed -> waiting_for_input
background/subagent pending -> waiting_for_subagent
oauth/auth required -> waiting_for_auth
```

The UI, automations, channel adapters, and evals should all consume the same normalized state field.

## Session/run/channel identity model

Make this distinction explicit:

```txt
channel delivery handle != session id != run id != stream cursor
```

Recommended terms:

- `session_id`: durable conversation/task identity.
- `run_id`: one active execution inside a session.
- `turn_id`: one user/input turn within a run.
- `step_id`: one model/tool/approval/event step.
- `cursor`: SSE or event replay cursor.
- `continuation_token`: channel-owned resume token, not a durable queue by itself.

This prevents Slack/email/webhook delivery state from leaking into runtime identity.

## Event-aware eval DSL

Build evals over real Arden server events.

Example:

```py
async def test_deferred_slack_search(t):
    await t.send("Find that Slack thread about Eve")
    t.called_tool("load_tools")
    t.loaded_tool_group("slack")
    t.no_failed_actions()
    t.completed()
```

Assertions to support first:

```py
t.completed()
t.failed()
t.waiting_for_approval()
t.waiting_for_input()
t.called_tool("tool_name")
t.loaded_tool_group("slack")
t.event_type("approval_needed")
t.no_failed_actions()
t.reply_includes("text")
```

Judge-based evals can come later. Deterministic event assertions are the important part.

## Tool result projection

Formalize the existing split between full tool output, UI preview, and model-visible text:

```py
ToolResult(
    data=full_structured_result,
    content=human_or_ui_text,
    model_content=minimized_or_redacted_observation,
    preview=audit_preview,
)
```

Every new tool should intentionally define what the model sees. This matters for large outputs, secrets, memory records, Slack/email data, and long traces.

## Approval policy upgrade

Current boolean approval is too small long-term. Add richer effective policies while keeping backward compatibility:

```py
class ApprovalMode(StrEnum):
    NEVER = "never"
    ONCE = "once"
    ALWAYS = "always"
    PREDICATE = "predicate"
```

Compatibility rule:

```txt
requires_approval = true  -> approval_mode = always
requires_approval = false -> approval_mode = never
```

Predicates must never silently downgrade a previously approval-required tool.

## Channel adapters and queues

Longer-term, define channels as adapters around the runtime:

```txt
Channel = auth + inbound normalization + native thread IDs + queueing + output rendering
Runtime = session/run/turn execution + events + tools + memory
```

Each channel owns:

- native thread/message IDs,
- delivery queue,
- continuation token,
- approval rendering,
- auth rendering,
- final response rendering.

Runtime owns:

- sessions,
- runs,
- turns,
- workflow states,
- event stream,
- tool execution.

Do not pretend continuation tokens are FIFO queues. Channel adapters should serialize delivery per native thread/session.

## Sandbox boundary

If arden adds Eve-style agent-written code execution, keep a hard boundary:

- no full repo mount by default,
- seed only explicit workspace files,
- preserve audit logs for file reads/writes/commands,
- approvals for writes outside workspace,
- separate runtime credentials from sandbox credentials.

Initial spec work should not implement sandboxing. Just reserve the shape:

```txt
agent/sandbox/
agent/workspace/
```

## Implementation roadmap

### Phase 1: runtime info and manifest

Create:

```txt
apps/server/arden/agent_surface/__init__.py
apps/server/arden/agent_surface/models.py
apps/server/arden/agent_surface/discovery.py
apps/server/arden/agent_surface/manifest.py
apps/server/arden/server/routers/runtime_info.py
docs/architecture/agent-surface.md
```

Change:

```txt
apps/server/arden/server/app.py
apps/server/arden/cli.py
```

Deliverables:

- `GET /runtime/info`
- `arden info`
- `.arden/manifest.json`
- validation warnings for invalid/missing filesystem capabilities

### Phase 2: filesystem skills and schedules

Create:

```txt
apps/server/arden/agent_surface/skills.py
apps/server/arden/agent_surface/schedules.py
apps/server/arden/server/routers/dev_runtime.py
docs/guides/filesystem-agent.md
```

Change:

```txt
apps/server/arden/skills/registry.py
apps/server/arden/automation/service.py
apps/server/arden/automation/store.py
apps/server/arden/server/app.py
```

Deliverables:

- scan `agent/skills/**/SKILL.md`
- scan `agent/schedules/**/*.{md,yaml,yml}`
- compile schedules into current automation records
- dev dispatch endpoint for schedules

### Phase 3: normalized workflow state

Create:

```txt
apps/server/arden/workflow/__init__.py
apps/server/arden/workflow/models.py
apps/server/arden/workflow/store.py
docs/architecture/runtime-events.md
```

Change:

```txt
apps/server/arden/events/sse.py
apps/server/arden/server/routers/chat.py
apps/server/arden/services/chat.py
apps/server/arden/tools/core/types.py
```

Deliverables:

- normalized state field on relevant runtime events
- UI compatibility with old event names
- persisted state for parked work

### Phase 4: event-aware evals

Create:

```txt
evals/client.py
evals/assertions.py
evals/runtime_case.py
evals/cases/basic_chat.eval.py
evals/cases/deferred_tools.eval.py
evals/cases/approval_wait.eval.py
evals/cases/schedule_dispatch.eval.py
```

Change:

```txt
evals/run.py
evals/report.py
```

Deliverables:

- drive a real Arden server via HTTP/SSE
- capture event streams
- deterministic event assertions
- CI-friendly reports

### Phase 5: approval policy model

Change:

```txt
apps/server/arden/tools/core/types.py
apps/server/arden/tools/core/registry.py
apps/server/arden/tools/core/base.py
apps/server/arden/server/routers/chat.py
apps/desktop/src/**/approval*.tsx
```

Deliverables:

- `ApprovalMode`
- backward-compatible `requires_approval`
- tool metadata reports effective approval policy
- UI can explain why approval is needed

### Phase 6: channel adapter boundary

Create:

```txt
apps/server/arden/channels/__init__.py
apps/server/arden/channels/base.py
apps/server/arden/channels/models.py
apps/server/arden/channels/queue.py
apps/server/arden/channels/slack.py
apps/server/arden/channels/email.py
docs/architecture/channels.md
```

Deliverables:

- channel-owned delivery queues
- native thread/message ID mapping
- approval/input/auth rendering hooks
- no conflation between channel continuation and runtime session identity

## Priority

If we only do three things:

1. `/runtime/info` and `.arden/manifest.json`
2. filesystem-authored schedules backed by existing automations
3. event-aware evals over real runtime events

Those three give the most Eve-style leverage without taking on Eve's platform assumptions.

## Risks

- Path-derived IDs can collide with built-in tools or existing skills.
- File-authored and DB-authored automations need clear precedence rules.
- Filesystem-authored tools can become arbitrary code execution if implemented naively.
- Richer approval policies could weaken safety if migration is careless.
- Event taxonomy changes can break desktop trace rendering if not additive.
- Channel queues can duplicate or drop messages if session/run state is not explicit.
- Sandbox features are security-sensitive and should not be rushed.

## Decision

Proceed with Phase 1 first. Do not implement tools, sandboxing, or channel rewrites until the runtime is inspectable and the event model is documented.
