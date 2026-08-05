"""File tools are client-placed: behavioral coverage of the device
implementations lives in apps/desktop/tests/executorDeviceTools.test.ts.
The server keeps schemas, policy, approvals, and the refusing in-process
bodies — that is what these tests pin down."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from arden.context.models import AreaContext, SessionState
from arden.core.prompts import AREA_BLOCK
from arden.core.tool_executor import ArdenToolExecutor
from arden.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.file_mutation import RevisionConflict, atomic_compare_and_swap, file_revision
from arden.tools.core.registry import ToolRegistry
from arden.tools.core.types import ToolPlacement
from arden.tools.deferred import is_deferred_tool
from arden.tools.executor import ToolExecutor
from arden.tools.files import (
    FileEditInput,
    FileReadInput,
    FileSearchTextInput,
    WriteFileInput,
    file_edit_tool,
    file_find_tool,
    file_list_tool,
    file_read_tool,
    file_search_text_tool,
    file_write_tool,
)

ALL_FILE_TOOLS = {
    "file_read": file_read_tool,
    "file_list": file_list_tool,
    "file_find": file_find_tool,
    "file_search_text": file_search_text_tool,
    "file_write": file_write_tool,
    "file_edit": file_edit_tool,
}


def _make_execution(tool_name: str, *, area_cwd: str | None = None) -> ToolExecution:
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
        area=(
            AreaContext(area_id="proj-1", area_ref="area~111111", name="Area", default_cwd=area_cwd)
            if area_cwd
            else None
        ),
    )
    return ToolExecution(tool_id="t1", tool_name=tool_name, ctx=ctx)


# --- placement and in-process refusal ---


def test_all_file_tools_are_client_placed():
    for name, file_tool in ALL_FILE_TOOLS.items():
        assert file_tool.policy.placement == ToolPlacement.CLIENT, name


@pytest.mark.asyncio
async def test_read_tools_refuse_in_process_execution(tmp_path):
    result = await file_read_tool.execute(_make_execution("file_read"), path=str(tmp_path / "x.txt"))
    assert result.is_error
    assert result.outcome.error.code == "no_client_execution"

    result = await file_list_tool.execute(_make_execution("file_list"), path=str(tmp_path))
    assert result.is_error
    assert result.outcome.error.code == "no_client_execution"


@pytest.mark.asyncio
async def test_write_tools_refuse_in_process_execution_without_touching_disk(tmp_path):
    target = tmp_path / "note.txt"
    result = await file_write_tool.execute(_make_execution("file_write"), path=str(target), content="hello")
    assert result.is_error
    assert result.outcome.error.code == "no_client_execution"
    assert not target.exists()


# --- atomic compare-and-swap (shared mutation primitive, still server infra) ---


def test_atomic_compare_and_swap_preserves_old_file_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "note.txt"
    target.write_text("old", encoding="utf-8")
    expected = file_revision(target).sha256

    def fail_replace(_source, _target):
        raise OSError("replace failed")

    monkeypatch.setattr("arden.tools.core.file_mutation.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        atomic_compare_and_swap(target, "new", expected)

    assert target.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_compare_and_swap_rejects_stale_revision(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("version A", encoding="utf-8")
    expected = file_revision(target).sha256
    target.write_text("version B", encoding="utf-8")

    with pytest.raises(RevisionConflict) as exc_info:
        atomic_compare_and_swap(target, "approved replacement", expected)

    assert exc_info.value.expected == expected
    assert exc_info.value.observed == file_revision(target).sha256
    assert target.read_text(encoding="utf-8") == "version B"


# --- area prompt contract ---


def test_area_prompt_tells_agent_to_use_relative_paths():
    prompt = AREA_BLOCK.render(
        area=AreaContext(
            area_id="proj-1",
            area_ref="area~111111",
            name="Area",
            default_cwd="/Users/me/src/area",
        )
    )

    assert "Default cwd: /Users/me/src/area" in prompt
    assert "Use relative paths from the default cwd" in prompt


# --- schemas ---


def test_file_write_schemas_reject_hash_arguments() -> None:
    for model in (FileReadInput, WriteFileInput, FileEditInput):
        assert "expected_sha256" not in model.model_fields

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WriteFileInput.model_validate({"path": "x", "content": "y", "expected_sha256": "a" * 64})


def test_file_search_schema_exposes_modes_and_bound_cursor() -> None:
    parsed = FileSearchTextInput.model_validate(
        {"query": "needle", "output_mode": "files_only", "cursor": "cursor-1", "limit": 25}
    )
    assert parsed.output_mode == "files_only"
    assert parsed.cursor == "cursor-1"

    with pytest.raises(ValidationError):
        FileSearchTextInput.model_validate({"query": "needle", "output_mode": "unknown"})
    with pytest.raises(ValidationError):
        FileSearchTextInput.model_validate({"query": "needle", "unexpected": True})
    with pytest.raises(ValidationError):
        FileSearchTextInput.model_validate({"query": "q" * 4097})


def test_file_tools_share_one_explicit_filesystem_concurrency_group() -> None:
    assert {tool.policy.concurrency_group for tool in ALL_FILE_TOOLS.values()} == {"filesystem"}


def test_internal_tool_metadata_uses_explicit_or_tool_scoped_concurrency_groups() -> None:
    executor = ToolExecutor()
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id="run"),
        io=IOBridge(),
    )
    wrapped = ArdenToolExecutor(executor, ctx)

    assert wrapped.get_meta("file_write").concurrency_group == "filesystem"
    assert wrapped.get_meta("bash").concurrency_group == "filesystem"
    assert wrapped.get_meta("current_time").concurrency_group == "current_time"


# --- approvals (still built server-side, against the same disk) ---


@pytest.mark.asyncio
async def test_write_approval_builds_diff_without_hashes(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("version A\n", encoding="utf-8")

    approval = await file_write_tool.approval_info(
        _make_execution("file_write"),
        path=str(target),
        content="approved replacement\n",
    )

    assert approval is not None
    assert approval.description.startswith("Replace ")
    assert approval.diff is not None
    assert "-version A" in approval.diff
    assert "+approved replacement" in approval.diff
    assert "sha256" not in approval.diff.lower()


@pytest.mark.asyncio
async def test_write_approval_for_new_file_says_create(tmp_path):
    approval = await file_write_tool.approval_info(
        _make_execution("file_write"),
        path=str(tmp_path / "new.txt"),
        content="hello",
    )
    assert approval is not None
    assert approval.description.startswith("Create ")


@pytest.mark.asyncio
async def test_edit_approval_requires_unique_match(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("same\nsame\n", encoding="utf-8")

    approval = await file_edit_tool.approval_info(
        _make_execution("file_edit"),
        path=str(target),
        old_text="same",
        new_text="different",
    )
    assert approval is None

    target.write_text("hello old world\n", encoding="utf-8")
    approval = await file_edit_tool.approval_info(
        _make_execution("file_edit"),
        path=str(target),
        old_text="old",
        new_text="new",
    )
    assert approval is not None
    assert approval.diff is not None
    assert "+hello new world" in approval.diff


@pytest.mark.asyncio
async def test_area_approvals_display_relative_paths(tmp_path):
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")

    approval = await file_edit_tool.approval_info(
        _make_execution("file_edit", area_cwd=str(tmp_path)),
        path="notes.txt",
        old_text="hello",
        new_text="bye",
    )

    assert approval is not None
    assert approval.description == "Edit notes.txt"
    assert str(tmp_path) not in approval.description


# --- registration ---


def test_file_tools_are_registered_as_core_tools():
    executor = ToolExecutor()

    names = set(executor.registry.tools)

    assert {"file_read", "file_list", "file_find", "file_search_text", "file_write", "file_edit"}.issubset(names)
    assert not is_deferred_tool("file_read", executor.registry)
    assert not is_deferred_tool("file_list", executor.registry)
    assert not is_deferred_tool("file_find", executor.registry)
    assert not is_deferred_tool("file_search_text", executor.registry)
    assert is_deferred_tool("file_write", executor.registry)
    assert is_deferred_tool("file_edit", executor.registry)


@pytest.mark.asyncio
async def test_write_file_requires_approval_through_registry(tmp_path):
    target = tmp_path / "new.txt"
    registry = ToolRegistry()
    registry.register("file_write", file_write_tool)
    execution = _make_execution("file_write")

    result = await registry.execute(
        "file_write",
        execution,
        {"path": str(target), "content": "hello"},
    )

    assert result.preview == "Rejected"
    assert not target.exists()
