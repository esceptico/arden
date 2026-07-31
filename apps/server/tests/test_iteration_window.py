"""Bounded history window for iteration-mode loops.

When a loop fires in `read_history=True` mode it re-enters the target
session. To keep the prompt context bounded for long-running monitors we
cap prior history to LOOP_ITERATION_HISTORY_WINDOW messages — preserving
the system message at index 0 and keeping the most recent tail.

Trimming is runtime-only — disk history is untouched.
"""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from arden.automation.prompts import AUTOMATION_SUFFIX
from arden.constants import LOOP_ITERATION_HISTORY_WINDOW
from arden.context.models import SessionData, SessionState
from arden.core.factory import AgentConfig
from arden.events.internal import RunFailed
from arden.server.app import _recover_interrupted_chat_runs
from arden.server.bus import BusRegistry
from arden.server.state import RunRegistry
from arden.services.chat import (
    ChatDeps,
    _chat_request_hash,
    _close_interrupted_tool_step,
    _persistable_messages,
    _trim_for_loop_iteration,
    prepare_chat,
    resume_suspended_chat_run,
)
from arden.tools.executor import ToolExecutor


def _make_history(n: int, *, with_system: bool = True) -> list[dict]:
    msgs: list[dict] = []
    if with_system:
        msgs.append({"role": "system", "content": "you are helpful"})
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"msg-{i}"})
    return msgs


def test_interrupted_tool_call_is_closed_before_a_later_turn():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "send_email", "arguments": "{}"},
                }
            ],
        },
        {"role": "user", "content": "next run"},
    ]

    _close_interrupted_tool_step(messages)

    assert [message["role"] for message in messages] == ["assistant", "tool", "user"]
    assert messages[1]["tool_call_id"] == "call-1"
    assert "interrupted" in messages[1]["content"]


def test_chat_request_identity_excludes_trusted_runtime_provenance():
    assert _chat_request_hash(
        session_id="sess-1",
        message="Run feed",
        skip_approvals=None,
        images=None,
        context=None,
    ) == _chat_request_hash(
        session_id="sess-1",
        message="Run feed",
        skip_approvals=False,
        images=[],
        context=[],
    )


class _StubExecutor:
    def __init__(self) -> None:
        from arden.tools.core.registry import ToolRegistry

        self.registry = ToolRegistry()
        self.tool_services: dict[str, object] = {}

    def get_tools(self) -> list[dict]:
        return []


class _StubSessionService:
    def __init__(
        self,
        messages: list[dict],
        session_id: str = "sess-1",
        last_input_tokens: int | None = None,
    ) -> None:
        self._messages = messages
        self._session_id = session_id
        self._last_input_tokens = last_input_tokens
        self.save_calls: list[tuple[list[dict], dict | None]] = []
        self.save_progress_calls: list[list[dict]] = []

    async def load(self, session_id: str | None = None) -> SessionData | None:
        state = SessionState(
            session_id=session_id or self._session_id,
            started_at=datetime.now(UTC),
        )
        return SessionData(state=state, messages=list(self._messages), last_input_tokens=self._last_input_tokens)

    def create(
        self,
        name: str | None = None,
        session_type: str = "chat",
        origin_automation_id: str | None = None,
    ) -> SessionState:
        return SessionState(
            session_id=self._session_id,
            started_at=datetime.now(UTC),
            name=name,
        )

    async def save_progress(self, session_state, messages: list[dict]) -> None:
        self.save_progress_calls.append(list(messages))

    async def save(self, session_state, messages: list[dict], metadata: dict | None = None) -> None:
        self._messages = list(messages)
        self._last_input_tokens = metadata.get("last_input_tokens") if metadata else None
        self.save_calls.append((list(messages), metadata))


class _LoopPreTurnCompactor:
    def __init__(self):
        self.seen: list[tuple[int, int | None]] = []

    def should_compact(self, messages: list[dict], model: str, last_input_tokens: int | None) -> bool:
        self.seen.append((len(messages), last_input_tokens))
        return True

    async def maybe_compact(
        self,
        messages: list[dict],
        model: str,
        last_input_tokens: int | None,
        *,
        rehydration_state: dict | None = None,
    ) -> list[dict] | None:
        return [
            messages[0],
            {"role": "assistant", "content": "[Session State Handoff]\nloop summary"},
            *messages[-4:],
        ]


def _make_deps(session_service: _StubSessionService) -> ChatDeps:
    return _make_deps_with_config(
        session_service,
        AgentConfig(
            model="gpt-5.2",
            research_model=None,
            max_depth=1,
            deferred_tools=False,
        ),
    )


def _make_deps_with_config(session_service: _StubSessionService, config: AgentConfig) -> ChatDeps:
    return ChatDeps(
        chat_model="gpt-5.2",
        agent_config=config,
        executor=_StubExecutor(),
        session_service=session_service,
        run_registry=RunRegistry(),
        available_integrations=[],
        integration_errors={},
    )


def _split_history(messages: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """Returns (system, prior_history, fresh_user_message)."""
    system = [m for m in messages if m.get("role") == "system"]
    user_added = messages[-1]
    middle = messages[len(system) : -1]
    return system, middle, user_added


def test_loop_iteration_history_window_constant_exists():
    # Sanity check that the budget is what the design calls for. If this
    # ever changes intentionally, update the test consciously.
    assert LOOP_ITERATION_HISTORY_WINDOW == 50


@pytest.mark.asyncio
async def test_prepare_chat_records_context_manifest_with_explicit_provenance():
    svc = _StubSessionService([])
    ctx = await prepare_chat(
        _make_deps(svc),
        message="Summarize this",
        session_id="sess-1",
        context=[
            {
                "type": "context",
                "content_type": "selected_text",
                "content": "Evidence",
                "metadata": {
                    "source": "desktop",
                    "ref": "selection:1",
                    "freshness": "now",
                    "selection_reason": "user selected",
                },
            }
        ],
    )

    selected = next(item for item in ctx.run.context_manifest if item["content_type"] == "selected_text")
    assert selected["source"] == "desktop"
    assert selected["ref"] == "selection:1"
    assert selected["selection_reason"] == "user selected"
    assert any(item["content_type"] == "system_prompt" for item in ctx.run.context_manifest)


@pytest.mark.asyncio
async def test_prepare_chat_uses_only_trusted_automation_authority():
    forged_client_id = "loop:feed-worker:1"
    forged = await prepare_chat(
        _make_deps(_StubSessionService([])),
        message="Run feed",
        session_id="sess-1",
        client_id=forged_client_id,
    )
    trusted = await prepare_chat(
        _make_deps(_StubSessionService([])),
        message="Run feed",
        session_id="sess-1",
        client_id=forged_client_id,
        loop_task_id="feed-worker",
        automation_id="feed-worker",
    )

    assert forged.run.loop_task_id is None
    assert forged.run.automation_id is None
    assert trusted.run.loop_task_id == "feed-worker"
    assert trusted.run.automation_id == "feed-worker"


@pytest.mark.asyncio
async def test_prepare_chat_adds_autonomous_guard_for_session_bound_loop():
    ctx = await prepare_chat(
        _make_deps(_StubSessionService([])),
        message="Process this event context",
        session_id="sess-1",
        loop_task_id="feed-worker",
        automation_id="feed-worker",
    )

    system_blocks = ctx.run.messages[0]["content"]
    assert sum(block["text"].count(AUTOMATION_SUFFIX) for block in system_blocks) == 1


@pytest.mark.asyncio
async def test_prepare_chat_resolves_daily_notes_tool_scope():
    deps = replace(
        _make_deps(_StubSessionService([])),
        executor=ToolExecutor(
            get_services=lambda: {
                "wiki": object(),
                "wiki_post_commit": object(),
            }
        ),
    )

    ctx = await prepare_chat(
        deps,
        message="/daily-notes",
        session_id="sess-1",
        loop_task_id="daily-notes",
        automation_id="daily-notes",
        tool_scope="daily_notes",
    )

    names = {schema["function"]["name"] for schema in ctx.tools}
    assert {"create_wiki_page", "edit_wiki_page"} <= names
    assert "archive_wiki_page" not in names


@pytest.mark.asyncio
async def test_prepare_resumed_chat_restores_run_without_appending_user_message():
    history = [
        {"role": "system", "content": "old"},
        {"role": "user", "content": "do it", "client_id": "client-1"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "write", "arguments": "{}"}}],
        },
    ]
    svc = _StubSessionService(history)
    deps = _make_deps(svc)

    ctx = await prepare_chat(
        deps,
        message="",
        session_id="sess-1",
        resume_run_id="original-run",
    )

    assert ctx.run.run_id == "original-run"
    assert [message["role"] for message in ctx.run.messages] == ["system", "user", "assistant"]
    assert ctx.run.client_id == "client-1"


@pytest.mark.asyncio
async def test_resume_suspended_chat_run_schedules_original_run(monkeypatch):
    from arden.services import chat as chat_service

    history = [
        {"role": "system", "content": "old"},
        {"role": "user", "content": "do it"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "write", "arguments": "{}"}}],
        },
    ]
    svc = _StubSessionService(history)

    class Store:
        async def get_chat_run(self, run_id):
            return {
                "run_id": run_id,
                "session_id": "sess-1",
                "status": "interrupted",
                "stop_reason": "server_restart",
                "metadata": {
                    "loop_task_id": "feed-worker",
                    "automation_id": "feed-worker",
                },
            }

        async def get_latest_session_event_seq(self, session_id):
            return 0

        async def get_latest_session_checkpoint_seq(self, session_id):
            return 0

    svc.store = Store()
    deps = _make_deps(svc)
    seen = []

    async def fake_run_chat(ctx, bus, buses):
        seen.append((ctx.run.run_id, ctx.run.loop_task_id, ctx.run.automation_id))

    monkeypatch.setattr(chat_service, "run_chat", fake_run_chat)

    result = await resume_suspended_chat_run(
        deps.run_registry,
        lambda: deps,
        BusRegistry(),
        run_id="original-run",
        session_id="sess-1",
        session_service=svc,
    )
    await deps.run_registry.get_run("original-run").task

    assert result == {"run_id": "original-run", "session_id": "sess-1", "status": "resumed"}
    assert seen == [("original-run", "feed-worker", "feed-worker")]


@pytest.mark.asyncio
async def test_restart_settles_automation_chat_that_cannot_resume():
    interrupted = {
        "run_id": "run-1",
        "session_id": "area-session",
        "metadata": {
            "loop_task_id": "area:health",
            "automation_id": "area:health",
        },
    }
    failures: list[RunFailed] = []
    sequence: list[str] = []

    class Store:
        async def list_interrupted_chat_runs(self):
            return [interrupted]

    class Outbox:
        async def handle_run_failed(self, event: RunFailed) -> None:
            failures.append(event)

    class Automations:
        async def bind_interrupted_chat_run(
            self,
            task_id: str,
            run_id: str,
            session_id: str,
        ) -> bool:
            sequence.append(f"bind:{task_id}:{run_id}:{session_id}")
            return True

    runtime = SimpleNamespace(
        session_service=SimpleNamespace(store=Store()),
        stores=SimpleNamespace(automations=Automations()),
        automation=SimpleNamespace(outbox_runtime=Outbox()),
    )

    async def cannot_resume(_run_id: str, _session_id: str):
        sequence.append("resume")
        return None

    await _recover_interrupted_chat_runs(runtime, cannot_resume)

    assert failures == [
        RunFailed(
            run_id="run-1",
            session_id="area-session",
            error="chat run could not resume after server restart",
            automation_task_id="area:health",
        )
    ]
    assert sequence == ["bind:area:health:run-1:area-session", "resume"]


@pytest.mark.asyncio
async def test_restart_binds_automation_before_resuming_chat():
    interrupted = {
        "run_id": "run-1",
        "session_id": "area-session",
        "metadata": {
            "loop_task_id": "area:health",
            "automation_id": "area:health",
        },
    }
    sequence: list[str] = []
    failures: list[RunFailed] = []

    class Store:
        async def list_interrupted_chat_runs(self):
            return [interrupted]

    class Automations:
        async def bind_interrupted_chat_run(self, task_id, run_id, session_id):
            sequence.append("bind")
            return True

        async def refresh_detached_chat_run(
            self,
            task_id,
            run_id,
            session_id,
            started_at,
        ):
            sequence.append("refresh")
            return True

    class Outbox:
        async def handle_run_failed(self, event: RunFailed) -> None:
            failures.append(event)

    runtime = SimpleNamespace(
        session_service=SimpleNamespace(store=Store()),
        stores=SimpleNamespace(automations=Automations()),
        automation=SimpleNamespace(outbox_runtime=Outbox()),
    )

    async def resume(run_id: str, session_id: str):
        sequence.append("resume")
        return {"run_id": run_id, "session_id": session_id, "status": "resumed"}

    await _recover_interrupted_chat_runs(runtime, resume)

    assert sequence == ["bind", "resume", "refresh"]
    assert failures == []


@pytest.mark.asyncio
async def test_loop_iteration_trims_history_to_window():
    # 60 prior messages (system + 60). Loop fire should leave system + last 50
    # prior msgs + the fresh user prompt = 52 entries.
    history = _make_history(60)
    svc = _StubSessionService(history)
    deps = _make_deps(svc)

    client_id = "loop:loop-shy-otter:7"
    ctx = await prepare_chat(
        deps,
        message="iteration prompt",
        skip_approvals=None,
        session_id="sess-1",
        client_id=client_id,
        loop_task_id="loop-shy-otter",
    )

    system, prior, user_msg = _split_history(ctx.run.messages)
    assert len(system) == 1
    assert len(prior) == LOOP_ITERATION_HISTORY_WINDOW
    # Kept the tail, not the head.
    assert prior[0]["content"] == "msg-10"
    assert prior[-1]["content"] == "msg-59"
    # The fresh loop-dispatched message is appended after the trimmed tail.
    assert user_msg["role"] == "user"
    assert user_msg.get("is_meta") is True
    assert user_msg["client_id"] == "loop:loop-shy-otter:7"


@pytest.mark.asyncio
async def test_loop_iteration_precompacts_full_history_before_trim():
    history = _make_history(60)
    compactor = _LoopPreTurnCompactor()
    emitted = []

    async def emit(event):
        emitted.append(event)

    svc = _StubSessionService(history, last_input_tokens=314_000)
    deps = _make_deps_with_config(
        svc,
        AgentConfig(
            model="gpt-5.2",
            research_model=None,
            max_depth=1,
            deferred_tools=False,
            compactor=compactor,
        ),
    )

    client_id = "loop:loop-compact:3"
    ctx = await prepare_chat(
        deps,
        message="iteration prompt",
        skip_approvals=None,
        session_id="sess-1",
        client_id=client_id,
        loop_task_id="loop-compact",
        emit=emit,
    )

    assert compactor.seen == [(len(history), 314_000)]
    assert [event.type.value for event in emitted] == ["compaction_started", "compaction_finished"]
    assert emitted[-1].messages_before == len(history)
    assert emitted[-1].messages_after == 6
    assert svc.save_calls
    compacted, metadata = svc.save_calls[-1]
    assert len(compacted) == 6
    assert metadata == {"last_input_tokens": None, "last_message_count": 6}
    assert ctx.initial_input_tokens is None
    assert ctx.run.history_prefix == []
    assert ctx.run.messages[-1]["client_id"] == client_id


@pytest.mark.asyncio
async def test_loop_iteration_under_window_keeps_all_history():
    # 30 prior messages — below the cap → no trimming.
    history = _make_history(30)
    svc = _StubSessionService(history)
    deps = _make_deps(svc)

    client_id = "loop:loop-a:1"
    ctx = await prepare_chat(
        deps,
        message="iter",
        skip_approvals=None,
        session_id="sess-1",
        client_id=client_id,
        loop_task_id="loop-a",
    )

    system, prior, _ = _split_history(ctx.run.messages)
    assert len(system) == 1
    assert len(prior) == 30
    assert prior[0]["content"] == "msg-0"
    assert prior[-1]["content"] == "msg-29"


@pytest.mark.asyncio
async def test_non_loop_chat_does_not_trim_history():
    # 60 prior messages but no loop client_id → full history visible.
    history = _make_history(60)
    svc = _StubSessionService(history)
    deps = _make_deps(svc)

    ctx = await prepare_chat(
        deps,
        message="hi there",
        skip_approvals=False,
        session_id="sess-1",
        client_id="user-cid-42",
    )

    system, prior, user_msg = _split_history(ctx.run.messages)
    assert len(system) == 1
    assert len(prior) == 60
    assert prior[0]["content"] == "msg-0"
    assert prior[-1]["content"] == "msg-59"
    # Not flagged as meta — this is a real user message.
    assert "is_meta" not in user_msg


@pytest.mark.asyncio
async def test_goal_meta_chat_does_not_use_loop_iteration_window():
    # Goal continuations are hidden/meta user messages, but they are not
    # scheduler-loop ticks. They need full history so normal compaction can
    # summarize old context instead of silently trimming it away.
    history = _make_history(60)
    svc = _StubSessionService(history)
    deps = _make_deps(svc)

    client_id = "goal:123"
    ctx = await prepare_chat(
        deps,
        message="Continue working toward this goal",
        skip_approvals=None,
        session_id="sess-1",
        client_id=client_id,
    )

    system, prior, user_msg = _split_history(ctx.run.messages)
    assert len(system) == 1
    assert len(prior) == 60
    assert ctx.run.history_prefix == []
    assert user_msg["client_id"] == client_id
    assert user_msg.get("is_meta") is True


@pytest.mark.asyncio
async def test_loop_iteration_stashes_prefix_for_persistence():
    # Trimming must not destroy disk history — the dropped head sits on
    # the run as `history_prefix` so save paths can re-prepend it.
    history = _make_history(60)
    svc = _StubSessionService(history)
    deps = _make_deps(svc)

    client_id = "loop:loop-c:1"
    ctx = await prepare_chat(
        deps,
        message="iter",
        skip_approvals=None,
        session_id="sess-1",
        client_id=client_id,
        loop_task_id="loop-c",
    )

    prefix = ctx.run.history_prefix
    # 60 prior + 1 system. system is preserved separately, then 50 of the
    # 60 non-system msgs go to the agent view → 10 stashed in the prefix.
    assert len(prefix) == 10
    assert prefix[0]["content"] == "msg-0"
    assert prefix[-1]["content"] == "msg-9"


@pytest.mark.asyncio
async def test_loop_iteration_save_progress_persists_full_history():
    # End-to-end: when a save path fires after a trim, the bytes that land
    # in session storage must be history_prefix + agent_view, i.e. the
    # full pre-trim history plus the fresh user message. Otherwise we'd
    # silently truncate disk history on every loop tick.
    history = _make_history(60)
    svc = _StubSessionService(history)
    deps = _make_deps(svc)

    client_id = "loop:loop-persist:1"
    ctx = await prepare_chat(
        deps,
        message="iter",
        skip_approvals=None,
        session_id="sess-1",
        client_id=client_id,
        loop_task_id="loop-persist",
    )

    # Sanity: the agent view was actually trimmed.
    assert len(ctx.run.history_prefix) == 10
    assert len(ctx.run.messages) < len(history) + 1

    # Simulate any save path firing (start_chat's pre-stream save,
    # _checkpoint, _save_snapshot, or the final save in `finally`).
    await deps.session_service.save_progress(ctx.session_state, _persistable_messages(ctx.run))

    assert svc.save_progress_calls, "save_progress should have been called"
    persisted = svc.save_progress_calls[-1]
    # 1 system + 60 prior + 1 fresh user msg = 62.
    assert len(persisted) == len(history) + 1
    assert persisted == [*ctx.run.history_prefix, *ctx.run.messages]
    # Every original prior message survives — none of msg-0..msg-59 went
    # missing from disk just because the agent view was trimmed.
    persisted_contents = {m.get("content") for m in persisted if isinstance(m.get("content"), str)}
    for i in range(60):
        assert f"msg-{i}" in persisted_contents, f"msg-{i} dropped from disk"
    # Tail is the fresh loop-dispatched user message.
    assert persisted[-1]["role"] == "user"
    assert persisted[-1].get("is_meta") is True


@pytest.mark.asyncio
async def test_non_loop_chat_leaves_history_prefix_empty():
    history = _make_history(60)
    svc = _StubSessionService(history)
    deps = _make_deps(svc)

    ctx = await prepare_chat(
        deps,
        message="hi",
        skip_approvals=False,
        session_id="sess-1",
        client_id="user-cid",
    )

    assert ctx.run.history_prefix == []


@pytest.mark.asyncio
async def test_loop_iteration_preserves_system_after_trim():
    # System message must survive even when the tail is trimmed aggressively.
    history = _make_history(60)
    # Make the system message recognizable so we can assert it's the same one.
    history[0]["content"] = "SENTINEL-SYSTEM-PROMPT"
    svc = _StubSessionService(history)
    deps = _make_deps(svc)

    client_id = "loop:loop-b:99"
    ctx = await prepare_chat(
        deps,
        message="iter",
        skip_approvals=None,
        session_id="sess-1",
        client_id=client_id,
        loop_task_id="loop-b",
    )

    # The system message is rebuilt by _prepare_messages from system_blocks,
    # so the *content* won't match the sentinel — but it must still be
    # present at index 0 and the trimmed tail must not contain a leftover
    # system row.
    assert ctx.run.messages[0]["role"] == "system"
    other_systems = [m for m in ctx.run.messages[1:] if m.get("role") == "system"]
    assert other_systems == []
    # And the prior-history portion is exactly the cap.
    _, prior, _ = _split_history(ctx.run.messages)
    assert len(prior) == LOOP_ITERATION_HISTORY_WINDOW


def test_trim_expands_when_cut_lands_inside_tool_sequence(monkeypatch):
    # Naive cut at the WINDOW boundary would orphan a tool_result whose
    # parent assistant (with tool_calls) sits one slot earlier. The trim
    # must walk the boundary backward to a clean split so OpenAI doesn't
    # reject the request with "No tool call found for function call output".
    monkeypatch.setattr("arden.services.chat.LOOP_ITERATION_HISTORY_WINDOW", 2)
    history = [
        {"role": "user", "content": "kick it off"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_A", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call_A", "content": "result-A"},
        {"role": "tool", "tool_call_id": "call_A", "content": "result-A2"},
        {"role": "user", "content": "next"},
    ]

    prefix, view = _trim_for_loop_iteration(history)

    # Naive WINDOW=2 would have given view=[tool_result_B, user2] with an
    # orphan tool_result at the head. After expansion the view must keep
    # the entire tool sequence intact, starting at a clean boundary (the
    # user message that opened the turn).
    assert view[0]["role"] == "user"
    assert view[0]["content"] == "kick it off"
    assert view == history
    assert prefix == []


def test_trim_no_expansion_when_cut_lands_on_clean_assistant(monkeypatch):
    # Cut lands on an assistant with no tool_calls — already a clean
    # boundary, no expansion needed. Tail is exactly WINDOW messages.
    monkeypatch.setattr("arden.services.chat.LOOP_ITERATION_HISTORY_WINDOW", 2)
    history = [
        {"role": "user", "content": "user1"},
        {"role": "assistant", "content": "asst1"},
        {"role": "user", "content": "user2"},
        {"role": "assistant", "content": "asst2"},
        {"role": "user", "content": "user3"},
        {"role": "assistant", "content": "asst3"},
    ]

    prefix, view = _trim_for_loop_iteration(history)

    assert view == [
        {"role": "user", "content": "user3"},
        {"role": "assistant", "content": "asst3"},
    ]
    assert prefix == history[:-2]


def test_trim_no_expansion_when_cut_lands_on_user(monkeypatch):
    # Cut lands on a user message — clean boundary, no expansion.
    monkeypatch.setattr("arden.services.chat.LOOP_ITERATION_HISTORY_WINDOW", 2)
    history = [
        {"role": "assistant", "content": "asst1"},
        {"role": "user", "content": "user2"},
        {"role": "assistant", "content": "asst2"},
    ]

    prefix, view = _trim_for_loop_iteration(history)

    assert view == [
        {"role": "user", "content": "user2"},
        {"role": "assistant", "content": "asst2"},
    ]
    assert prefix == [{"role": "assistant", "content": "asst1"}]


@pytest.mark.asyncio
async def test_loop_iteration_fires_uncompacted_when_precompaction_fails():
    # Pre-run compaction is maintenance, not a precondition: if the summarize
    # call blows up (e.g. its request exceeds the model window), the fire must
    # proceed on the trimmed view — raising would brick the loop for good,
    # replaying the same oversized history into the same error every hour.
    class _ExplodingCompactor:
        def should_compact(self, messages, model, last_input_tokens):
            return True

        async def maybe_compact(self, messages, model, last_input_tokens, *, rehydration_state=None):
            raise RuntimeError("APIError: Your input exceeds the context window of this model.")

    history = _make_history(60)
    emitted = []

    async def emit(event):
        emitted.append(event)

    svc = _StubSessionService(history, last_input_tokens=314_000)
    deps = _make_deps_with_config(
        svc,
        AgentConfig(
            model="gpt-5.2",
            research_model=None,
            max_depth=1,
            deferred_tools=False,
            compactor=_ExplodingCompactor(),
        ),
    )

    client_id = "loop:loop-compact:4"
    ctx = await prepare_chat(
        deps,
        message="iteration prompt",
        skip_approvals=None,
        session_id="sess-1",
        client_id=client_id,
        loop_task_id="loop-compact",
        emit=emit,
    )

    assert [event.type.value for event in emitted] == ["compaction_started", "compaction_finished"]
    assert emitted[-1].messages_before == len(history)
    assert emitted[-1].messages_after == len(history)
    # Nothing was rewritten on disk, and the fire still assembled a run.
    assert svc.save_calls == []
    assert ctx.run.messages[-1]["client_id"] == client_id
