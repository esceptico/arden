"""Scored tool-selection journeys over the real Agent and tool executor.

The default cases are scripted and deterministic: they verify the workflow
runner and scoring contracts, not a provider model's semantic quality. Set
``ARDEN_TOOL_HARNESS_MODEL`` to opt into three fresh provider-backed trials.
"""

import ast
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import monotonic
from typing import Any

import pytest
from pydantic import BaseModel, Field

from arden.agent import Agent, Result, StopReason, ToolCompleted, ToolResult, ToolStarted
from arden.config import get_config
from arden.constants import OFFLOAD_THRESHOLD, RAW_TOOL_RESULT_DATA_KEY
from arden.core import raw_tool_results, tool_result_files
from arden.core.llm_client import llm_client
from arden.llm import router as llm_router
from arden.tools.core import EmptyInput, Tool, ToolAction, ToolPolicy, ToolScope, tool
from arden.tools.core.context import ToolExecution
from tests.helpers import (
    MockCompletionClient,
    MockLLMClient,
    make_executor,
    make_test_executor,
    make_text_response,
    make_tool_response,
)


@dataclass(frozen=True)
class RecoveryPath:
    failed_tool: str
    error_code: str
    recovery_tool: str
    retry_tool: str


@dataclass(frozen=True)
class WorkflowContract:
    name: str
    prompt: str
    expected_calls: tuple[str, ...]
    expected_arguments: tuple[dict[str, Any], ...]
    expected_result_patterns: tuple[str, ...]
    allowed_tools: frozenset[str]
    answer_pattern: str
    recoveries: tuple[RecoveryPath, ...] = ()
    max_tool_calls: int = 4
    max_steps: int = 4
    max_payload_bytes: int = OFFLOAD_THRESHOLD
    max_answer_bytes: int = 100
    require_offload: bool = False


@dataclass(frozen=True)
class ToolAttempt:
    tool_id: str
    name: str
    args: dict[str, Any]
    started_at: int
    completed_at: int | None
    completed: ToolCompleted | None

    @property
    def error_code(self) -> str | None:
        outcome = self.completed.outcome if self.completed else None
        return outcome.error.code if outcome and outcome.error else None

    @property
    def succeeded(self) -> bool:
        return self.completed is not None and not self.completed.is_error


@dataclass(frozen=True)
class WorkflowMetrics:
    tool_calls: tuple[str, ...]
    argument_mismatches: tuple[int, ...]
    result_mismatches: tuple[int, ...]
    pairing_errors: tuple[str, ...]
    wrong_tool_calls: int
    retries: int
    failures: tuple[str, ...]
    offloaded_results: int
    max_result_payload_bytes: int
    max_model_message_bytes: int
    final_answer_bytes: int
    model_steps: int
    duration_ms: int
    usage: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_calls": list(self.tool_calls),
            "argument_mismatches": list(self.argument_mismatches),
            "result_mismatches": list(self.result_mismatches),
            "pairing_errors": list(self.pairing_errors),
            "wrong_tool_calls": self.wrong_tool_calls,
            "retries": self.retries,
            "failures": list(self.failures),
            "offloaded_results": self.offloaded_results,
            "max_result_payload_bytes": self.max_result_payload_bytes,
            "max_model_message_bytes": self.max_model_message_bytes,
            "final_answer_bytes": self.final_answer_bytes,
            "model_steps": self.model_steps,
            "duration_ms": self.duration_ms,
            "usage": self.usage,
        }


@dataclass(frozen=True)
class WorkflowReport:
    scenario: str
    score: int
    checks: dict[str, bool]
    metrics: WorkflowMetrics
    violations: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return all(self.checks.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "score": self.score,
            "passed": self.passed,
            "checks": self.checks,
            "metrics": self.metrics.to_dict(),
            "violations": list(self.violations),
        }


@dataclass(frozen=True)
class WorkflowRun:
    report: WorkflowReport
    attempts: tuple[ToolAttempt, ...]
    pairing_errors: tuple[str, ...]
    messages: tuple[dict, ...]
    result: Result


@dataclass
class _RecordStore:
    read_refs: set[str] = field(default_factory=set)
    update_attempts: int = 0


class RecordRefInput(BaseModel):
    ref: str = Field(min_length=1, max_length=128)


class RecordSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=128)


class RecordUpdateInput(BaseModel):
    ref: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=128)


_READ_POLICY = ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL)
_EXPORT_POLICY = ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, offload=False)
_WRITE_POLICY = ToolPolicy(
    action=ToolAction.WRITE,
    scope=ToolScope.INTERNAL,
    destructive=True,
    open_world=False,
    idempotent=True,
)


def _record_tools(store: _RecordStore) -> dict[str, Tool]:
    async def read_record(_execution: ToolExecution, args: RecordRefInput) -> ToolResult:
        store.read_refs.add(args.ref)
        return ToolResult(content=f"{args.ref} is current at revision 7.", preview=f"Read {args.ref}")

    async def search_records(_execution: ToolExecution, args: RecordSearchInput) -> ToolResult:
        return ToolResult(content=f"Search matches for {args.query}: stable-7, incident-42.", preview="2 matches")

    async def update_record(_execution: ToolExecution, args: RecordUpdateInput) -> ToolResult:
        store.update_attempts += 1
        if store.update_attempts == 1 or args.ref not in store.read_refs:
            return ToolResult.failure(
                code="fresh_read_required",
                message=f"Read {args.ref} before updating it, then retry once.",
                preview="Fresh read required",
                retryable=True,
                recovery_action=f"Call record_read for {args.ref}, then retry record_update once.",
            )
        return ToolResult(content=f"{args.ref} updated to {args.value}.", preview=f"Updated {args.ref}")

    async def export_record(_execution: ToolExecution, _args: EmptyInput) -> ToolResult:
        return ToolResult(
            content="export ready\n" + ("Ω" * 80_000),
            preview="Export ready",
        )

    return {
        "record_read": tool(
            display_name="Read Record",
            description="Read one record by its exact ref. Prefer this over broad search when a ref is known.",
            input_model=RecordRefInput,
            policy=_READ_POLICY,
            execute=read_record,
        ),
        "record_search": tool(
            display_name="Search Records",
            description="Search record titles when no exact ref is known. Do not use for a known ref.",
            input_model=RecordSearchInput,
            policy=_READ_POLICY,
            execute=search_records,
        ),
        "record_update": tool(
            display_name="Update Record",
            description="Update one record by exact ref. A fresh-read error requires record_read before one retry.",
            input_model=RecordUpdateInput,
            policy=_WRITE_POLICY,
            execute=update_record,
        ),
        "record_export": tool(
            display_name="Export Record",
            description="Export the requested record as a potentially large exact report.",
            policy=_EXPORT_POLICY,
            execute=export_record,
        ),
    }


def _make_agent(client: Any, tools: dict[str, Tool], *, model: str = "test-model") -> Agent:
    executor = make_executor(tools)
    return Agent(
        tools=executor.get_tools(),
        client=client,
        executor=make_test_executor(executor),
        model=model,
        max_iterations=6,
        max_tool_calls=6,
    )


def _attempts(events: list[Any]) -> tuple[tuple[ToolAttempt, ...], tuple[str, ...]]:
    starts: list[tuple[int, ToolStarted]] = []
    first_start: dict[str, tuple[int, ToolStarted]] = {}
    completions: dict[str, tuple[int, ToolCompleted]] = {}
    errors: list[str] = []
    for index, event in enumerate(events):
        if isinstance(event, ToolStarted):
            starts.append((index, event))
            if event.tool_id in first_start:
                errors.append(f"duplicate start id: {event.tool_id}")
            else:
                first_start[event.tool_id] = (index, event)
        elif isinstance(event, ToolCompleted):
            if event.tool_id in completions:
                errors.append(f"duplicate completion id: {event.tool_id}")
                continue
            completions[event.tool_id] = (index, event)
            started = first_start.get(event.tool_id)
            if started is None:
                errors.append(f"completion without start: {event.tool_id}")
            elif started[1].name != event.name:
                errors.append(f"completion name mismatch: {event.tool_id}")

    attempts: list[ToolAttempt] = []
    for started_at, started in starts:
        is_first = first_start.get(started.tool_id, (None, None))[0] == started_at
        completion_pair = completions.get(started.tool_id) if is_first else None
        if completion_pair is None:
            errors.append(f"missing completion: {started.tool_id}")
        attempts.append(
            ToolAttempt(
                tool_id=started.tool_id,
                name=started.name,
                args=started.args,
                started_at=started_at,
                completed_at=completion_pair[0] if completion_pair else None,
                completed=completion_pair[1] if completion_pair else None,
            )
        )
    return tuple(attempts), tuple(errors)


def _result_payload_bytes(completed: ToolCompleted) -> int:
    result = ToolResult(
        content=completed.result,
        preview=completed.preview,
        is_error=completed.is_error,
        data=completed.data,
        model_content=completed.model_content,
        source_refs=completed.source_refs,
        outcome=completed.outcome,
    ).with_default_outcome()
    return len(result.serialized_payload().encode("utf-8"))


def _model_message_bytes(messages: list[dict]) -> int:
    sizes = [
        len(json.dumps(message, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8"))
        for message in messages
        if str(message.get("role")) == "tool"
    ]
    return max(sizes, default=0)


def _valid_offload(completed: ToolCompleted) -> bool:
    blob = raw_tool_results.internal_blob_from_data(completed.data)
    data = completed.data or {}
    if (
        blob is None
        or blob.compression != "gzip"
        or data.get("truncated") is not True
        or data.get("raw_ref") != blob.blob_ref
        or data.get("raw_bytes") != blob.content_bytes
        or completed.model_content
    ):
        return False
    continuation = re.search(r"Use file_read\(path=(?P<path>.+?), offset=", completed.result)
    if continuation is None:
        return False
    try:
        blob_path = Path(blob.blob_path).resolve(strict=True)
        blob_path.relative_to(raw_tool_results.RAW_TOOL_RESULTS_BASE.resolve())
        payload_path_value = ast.literal_eval(continuation.group("path"))
        if not isinstance(payload_path_value, str):
            return False
        payload_path = Path(payload_path_value).resolve(strict=True)
        payload_path.relative_to(tool_result_files.RESULTS_BASE.resolve())
        payload = raw_tool_results.read_raw_tool_result(str(blob_path), compression=blob.compression)
        encoded = payload.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        decoded = ToolResult.from_serialized_payload(payload)
        return (
            blob.blob_ref == f"sha256:{digest}"
            and blob.content_sha256 == digest
            and blob.content_bytes == len(encoded)
            and blob.content_bytes > OFFLOAD_THRESHOLD
            and blob.stored_bytes == blob_path.stat().st_size
            and payload_path.name == f"{completed.tool_id}.payload.json"
            and payload_path.read_text(encoding="utf-8") == payload
            and decoded is not None
            and decoded.preview == completed.preview
            and decoded.is_error == completed.is_error
            and decoded.outcome == completed.outcome
            and completed.result.startswith(decoded.content[: min(256, len(decoded.content))])
        )
    except (OSError, SyntaxError, ValueError):
        return False


def _retry_count(attempts: tuple[ToolAttempt, ...]) -> int:
    last_failed_at: dict[str, int] = {}
    retries = 0
    for attempt in attempts:
        if last_failed_at.get(attempt.name, attempt.started_at) < attempt.started_at:
            retries += 1
        if attempt.completed is not None and attempt.completed_at is not None:
            if attempt.completed.is_error:
                last_failed_at[attempt.name] = attempt.completed_at
            else:
                last_failed_at.pop(attempt.name, None)
    return retries


def _has_recovery(attempts: tuple[ToolAttempt, ...], path: RecoveryPath) -> bool:
    for failed_index, failed in enumerate(attempts):
        if failed.name != path.failed_tool or failed.error_code != path.error_code or failed.completed_at is None:
            continue
        for recovery_index in range(failed_index + 1, len(attempts)):
            recovery = attempts[recovery_index]
            if (
                recovery.name != path.recovery_tool
                or not recovery.succeeded
                or recovery.completed_at is None
                or failed.completed_at >= recovery.started_at
            ):
                continue
            if any(
                retry.name == path.retry_tool and retry.succeeded and recovery.completed_at < retry.started_at
                for retry in attempts[recovery_index + 1 :]
            ):
                return True
    return False


def _score(
    contract: WorkflowContract,
    attempts: tuple[ToolAttempt, ...],
    pairing_errors: tuple[str, ...],
    messages: list[dict],
    result: Result,
    duration_ms: int,
) -> WorkflowReport:
    call_names = tuple(attempt.name for attempt in attempts)
    actual_arguments = tuple(attempt.args for attempt in attempts)
    argument_mismatches = tuple(
        index
        for index in range(max(len(actual_arguments), len(contract.expected_arguments)))
        if index >= len(actual_arguments)
        or index >= len(contract.expected_arguments)
        or actual_arguments[index] != contract.expected_arguments[index]
    )
    result_mismatches = tuple(
        index
        for index in range(max(len(attempts), len(contract.expected_result_patterns)))
        if index >= len(attempts)
        or index >= len(contract.expected_result_patterns)
        or attempts[index].completed is None
        or re.search(
            contract.expected_result_patterns[index],
            attempts[index].completed.result,
            flags=re.IGNORECASE,
        )
        is None
    )
    wrong_names = tuple(name for name in call_names if name not in contract.allowed_tools)
    failures = tuple(
        f"{attempt.name}:{attempt.error_code or 'unknown'}"
        for attempt in attempts
        if attempt.completed is not None and attempt.completed.is_error
    )
    completed = tuple(attempt.completed for attempt in attempts if attempt.completed is not None)
    payload_sizes = tuple(_result_payload_bytes(event) for event in completed)
    offloaded = sum(_valid_offload(event) for event in completed)
    retries = _retry_count(attempts)
    allowed_failures = {(path.failed_tool, path.error_code) for path in contract.recoveries}
    unexpected_failures = tuple(
        f"{attempt.name}:{attempt.error_code or 'unknown'}"
        for attempt in attempts
        if attempt.completed is not None
        and attempt.completed.is_error
        and (attempt.name, attempt.error_code) not in allowed_failures
    )
    missing_recoveries = tuple(path for path in contract.recoveries if not _has_recovery(attempts, path))
    answer_bytes = len(result.text.encode("utf-8"))
    checks = {
        "selection": bool(call_names) and call_names[0] == contract.expected_calls[0] and not wrong_names,
        "workflow": (
            call_names == contract.expected_calls
            and not argument_mismatches
            and not result_mismatches
            and not pairing_errors
            and len(call_names) <= contract.max_tool_calls
            and len(completed) == len(attempts)
        ),
        "recovery": (
            retries == len(contract.recoveries)
            and not missing_recoveries
            and not unexpected_failures
            and not pairing_errors
        ),
        "bounds": (
            result.steps <= contract.max_steps
            and max(payload_sizes, default=0) <= contract.max_payload_bytes
            and _model_message_bytes(messages) <= contract.max_payload_bytes
            and (not contract.require_offload or offloaded > 0)
        ),
        "answer": (
            result.stop_reason is StopReason.END_TURN
            and answer_bytes <= contract.max_answer_bytes
            and re.fullmatch(contract.answer_pattern, result.text.strip(), flags=re.IGNORECASE) is not None
        ),
    }
    violations: list[str] = []
    if not checks["selection"]:
        violations.append(f"wrong first/extra tool: calls={call_names!r}, wrong={wrong_names!r}")
    if not checks["workflow"]:
        violations.append(
            f"call sequence {call_names!r} != {contract.expected_calls!r}; "
            f"argument mismatch indexes={argument_mismatches!r}; "
            f"result mismatch indexes={result_mismatches!r}; pairing={pairing_errors!r}"
        )
    if not checks["recovery"]:
        violations.append(
            f"retries={retries}, missing_recoveries={len(missing_recoveries)}, "
            f"unexpected_failures={unexpected_failures!r}"
        )
    if not checks["bounds"]:
        violations.append(
            f"steps={result.steps}, payload_bytes={max(payload_sizes, default=0)}, "
            f"message_bytes={_model_message_bytes(messages)}, offloads={offloaded}"
        )
    if not checks["answer"]:
        violations.append(
            f"stop={result.stop_reason.value}, answer_bytes={answer_bytes}, pattern={contract.answer_pattern!r}"
        )

    metrics = WorkflowMetrics(
        tool_calls=call_names,
        argument_mismatches=argument_mismatches,
        result_mismatches=result_mismatches,
        pairing_errors=pairing_errors,
        wrong_tool_calls=len(wrong_names),
        retries=retries,
        failures=failures,
        offloaded_results=offloaded,
        max_result_payload_bytes=max(payload_sizes, default=0),
        max_model_message_bytes=_model_message_bytes(messages),
        final_answer_bytes=answer_bytes,
        model_steps=result.steps,
        duration_ms=duration_ms,
        usage=result.usage.to_dict(),
    )
    return WorkflowReport(
        scenario=contract.name,
        score=20 * sum(checks.values()),
        checks=checks,
        metrics=metrics,
        violations=tuple(violations),
    )


async def _run_workflow(agent: Agent, contract: WorkflowContract) -> WorkflowRun:
    messages = [
        {
            "role": "system",
            "content": (
                "Use only the supplied record tools. Choose the narrowest tool, follow typed recovery guidance, "
                "and keep the final answer under 100 characters."
            ),
        },
        {"role": "user", "content": contract.prompt},
    ]
    events: list[Any] = []
    started = monotonic()
    async for event in agent.stream(messages):
        events.append(event)
    duration_ms = round((monotonic() - started) * 1000)
    result = next(event for event in reversed(events) if isinstance(event, Result))
    attempts, pairing_errors = _attempts(events)
    return WorkflowRun(
        report=_score(contract, attempts, pairing_errors, messages, result, duration_ms),
        attempts=attempts,
        pairing_errors=pairing_errors,
        messages=tuple(messages),
        result=result,
    )


def _scripted_agent(responses: list, store: _RecordStore | None = None) -> Agent:
    client = MockLLMClient(MockCompletionClient(responses))
    return _make_agent(client, _record_tools(store or _RecordStore()))


def _tool_batch_response(calls: list[tuple[str, dict[str, Any], str]]):
    responses = [make_tool_response(name, args, call_id=call_id) for name, args, call_id in calls]
    response = responses[0]
    tool_calls = [call for item in responses for call in (item.choices[0].message.tool_calls or [])]
    choice = response.choices[0]
    message = replace(choice.message, tool_calls=tool_calls)
    return replace(response, choices=[replace(choice, message=message)])


def _selection_contract(name: str = "exact_ref_selection") -> WorkflowContract:
    return WorkflowContract(
        name=name,
        prompt=(
            "Read the current record with exact ref stable-7. Do not search all records. "
            "Reply exactly: stable-7 is current."
        ),
        expected_calls=("record_read",),
        expected_arguments=({"ref": "stable-7"},),
        expected_result_patterns=(r"stable-7 is current at revision 7\.?",),
        allowed_tools=frozenset({"record_read"}),
        answer_pattern=r"stable-7 is current\.?",
        max_tool_calls=1,
        max_steps=1,
    )


def _recovery_contract(name: str = "fresh_read_recovery") -> WorkflowContract:
    return WorkflowContract(
        name=name,
        prompt=(
            "Try record_update first to set incident-42 to resolved. If it returns fresh_read_required, "
            "call record_read for incident-42, retry record_update exactly once, and do not search. "
            "Reply exactly: incident-42 is resolved."
        ),
        expected_calls=("record_update", "record_read", "record_update"),
        expected_arguments=(
            {"ref": "incident-42", "value": "resolved"},
            {"ref": "incident-42"},
            {"ref": "incident-42", "value": "resolved"},
        ),
        expected_result_patterns=(
            r"Read incident-42 before updating it, then retry once\.?",
            r"incident-42 is current at revision 7\.?",
            r"incident-42 updated to resolved\.?",
        ),
        allowed_tools=frozenset({"record_update", "record_read"}),
        answer_pattern=r"incident-42 is resolved\.?",
        recoveries=(
            RecoveryPath(
                failed_tool="record_update",
                error_code="fresh_read_required",
                recovery_tool="record_read",
                retry_tool="record_update",
            ),
        ),
        max_tool_calls=3,
        max_steps=3,
    )


@pytest.fixture(autouse=True)
def _isolate_harness_output(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_result_files, "RESULTS_BASE", tmp_path / "tool-results")
    monkeypatch.setattr(raw_tool_results, "RAW_TOOL_RESULTS_BASE", tmp_path / "raw-tool-results")


@pytest.mark.asyncio
async def test_scored_workflow_selects_exact_read_over_search():
    contract = _selection_contract()
    agent = _scripted_agent(
        [
            make_tool_response("record_read", {"ref": "stable-7"}, call_id="selection-read"),
            make_text_response("stable-7 is current."),
        ]
    )

    run = await _run_workflow(agent, contract)

    assert run.report.passed, run.report.to_dict()
    assert run.report.score == 100
    assert run.report.metrics.wrong_tool_calls == 0


@pytest.mark.asyncio
async def test_scored_workflow_recovers_failed_write_via_read_then_retry():
    contract = _recovery_contract()
    agent = _scripted_agent(
        [
            make_tool_response(
                "record_update",
                {"ref": "incident-42", "value": "resolved"},
                call_id="recovery-update-1",
            ),
            make_tool_response("record_read", {"ref": "incident-42"}, call_id="recovery-read"),
            make_tool_response(
                "record_update",
                {"ref": "incident-42", "value": "resolved"},
                call_id="recovery-update-2",
            ),
            make_text_response("incident-42 is resolved."),
        ]
    )

    run = await _run_workflow(agent, contract)

    assert run.report.passed, run.report.to_dict()
    assert run.report.metrics.retries == 1
    assert run.report.metrics.failures == ("record_update:fresh_read_required",)


@pytest.mark.asyncio
async def test_scored_workflow_bounds_and_offloads_oversized_result():
    contract = WorkflowContract(
        name="oversized_export",
        prompt="Export the record, then reply exactly: Export is ready.",
        expected_calls=("record_export",),
        expected_arguments=({},),
        expected_result_patterns=(r"export ready",),
        allowed_tools=frozenset({"record_export"}),
        answer_pattern=r"export is ready\.?",
        max_tool_calls=1,
        max_steps=1,
        require_offload=True,
    )
    agent = _scripted_agent(
        [
            make_tool_response("record_export", {}, call_id="large-export"),
            make_text_response("Export is ready."),
        ]
    )

    run = await _run_workflow(agent, contract)

    assert run.report.passed, run.report.to_dict()
    assert run.report.metrics.max_result_payload_bytes <= OFFLOAD_THRESHOLD
    assert run.report.metrics.max_model_message_bytes <= OFFLOAD_THRESHOLD
    completed = run.attempts[0].completed
    assert completed is not None and completed.data is not None
    assert completed.data["truncated"] is True
    raw = completed.data[RAW_TOOL_RESULT_DATA_KEY]
    assert completed.data["raw_ref"] == raw["blob_ref"]
    assert completed.data["raw_bytes"] == raw["content_bytes"]
    assert raw["blob_ref"].startswith("sha256:")
    assert "Full tool result payload" in completed.result
    exact_payload = raw_tool_results.read_raw_tool_result(raw["blob_path"])
    exact_result = ToolResult.from_serialized_payload(exact_payload)
    assert exact_result is not None
    assert exact_result.content.startswith("export ready\n")
    assert len(exact_result.content) > 80_000
    assert Path(raw["blob_path"]).is_file()


@pytest.mark.asyncio
async def test_scorer_rejects_wrong_tool_and_missing_required_call():
    contract = _selection_contract("reject_wrong_namespace")
    agent = _scripted_agent(
        [
            make_tool_response("record_search", {"query": "stable-7"}, call_id="wrong-search"),
            make_text_response("stable-7 is current."),
        ]
    )

    run = await _run_workflow(agent, contract)

    assert not run.report.passed
    assert run.report.score == 60
    assert run.report.metrics.wrong_tool_calls == 1
    assert run.report.checks["selection"] is False
    assert run.report.checks["workflow"] is False


@pytest.mark.asyncio
async def test_scorer_rejects_same_batch_speculation_as_recovery():
    contract = _recovery_contract("reject_same_batch_recovery")
    agent = _scripted_agent(
        [
            _tool_batch_response(
                [
                    (
                        "record_update",
                        {"ref": "incident-42", "value": "resolved"},
                        "batch-update-1",
                    ),
                    ("record_read", {"ref": "incident-42"}, "batch-read"),
                    (
                        "record_update",
                        {"ref": "incident-42", "value": "resolved"},
                        "batch-update-2",
                    ),
                ]
            ),
            make_text_response("incident-42 is resolved."),
        ]
    )

    run = await _run_workflow(agent, contract)

    assert not run.report.passed
    assert run.report.checks["recovery"] is False
    assert run.report.metrics.retries == 0


@pytest.mark.asyncio
async def test_scorer_rejects_correct_names_with_wrong_arguments():
    contract = _recovery_contract("reject_wrong_arguments")
    agent = _scripted_agent(
        [
            make_tool_response(
                "record_update",
                {"ref": "wrong-99", "value": "resolved"},
                call_id="wrong-update-1",
            ),
            make_tool_response("record_read", {"ref": "wrong-99"}, call_id="wrong-read"),
            make_tool_response(
                "record_update",
                {"ref": "wrong-99", "value": "resolved"},
                call_id="wrong-update-2",
            ),
            make_text_response("incident-42 is resolved."),
        ]
    )

    run = await _run_workflow(agent, contract)

    assert not run.report.passed
    assert run.report.checks["workflow"] is False
    assert run.report.metrics.argument_mismatches == (0, 1, 2)


@pytest.mark.asyncio
async def test_scorer_rejects_negated_answer():
    contract = _selection_contract("reject_negated_answer")
    agent = _scripted_agent(
        [
            make_tool_response("record_read", {"ref": "stable-7"}, call_id="negated-read"),
            make_text_response("stable-7 is not current."),
        ]
    )

    run = await _run_workflow(agent, contract)

    assert not run.report.passed
    assert run.report.checks["answer"] is False
    assert run.report.metrics.final_answer_bytes < contract.max_answer_bytes


@pytest.mark.asyncio
async def test_scorer_rejects_duplicate_tool_ids():
    contract = replace(
        _selection_contract("reject_duplicate_ids"),
        expected_calls=("record_read", "record_read"),
        expected_arguments=({"ref": "stable-7"}, {"ref": "stable-7"}),
        expected_result_patterns=(
            r"stable-7 is current at revision 7\.?",
            r"stable-7 is current at revision 7\.?",
        ),
        max_tool_calls=2,
        max_steps=2,
    )
    agent = _scripted_agent(
        [
            make_tool_response("record_read", {"ref": "stable-7"}, call_id="duplicate"),
            make_tool_response("record_read", {"ref": "stable-7"}, call_id="duplicate"),
            make_text_response("stable-7 is current."),
        ]
    )

    run = await _run_workflow(agent, contract)

    assert not run.report.passed
    assert run.report.metrics.pairing_errors
    assert run.report.checks["workflow"] is False


@pytest.mark.asyncio
async def test_scorer_rejects_valid_but_unrelated_large_offload_manifest():
    contract = replace(_selection_contract("reject_unrelated_offload"), require_offload=True)
    agent = _scripted_agent(
        [
            make_tool_response("record_read", {"ref": "stable-7"}, call_id="forged-read"),
            make_text_response("stable-7 is current."),
        ]
    )
    run = await _run_workflow(agent, contract)
    completed = run.attempts[0].completed
    assert completed is not None
    unrelated = ToolResult(
        content="unrelated export\n" + ("Z" * 80_000),
        preview="Unrelated export",
    ).with_default_outcome()
    large_payload = unrelated.serialized_payload()
    blob = raw_tool_results.persist_raw_tool_result(large_payload)
    payload_path = tool_result_files.RESULTS_BASE / "test" / f"{completed.tool_id}.payload.json"
    payload_path.parent.mkdir(parents=True)
    payload_path.write_text(large_payload, encoding="utf-8")
    forged = replace(
        completed,
        data={
            "truncated": True,
            "raw_ref": blob.blob_ref,
            "raw_bytes": blob.content_bytes,
            **blob.to_internal_data(),
        },
        result=(
            f"{unrelated.content[:256]}\nUse file_read(path={str(payload_path)!r}, offset=N, limit=M) to retrieve it."
        ),
    )
    attempt = replace(run.attempts[0], completed=forged)

    report = _score(contract, (attempt,), (), list(run.messages), run.result, 0)

    assert blob.content_bytes > OFFLOAD_THRESHOLD
    assert not report.passed
    assert report.metrics.offloaded_results == 0
    assert report.metrics.result_mismatches == (0,)
    assert report.checks["bounds"] is False


@pytest.mark.asyncio
async def test_provider_selection_and_recovery_trials_when_explicitly_configured():
    model = os.environ.get("ARDEN_TOOL_HARNESS_MODEL", "").strip()
    if not model:
        pytest.skip("set ARDEN_TOOL_HARNESS_MODEL to run provider-backed workflow trials")
    trials = int(os.environ.get("ARDEN_TOOL_HARNESS_TRIALS", "3"))
    if not 1 <= trials <= 10:
        raise ValueError("ARDEN_TOOL_HARNESS_TRIALS must be between 1 and 10")

    contracts = (
        _selection_contract("provider_exact_ref_selection"),
        replace(
            _recovery_contract("provider_fresh_read_recovery"),
            max_steps=4,
        ),
    )
    llm_router.init(get_config())
    reports: dict[str, list[dict[str, Any]]] = {contract.name: [] for contract in contracts}
    try:
        for contract in contracts:
            for _ in range(trials):
                tools = _record_tools(_RecordStore())
                assert all(candidate.policy.scope is ToolScope.INTERNAL for candidate in tools.values())
                agent = _make_agent(llm_client, tools, model=model)
                reports[contract.name].append((await _run_workflow(agent, contract)).report.to_dict())
    finally:
        await llm_router.close()

    required = math.ceil(trials * 2 / 3)
    unsafe = [
        report
        for scenario_reports in reports.values()
        for report in scenario_reports
        if report["metrics"]["wrong_tool_calls"] or not report["checks"]["bounds"]
    ]
    assert unsafe == [], {"unsafe_trials": unsafe}
    for scenario, scenario_reports in reports.items():
        passed = sum(report["passed"] for report in scenario_reports)
        assert passed >= required, {
            "scenario": scenario,
            "passed": passed,
            "required": required,
            "trials": scenario_reports,
        }
