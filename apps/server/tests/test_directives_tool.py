import json
import re
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

import arden.tools.directives as directives_module
from arden.agent.ledger import SharedLedger
from arden.context.models import SessionState
from arden.core.tool_executor import ArdenToolExecutor
from arden.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry
from arden.tools.core.types import ToolOverrideDecision
from arden.tools.directives import (
    GetDirectivesInput,
    SetDirectivesInput,
    approve_set_directives,
    get_directives,
    get_directives_tool,
    set_directives,
    set_directives_tool,
)
from arden.tools.executor import ToolExecutor

HASH = re.compile(r"\b[0-9a-f]{64}\b")


def _execution(tool_name: str) -> ToolExecution:
    return ToolExecution(
        tool_id="call-1",
        tool_name=tool_name,
        ctx=ToolContext(
            session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
            registry=ToolRegistry(),
            run=RunContext(run_id="run-1"),
            io=IOBridge(),
            background_tasks=BackgroundTaskRegistry(session_id="test"),
        ),
    )


@pytest.mark.asyncio
async def test_directives_read_hides_revision_and_stale_write_is_rejected(tmp_path, monkeypatch):
    target = tmp_path / "directives.json"
    target.write_text(json.dumps({"content": "version A"}), encoding="utf-8")
    monkeypatch.setattr(directives_module, "DIRECTIVES_PATH", target)

    execution = _execution("directives_get")
    read = await get_directives(execution, GetDirectivesInput())
    approval = await approve_set_directives(
        execution,
        SetDirectivesInput(directives="approved replacement"),
    )
    target.write_text(json.dumps({"content": "version B"}), encoding="utf-8")
    result = await set_directives(
        execution,
        SetDirectivesInput(directives="approved replacement"),
    )

    assert read.content == "version A"
    assert "sha256" not in read.data
    assert "sha256" not in (approval.diff or "").lower()
    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "write_conflict"
    assert "sha256" not in result.content.lower()
    assert json.loads(target.read_text(encoding="utf-8"))["content"] == "version B"


@pytest.mark.asyncio
async def test_directives_conflict_stays_hash_free_through_agent_executor(tmp_path, monkeypatch):
    target = tmp_path / "directives.json"
    target.write_text(json.dumps({"content": "version A"}), encoding="utf-8")
    monkeypatch.setattr(directives_module, "DIRECTIVES_PATH", target)
    registry = ToolRegistry(tool_overrides={"directives_set": ToolOverrideDecision.APPROVE})
    registry.register("directives_get", get_directives_tool)
    registry.register("directives_set", set_directives_tool)
    context = _execution("directives_get").ctx
    context.registry = registry
    executor = ArdenToolExecutor(
        ToolExecutor().with_registry(registry),
        context,
        ledger=SharedLedger(),
    )

    await executor.execute("directives_get", {}, "read-call")
    target.write_text(json.dumps({"content": "version B"}), encoding="utf-8")
    result = await executor.execute(
        "directives_set",
        {"directives": "approved replacement"},
        "write-call",
    )

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "write_conflict"
    assert HASH.search(result.serialized_payload()) is None


@pytest.mark.asyncio
async def test_directives_create_and_clear_are_idempotent(tmp_path, monkeypatch):
    target = tmp_path / "directives.json"
    monkeypatch.setattr(directives_module, "DIRECTIVES_PATH", target)

    execution = _execution("directives_set")
    created = await set_directives(execution, SetDirectivesInput(directives="Be concise"))
    cleared = await set_directives(
        execution,
        SetDirectivesInput(directives=""),
    )

    assert created.outcome is not None and created.outcome.effect is not None
    assert created.outcome.effect.before_ref is None
    assert created.outcome.effect.after_ref is None
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["content"] == ""
    assert "sha256" not in created.data and "sha256" not in cleared.data


@pytest.mark.asyncio
async def test_directives_require_a_fresh_read_for_replacement(tmp_path, monkeypatch):
    target = tmp_path / "directives.json"
    target.write_text(json.dumps({"content": "existing"}), encoding="utf-8")
    monkeypatch.setattr(directives_module, "DIRECTIVES_PATH", target)

    result = await set_directives(_execution("directives_set"), SetDirectivesInput(directives="replacement"))

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "fresh_read_required"
    assert json.loads(target.read_text(encoding="utf-8"))["content"] == "existing"


@pytest.mark.asyncio
async def test_directives_reject_a_read_that_will_be_offloaded(tmp_path, monkeypatch):
    target = tmp_path / "directives.json"
    target.write_text(json.dumps({"content": "x" * 60_000}), encoding="utf-8")
    monkeypatch.setattr(directives_module, "DIRECTIVES_PATH", target)
    execution = _execution("directives_set")

    await get_directives(execution, GetDirectivesInput())
    result = await set_directives(execution, SetDirectivesInput(directives="replacement"))

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "fresh_read_required"
    assert json.loads(target.read_text(encoding="utf-8"))["content"] == "x" * 60_000


def test_directive_schema_rejects_hash_arguments() -> None:
    assert "expected_sha256" not in SetDirectivesInput.model_fields
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SetDirectivesInput.model_validate({"directives": "x", "expected_sha256": "a" * 64})
