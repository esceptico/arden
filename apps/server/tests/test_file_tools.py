from datetime import UTC, datetime

import pytest

from ntrp.context.models import ProjectContext, SessionState
from ntrp.core.prompts import PROJECT_BLOCK
from ntrp.tools import files as file_tools_module
from ntrp.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from ntrp.tools.core.registry import ToolRegistry
from ntrp.tools.deferred import is_deferred_tool
from ntrp.tools.executor import ToolExecutor
from ntrp.tools.files import (
    edit_file_tool,
    find_files_tool,
    list_files_tool,
    read_file_tool,
    search_text_tool,
    write_file_tool,
)


def _make_execution(tool_name: str, *, project_cwd: str | None = None) -> ToolExecution:
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
        project=(ProjectContext(project_id="proj-1", name="Project", default_cwd=project_cwd) if project_cwd else None),
    )
    return ToolExecution(tool_id="t1", tool_name=tool_name, ctx=ctx)


@pytest.mark.asyncio
async def test_read_file_self_reports_source_refs(tmp_path):
    note = tmp_path / "q3.md"
    note.write_text("dashboard notes", encoding="utf-8")

    result = await read_file_tool.execute(_make_execution("read_file"), path=str(note))

    assert not result.is_error
    assert [ref.to_dict() for ref in result.source_refs] == [
        {
            "provider": "filesystem",
            "kind": "file",
            "ref": str(note.resolve()),
            "title": "q3.md",
        }
    ]


def test_project_prompt_tells_agent_to_use_relative_paths():
    prompt = PROJECT_BLOCK.render(
        project=ProjectContext(project_id="proj-1", name="Project", default_cwd="/Users/me/src/project")
    )

    assert "Default cwd: /Users/me/src/project" in prompt
    assert "Use relative paths from the default cwd" in prompt


@pytest.mark.asyncio
async def test_project_file_tools_display_paths_relative_to_default_cwd(tmp_path):
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")

    result = await list_files_tool.execute(_make_execution("list_files", project_cwd=str(tmp_path)), path=".")

    assert not result.is_error
    assert result.content.startswith(". (1 entries)")
    assert str(tmp_path) not in result.content
    assert result.data["path"] == "."
    assert result.data["absolute_path"] == str(tmp_path)
    assert result.data["entries"][0]["path"] == "README.md"
    assert result.data["entries"][0]["absolute_path"] == str(tmp_path / "README.md")


@pytest.mark.asyncio
async def test_project_write_and_edit_results_display_relative_paths(tmp_path):
    execution = _make_execution("write_file", project_cwd=str(tmp_path))
    write = await write_file_tool.execute(execution, path="notes.txt", content="hello")

    assert not write.is_error
    assert "Wrote notes.txt" in write.content
    assert str(tmp_path) not in write.content
    assert write.data["path"] == "notes.txt"
    assert write.data["absolute_path"] == str(tmp_path / "notes.txt")

    edit = await edit_file_tool.execute(
        _make_execution("edit_file", project_cwd=str(tmp_path)),
        path="notes.txt",
        old_text="hello",
        new_text="bye",
    )

    assert not edit.is_error
    assert edit.content == "Edited notes.txt."
    assert edit.data["path"] == "notes.txt"
    assert edit.data["absolute_path"] == str(tmp_path / "notes.txt")


@pytest.mark.asyncio
async def test_list_files_returns_directory_entries(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")

    result = await list_files_tool.execute(_make_execution("list_files"), path=str(tmp_path))

    assert not result.is_error
    assert "README.md" in result.content
    assert "src/" in result.content
    assert result.data["total"] == 2


@pytest.mark.asyncio
async def test_find_files_matches_glob_recursively(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "app.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")

    result = await find_files_tool.execute(_make_execution("find_files"), path=str(tmp_path), pattern="*.py")

    assert not result.is_error
    assert "pkg/app.py" in result.content
    assert result.data["matches"] == [
        {"path": str(tmp_path / "pkg" / "app.py"), "relative_path": "pkg/app.py", "size": "11B"}
    ]


@pytest.mark.asyncio
async def test_find_files_uses_rg_fast_path(tmp_path, monkeypatch):
    fast_match = {"path": str(tmp_path / "fast.py"), "relative_path": "fast.py", "size": "4B"}

    def fake_rg(root, args):
        assert root == tmp_path
        assert args.pattern == "*.py"
        return [fast_match]

    monkeypatch.setattr(file_tools_module, "_find_files_with_rg", fake_rg)

    result = await find_files_tool.execute(_make_execution("find_files"), path=str(tmp_path), pattern="*.py")

    assert not result.is_error
    assert result.data["matches"] == [fast_match]
    assert "fast.py" in result.content


@pytest.mark.asyncio
async def test_find_files_requires_rg(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("print('hi')", encoding="utf-8")
    monkeypatch.setattr(file_tools_module.shutil, "which", lambda _name: None)

    result = await find_files_tool.execute(_make_execution("find_files"), path=str(tmp_path), pattern="*.py")

    assert result.is_error
    assert result.data["matches"] == []
    assert "ripgrep" in result.content


@pytest.mark.asyncio
async def test_search_text_returns_line_matches(tmp_path):
    (tmp_path / "one.txt").write_text("alpha\nneedle here\n", encoding="utf-8")
    (tmp_path / "two.txt").write_text("nothing\n", encoding="utf-8")

    result = await search_text_tool.execute(_make_execution("search_text"), path=str(tmp_path), query="needle")

    assert not result.is_error
    assert "one.txt:2:" in result.content
    assert result.data["matches"][0]["text"] == "needle here"


@pytest.mark.asyncio
async def test_search_text_uses_rg_fast_path(tmp_path, monkeypatch):
    fast_match = {
        "path": str(tmp_path / "fast.txt"),
        "relative_path": "fast.txt",
        "line": 3,
        "column": 7,
        "text": "fast needle",
    }

    def fake_rg(root, args):
        assert root == tmp_path
        assert args.query == "needle"
        return [fast_match]

    monkeypatch.setattr(file_tools_module, "_search_text_with_rg", fake_rg)

    result = await search_text_tool.execute(_make_execution("search_text"), path=str(tmp_path), query="needle")

    assert not result.is_error
    assert result.data["matches"] == [fast_match]
    assert "fast.txt:3:7: fast needle" in result.content


@pytest.mark.asyncio
async def test_search_text_requires_rg(tmp_path, monkeypatch):
    (tmp_path / "one.txt").write_text("needle here\n", encoding="utf-8")
    monkeypatch.setattr(file_tools_module.shutil, "which", lambda _name: None)

    result = await search_text_tool.execute(_make_execution("search_text"), path=str(tmp_path), query="needle")

    assert result.is_error
    assert result.data["matches"] == []
    assert "ripgrep" in result.content


@pytest.mark.asyncio
async def test_write_file_writes_exact_content(tmp_path):
    target = tmp_path / "new.txt"

    result = await write_file_tool.execute(_make_execution("write_file"), path=str(target), content="a\nb\n")

    assert not result.is_error
    assert target.read_text(encoding="utf-8") == "a\nb\n"
    assert result.data == {"path": str(target), "lines": 3}


@pytest.mark.asyncio
async def test_edit_file_replaces_one_exact_block(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("hello old world\n", encoding="utf-8")

    result = await edit_file_tool.execute(
        _make_execution("edit_file"),
        path=str(target),
        old_text="old",
        new_text="new",
    )

    assert not result.is_error
    assert target.read_text(encoding="utf-8") == "hello new world\n"


@pytest.mark.asyncio
async def test_edit_file_rejects_ambiguous_block(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("same\nsame\n", encoding="utf-8")

    result = await edit_file_tool.execute(
        _make_execution("edit_file"),
        path=str(target),
        old_text="same",
        new_text="different",
    )

    assert result.is_error
    assert "matched 2 times" in result.content
    assert target.read_text(encoding="utf-8") == "same\nsame\n"


def test_file_tools_are_registered_as_core_tools():
    executor = ToolExecutor()

    names = set(executor.registry.tools)

    assert {"read_file", "list_files", "find_files", "search_text", "write_file", "edit_file"}.issubset(names)
    assert not is_deferred_tool("read_file", executor.registry)
    assert not is_deferred_tool("list_files", executor.registry)
    assert not is_deferred_tool("find_files", executor.registry)
    assert not is_deferred_tool("search_text", executor.registry)
    assert is_deferred_tool("write_file", executor.registry)
    assert is_deferred_tool("edit_file", executor.registry)


@pytest.mark.asyncio
async def test_write_file_requires_approval_through_registry(tmp_path):
    target = tmp_path / "new.txt"
    registry = ToolRegistry()
    registry.register("write_file", write_file_tool)
    execution = _make_execution("write_file")

    result = await registry.execute("write_file", execution, {"path": str(target), "content": "hello"})

    assert result.preview == "Rejected"
    assert not target.exists()
