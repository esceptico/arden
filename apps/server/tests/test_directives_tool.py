import json
from datetime import UTC, datetime

import pytest

import ntrp.tools.directives as directives_module
from ntrp.context.models import SessionState
from ntrp.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from ntrp.tools.core.registry import ToolRegistry
from ntrp.tools.directives import (
    GetDirectivesInput,
    SetDirectivesInput,
    approve_set_directives,
    get_directives,
    set_directives,
)


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
async def test_directives_read_exposes_revision_and_stale_write_is_rejected(tmp_path, monkeypatch):
    target = tmp_path / "directives.json"
    target.write_text(json.dumps({"content": "version A"}), encoding="utf-8")
    monkeypatch.setattr(directives_module, "DIRECTIVES_PATH", target)

    read = await get_directives(_execution("get_directives"), GetDirectivesInput())
    expected = read.data["sha256"]
    approval = await approve_set_directives(
        _execution("set_directives"),
        SetDirectivesInput(directives="approved replacement", expected_sha256=expected),
    )
    target.write_text(json.dumps({"content": "version B"}), encoding="utf-8")
    result = await set_directives(
        _execution("set_directives"),
        SetDirectivesInput(directives="approved replacement", expected_sha256=expected),
    )

    assert read.content == "version A"
    assert expected in (approval.diff or "")
    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "write_conflict"
    assert json.loads(target.read_text(encoding="utf-8"))["content"] == "version B"


@pytest.mark.asyncio
async def test_directives_create_and_clear_keep_revision_history(tmp_path, monkeypatch):
    target = tmp_path / "directives.json"
    monkeypatch.setattr(directives_module, "DIRECTIVES_PATH", target)

    created = await set_directives(
        _execution("set_directives"),
        SetDirectivesInput(directives="Be concise", expected_sha256="absent"),
    )
    cleared = await set_directives(
        _execution("set_directives"),
        SetDirectivesInput(directives="", expected_sha256=created.data["sha256"]),
    )

    assert created.outcome is not None and created.outcome.effect is not None
    assert created.outcome.effect.before_ref == "absent"
    assert created.outcome.effect.after_ref == created.data["sha256"]
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["content"] == ""
    assert cleared.data["sha256"] != created.data["sha256"]
