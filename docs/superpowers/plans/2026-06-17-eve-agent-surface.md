# Eve Agent Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Eve-inspired Arden agent surface in six committed phases.

**Architecture:** Add an additive runtime-inspection layer first, then filesystem skills/schedules, normalized workflow state, event-stream eval helpers, richer approval policy metadata, and channel adapter boundaries. Existing runtime behavior remains canonical.

**Tech Stack:** Python 3.13, FastAPI, Click, pytest, Pydantic, YAML.

---

### Phase 1: Runtime Info And Manifest

**Files:**
- Create: `apps/server/arden/agent_surface/{__init__.py,models.py,discovery.py,manifest.py}`
- Create: `apps/server/arden/server/routers/runtime_info.py`
- Modify: `apps/server/arden/server/app.py`
- Modify: `apps/server/arden/cli.py`
- Test: `apps/server/tests/test_agent_surface_runtime_info.py`

- [ ] Add failing tests for path-derived discovery, manifest writing, `/runtime/info`, and `arden info`.
- [ ] Implement models, discovery, manifest writer, router, and CLI command.
- [ ] Run phase tests and commit.

### Phase 2: Filesystem Skills And Schedules

**Files:**
- Create: `apps/server/arden/agent_surface/{skills.py,schedules.py}`
- Create: `apps/server/arden/server/routers/dev_runtime.py`
- Modify: `apps/server/arden/skills/registry.py`
- Modify: `apps/server/arden/automation/{models.py,service.py,store.py}`
- Modify: `apps/server/arden/server/app.py`
- Test: `apps/server/tests/test_agent_surface_filesystem.py`

- [ ] Add failing tests for `agent/skills/**/SKILL.md`, schedule parsing, automation compilation, and dev dispatch.
- [ ] Implement filesystem skill loading and schedule compilation into existing automation records.
- [ ] Run phase tests and commit.

### Phase 3: Normalized Workflow State

**Files:**
- Create: `apps/server/arden/workflow/{__init__.py,models.py,store.py}`
- Modify: `apps/server/arden/events/sse.py`
- Modify: `apps/server/arden/server/routers/chat.py`
- Modify: `apps/server/arden/services/chat.py`
- Modify: `apps/server/arden/tools/core/types.py`
- Test: `apps/server/tests/test_workflow_state.py`

- [ ] Add failing tests for event-to-state mapping and parked-state persistence.
- [ ] Add normalized workflow state to relevant runtime events without renaming existing events.
- [ ] Run phase tests and commit.

### Phase 4: Event-Aware Evals

**Files:**
- Create: `evals/client.py`, `evals/assertions.py`, `evals/runtime_case.py`
- Create: `evals/cases/{basic_chat.eval.py,deferred_tools.eval.py,approval_wait.eval.py,schedule_dispatch.eval.py}`
- Modify: `evals/run.py`, `evals/report.py`
- Test: `apps/server/tests/test_event_aware_evals.py`

- [ ] Add failing tests for deterministic event assertions.
- [ ] Implement reusable eval stream capture and assertions.
- [ ] Run phase tests and commit.

### Phase 5: Approval Policy Model

**Files:**
- Modify: `apps/server/arden/tools/core/{types.py,registry.py,base.py}`
- Modify: `apps/server/arden/server/routers/chat.py`
- Modify: `apps/desktop/src/**/approval*.tsx`
- Test: `apps/server/tests/test_approval_policy.py`

- [ ] Add failing tests for `ApprovalMode` compatibility and metadata.
- [ ] Implement additive approval policy fields while preserving `requires_approval`.
- [ ] Run phase tests and commit.

### Phase 6: Channel Adapter Boundary

**Files:**
- Create: `apps/server/arden/channels/{__init__.py,base.py,models.py,queue.py,slack.py,email.py}`
- Create: `docs/architecture/channels.md`
- Test: `apps/server/tests/test_channel_adapters.py`

- [ ] Add failing tests for runtime identity versus channel delivery identity.
- [ ] Implement channel adapter models and serialized per-native-thread queues.
- [ ] Run phase tests and commit.
