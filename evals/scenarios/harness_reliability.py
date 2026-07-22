import json
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import arden.database as database
from arden.agent import (
    Agent,
    AgentHooks,
    Choice,
    CompletionResponse,
    FunctionCall,
    Message,
    Role,
    ToolCall,
    ToolCompleted,
    ToolEffect,
    ToolError,
    ToolMeta,
    ToolOutcome,
    ToolOutcomeStatus,
    ToolResult,
    ToolVerification,
    Usage,
)
from arden.context.store import SessionStore


@dataclass(frozen=True)
class JourneyResult:
    name: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, str | bool]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


class _ScriptedModel:
    def __init__(self, responses: list[CompletionResponse]):
        self.responses = list(responses)

    async def stream(
        self, messages, model, tools, tool_choice=None, reasoning_effort=None
    ):
        if not self.responses:
            raise RuntimeError("scripted model exhausted")
        response = self.responses.pop(0)
        if response.choices[0].message.content:
            yield response.choices[0].message.content
        yield response


class _FakeTools:
    def __init__(
        self,
        results: dict[str, ToolResult],
        *,
        allowed: set[str] | None = None,
        approved_call_ids: set[str] | None = None,
    ):
        self.results = results
        self.allowed = allowed
        self.approved_call_ids = approved_call_ids
        self.calls: list[tuple[str, dict, str]] = []

    async def execute(self, name: str, args: dict, tool_call_id: str) -> ToolResult:
        if self.allowed is not None and name not in self.allowed:
            return ToolResult.failure(
                code="permission_denied",
                message=f"Tool {name} is outside this run's scope.",
                preview="Denied",
                status=ToolOutcomeStatus.DENIED,
            )
        if (
            self.approved_call_ids is not None
            and tool_call_id not in self.approved_call_ids
        ):
            return ToolResult.failure(
                code="approval_required",
                message="Mutation was not approved.",
                preview="Approval required",
                status=ToolOutcomeStatus.DENIED,
            )
        self.calls.append((name, args, tool_call_id))
        return self.results[name]

    def get_meta(self, name: str) -> ToolMeta:
        return ToolMeta(name=name, display_name=name)


def _tool_call(call_id: str, name: str, arguments: dict | str) -> ToolCall:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return ToolCall(
        id=call_id, type="function", function=FunctionCall(name=name, arguments=raw)
    )


def _response(
    *, text: str | None = None, calls: list[ToolCall] | None = None
) -> CompletionResponse:
    return CompletionResponse(
        choices=[
            Choice(
                message=Message(
                    role=Role.ASSISTANT,
                    content=text,
                    tool_calls=calls,
                    reasoning_content=None,
                ),
                finish_reason=None,
            )
        ],
        usage=Usage(),
        model="eval",
    )


async def _agent_events(
    responses: list[CompletionResponse],
    tools: _FakeTools,
    *,
    messages: list[dict] | None = None,
    hooks: AgentHooks | None = None,
) -> tuple[list[object], list[dict]]:
    transcript = messages or [
        {"role": "system", "content": "eval"},
        {"role": "user", "content": "run journey"},
    ]
    schemas = [
        {
            "type": "function",
            "function": {"name": name, "description": name, "parameters": {}},
        }
        for name in tools.results
    ]
    agent = Agent(
        tools=schemas,
        client=_ScriptedModel(responses),
        executor=tools,
        model="eval",
        hooks=hooks,
    )
    return [event async for event in agent.stream(transcript)], transcript


def _completed(events: list[object]) -> list[ToolCompleted]:
    return [event for event in events if isinstance(event, ToolCompleted)]


async def _approved_mutation() -> None:
    outcome = ToolOutcome(
        status=ToolOutcomeStatus.SUCCEEDED,
        effect=ToolEffect(operation="send", target="message:42"),
        verification=ToolVerification(
            postcondition="message exists",
            observed="message:42 fetched",
            confidence=1.0,
        ),
        receipt="receipt-42",
    )
    tools = _FakeTools(
        {"send": ToolResult(content="sent", preview="sent", outcome=outcome)},
        approved_call_ids={"send-1"},
    )
    events, _ = await _agent_events(
        [
            _response(calls=[_tool_call("send-1", "send", {"body": "hello"})]),
            _response(text="done"),
        ],
        tools,
    )
    assert tools.calls == [("send", {"body": "hello"}, "send-1")]
    assert _completed(events)[0].outcome == outcome


async def _malformed_repair() -> None:
    tools = _FakeTools({"lookup": ToolResult(content="found", preview="found")})
    events, transcript = await _agent_events(
        [
            _response(calls=[_tool_call("bad-1", "lookup", "{bad json}")]),
            _response(calls=[_tool_call("good-1", "lookup", {"query": "fixed"})]),
            _response(text="done"),
        ],
        tools,
    )
    assert tools.calls == [("lookup", {"query": "fixed"}, "good-1")]
    assert _completed(events)[0].outcome.error.code == "invalid_tool_arguments"
    assert any("invalid_tool_arguments" in row.get("content", "") for row in transcript)


async def _scope_denial() -> None:
    tools = _FakeTools(
        {
            "allowed": ToolResult(content="ok", preview="ok"),
            "forbidden": ToolResult(content="bad", preview="bad"),
        },
        allowed={"allowed"},
    )
    events, _ = await _agent_events(
        [
            _response(calls=[_tool_call("scope-1", "forbidden", {})]),
            _response(text="done"),
        ],
        tools,
    )
    completed = _completed(events)[0]
    assert tools.calls == []
    assert completed.outcome.status == ToolOutcomeStatus.DENIED


async def _restart_resume() -> None:
    calls = [
        _tool_call("done-1", "write", {"value": 1}),
        _tool_call("new-1", "write", {"value": 2}),
    ]
    transcript = [
        {"role": "system", "content": "eval"},
        {"role": "user", "content": "write both"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": call.type,
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in calls
            ],
        },
    ]

    async def recover(pending):
        assert [item.tool_call.id for item in pending] == ["done-1", "new-1"]
        return {"done-1": ToolResult(content="durable", preview="durable")}

    tools = _FakeTools({"write": ToolResult(content="new", preview="new")})
    _, transcript = await _agent_events(
        [_response(text="resumed")],
        tools,
        messages=transcript,
        hooks=AgentHooks(recover_tool_calls=recover),
    )
    assert tools.calls == [("write", {"value": 2}, "new-1")]
    assert [row["tool_call_id"] for row in transcript if row["role"] == "tool"] == [
        "done-1",
        "new-1",
    ]


async def _background_exactly_once() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "eval.db"
        conn = await database.connect(path)
        read_conn = await database.connect(path, readonly=True)
        try:
            store = SessionStore(conn, read_conn)
            await store.init_schema()
            await store.record_background_agent_started(
                task_id="bg-1",
                session_id="session-1",
                parent_run_id="run-1",
                command="research",
            )
            first = await store.claim_background_agent_completion(
                task_id="bg-1",
                session_id="session-1",
                status="completed",
                completion_id="completion-1",
                result_text="authoritative result",
            )
            second = await store.claim_background_agent_completion(
                task_id="bg-1",
                session_id="session-1",
                status="failed",
                completion_id="completion-2",
                result_text="duplicate",
            )
            assert first["claimed"] is True
            assert second["claimed"] is False
            assert second["completion_id"] == "completion-1"
            assert (
                await store.get_background_agent_result("session-1", "bg-1")
                == "authoritative result"
            )
            assert len(await store.list_undelivered_background_completions()) == 1
        finally:
            await read_conn.close()
            await conn.close()


async def _failed_postcondition() -> None:
    outcome = ToolOutcome(
        status=ToolOutcomeStatus.FAILED,
        error=ToolError(code="postcondition_failed", retryable=False),
        effect=ToolEffect(operation="update", target="record:1"),
        verification=ToolVerification(
            postcondition="record contains new value",
            observed="old value remained",
            confidence=1.0,
        ),
    )
    tools = _FakeTools(
        {
            "update": ToolResult(
                content="verification failed",
                preview="failed",
                is_error=True,
                outcome=outcome,
            )
        }
    )
    events, _ = await _agent_events(
        [
            _response(calls=[_tool_call("verify-1", "update", {})]),
            _response(text="reported failure"),
        ],
        tools,
    )
    assert _completed(events)[0].outcome == outcome


async def _partial_completion() -> None:
    tools = _FakeTools(
        {
            "first": ToolResult(
                content="done",
                preview="done",
                outcome=ToolOutcome(
                    status=ToolOutcomeStatus.SUCCEEDED, receipt="receipt-1"
                ),
            ),
            "second": ToolResult.failure(
                code="provider_error",
                message="failed",
                preview="failed",
                retryable=True,
            ),
        }
    )
    events, _ = await _agent_events(
        [
            _response(
                calls=[
                    _tool_call("part-1", "first", {}),
                    _tool_call("part-2", "second", {}),
                ]
            ),
            _response(text="partially complete"),
        ],
        tools,
    )
    statuses = {event.tool_id: event.outcome.status for event in _completed(events)}
    assert statuses == {
        "part-1": ToolOutcomeStatus.SUCCEEDED,
        "part-2": ToolOutcomeStatus.FAILED,
    }


_JOURNEYS: tuple[tuple[str, Callable[[], Awaitable[None]]], ...] = (
    ("approved_mutation", _approved_mutation),
    ("malformed_repair", _malformed_repair),
    ("scope_denial", _scope_denial),
    ("restart_resume", _restart_resume),
    ("background_exactly_once", _background_exactly_once),
    ("failed_postcondition", _failed_postcondition),
    ("partial_completion", _partial_completion),
)


async def run_harness_reliability_journeys() -> list[JourneyResult]:
    results: list[JourneyResult] = []
    for name, journey in _JOURNEYS:
        try:
            await journey()
        except Exception as exc:
            results.append(
                JourneyResult(
                    name=name, passed=False, detail=f"{type(exc).__name__}: {exc}"
                )
            )
        else:
            results.append(JourneyResult(name=name, passed=True))
    return results
