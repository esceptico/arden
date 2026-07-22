from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ntrp.commands.models import CommandOutcome, CommandRunRequest
from ntrp.server.routers.commands import start_command_run


def test_command_outcome_rejects_arbitrary_url_destination():
    with pytest.raises(ValidationError):
        CommandOutcome.model_validate(
            {
                "status": "completed",
                "summary": "Opened it",
                "destination": {"kind": "url", "url": "https://example.com"},
            }
        )


def test_command_outcome_accepts_typed_automation_destination():
    outcome = CommandOutcome.model_validate(
        {
            "status": "completed",
            "summary": "Opened email automation",
            "destination": {"kind": "automation", "task_id": "email-digest"},
        }
    )

    assert outcome.destination.kind == "automation"
    assert outcome.destination.task_id == "email-digest"


class _SessionService:
    def __init__(self):
        self.saved = {}

    async def load(self, session_id):
        return self.saved.get(session_id)

    def create(self, **kwargs):
        return SimpleNamespace(**kwargs, name=None, chat_model=None)

    async def save(self, state, messages):
        self.saved[state.session_id] = SimpleNamespace(state=state, messages=messages)


class _Executor:
    def get_tools(self, *, command_eligible=None):
        assert command_eligible is True
        return [
            {"type": "function", "function": {"name": "list_automations"}},
            {"type": "function", "function": {"name": "update_automation"}},
            {"type": "function", "function": {"name": "tool_search"}},
        ]


@pytest.mark.asyncio
async def test_start_command_run_creates_hidden_scoped_agent_session(monkeypatch):
    submitted = []

    async def fake_submit(*args, **kwargs):
        submitted.append(kwargs)
        return {"run_id": "run-1", "session_id": kwargs["session_id"], "status": "running"}

    monkeypatch.setattr("ntrp.server.routers.commands.submit_chat_message", fake_submit)
    sessions = _SessionService()
    runtime = SimpleNamespace(
        session_service=sessions,
        executor=_Executor(),
        run_registry=object(),
        build_chat_deps=lambda **kwargs: object(),
        resolve_session_chat_model=lambda _session_id: None,
    )
    request = CommandRunRequest(query="pause email automation", client_id="command-1")

    response = await start_command_run(request, runtime=runtime, buses=object())

    assert response.run_id == "run-1"
    assert response.session_id.startswith("command_")
    state = sessions.saved[response.session_id].state
    assert state.session_type == "agent"
    assert state.agent_type == "command_sidecar"
    assert submitted[0]["tool_scope"] == (
        "list_automations",
        "tool_search",
        "update_automation",
    )
    assert submitted[0]["output_schema"] is CommandOutcome
    assert submitted[0]["skip_approvals"] is False


@pytest.mark.asyncio
async def test_start_command_run_reuses_stable_session(monkeypatch):
    async def fake_submit(*args, **kwargs):
        return {"run_id": "run-1", "session_id": kwargs["session_id"], "status": "running"}

    monkeypatch.setattr("ntrp.server.routers.commands.submit_chat_message", fake_submit)
    sessions = _SessionService()
    runtime = SimpleNamespace(
        session_service=sessions,
        executor=_Executor(),
        run_registry=object(),
        build_chat_deps=lambda **kwargs: object(),
        resolve_session_chat_model=lambda _session_id: None,
    )
    request = CommandRunRequest(query="open automations", client_id="command-1")

    first = await start_command_run(request, runtime=runtime, buses=object())
    second = await start_command_run(request, runtime=runtime, buses=object())

    assert first == second
    assert len(sessions.saved) == 1
