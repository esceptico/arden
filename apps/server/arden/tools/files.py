"""File tools — client-placed: the device executor implements them.

The server keeps the canonical schemas, policies, and approval builders
(diffs are computed here, against the same disk while server and device
share a machine). The Python execute bodies only refuse: the
ExecutionRouter dispatches these tools to the device before the registry
runs, and file work never silently falls back to server execution.

Device-side implementations live in apps/desktop/electron/executor-tools.cjs
and must match the formats and failure codes documented here.
"""

import asyncio
import difflib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPlacement, ToolPolicy, ToolScope

READ_FILE_DESCRIPTION = (
    "Read content from a file on the user's device. Use for code, configs, logs, etc. "
    "For large files, use offset and limit parameters to read in chunks. "
    "When an area default cwd is set, use paths relative to it unless reading outside the area."
)
LIST_FILES_DESCRIPTION = (
    "List files in a directory on the user's device with compact type/size metadata. "
    "When an area default cwd is set, use paths relative to it unless listing outside the area."
)
FIND_FILES_DESCRIPTION = (
    "Find files by glob pattern under a directory on the user's device. "
    "When an area default cwd is set, use paths relative to it unless searching outside the area."
)
SEARCH_TEXT_DESCRIPTION = (
    "Search files on the user's device for literal text. "
    "When an area default cwd is set, use paths relative to it unless searching outside the area."
)
WRITE_FILE_DESCRIPTION = (
    "Write exact UTF-8 content to a file on the user's device. Creates the file or replaces existing content. "
    "Read an existing file completely before replacing it. "
    "When an area default cwd is set, use paths relative to it unless writing outside the area."
)
EDIT_FILE_DESCRIPTION = (
    "Edit a file on the user's device by replacing one exact text block with another. "
    "The old text must match exactly once. "
    "When an area default cwd is set, use paths relative to it unless editing outside the area."
)

_DEFAULT_OFFSET = 1
_DEFAULT_LINE_LIMIT = 500
_DEFAULT_ENTRY_LIMIT = 200
_DEFAULT_MATCH_LIMIT = 100


def _session_cwd(execution: ToolExecution) -> str | None:
    return execution.ctx.area.default_cwd if execution.ctx.area else None


def _resolve_path(path: str, cwd: str | None = None) -> Path:
    expanded = Path(path).expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    root = Path(cwd).expanduser() if cwd else Path.cwd()
    return (root / expanded).resolve()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _display_path(path: Path, cwd: str | None = None) -> str:
    if not cwd:
        return str(path)
    return _relative_path(path, Path(cwd).expanduser().resolve())


def _unified_diff(path: Path, before: str, after: str, *, display_path: str | None = None) -> str | None:
    label = display_path or str(path)
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=label,
            tofile=label,
        )
    )
    return diff or None


def _refuse_in_process(tool_name: str) -> ToolResult:
    return ToolResult.failure(
        code="no_client_execution",
        message=f"{tool_name} runs on the user's device, and this server has no client execution backend configured.",
        preview="No device executor",
        recovery_action="Start the desktop app, or use server-side tools instead.",
    )


class ReadFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="File path. Prefer relative paths from the area default cwd when set.")
    offset: int = Field(
        default=_DEFAULT_OFFSET,
        ge=1,
        le=10_000_000,
        description=f"Line number to start from (1-based, default: {_DEFAULT_OFFSET})",
    )
    limit: int = Field(
        default=_DEFAULT_LINE_LIMIT,
        ge=1,
        le=10_000,
        description=f"Maximum lines to read (default: {_DEFAULT_LINE_LIMIT})",
    )


async def read_file(execution: ToolExecution, args: ReadFileInput) -> ToolResult:
    return _refuse_in_process("file_read")


class ListFilesInput(BaseModel):
    path: str = Field(
        default=".", description="Directory path to list. Prefer relative paths from the area default cwd when set."
    )
    limit: int = Field(default=_DEFAULT_ENTRY_LIMIT, ge=1, le=1000, description="Maximum entries to return.")
    include_hidden: bool = Field(default=False, description="Include dotfiles and dot-directories.")
    cursor: str | None = Field(
        default=None, max_length=500, description="Opaque cursor from a previous file_list page."
    )


async def list_files(execution: ToolExecution, args: ListFilesInput) -> ToolResult:
    return _refuse_in_process("file_list")


class FindFilesInput(BaseModel):
    path: str = Field(
        default=".",
        description="Directory path to search under. Prefer relative paths from the area default cwd when set.",
    )
    pattern: str = Field(default="*", description="Glob pattern, for example '*.py' or '**/README.md'.")
    limit: int = Field(default=_DEFAULT_ENTRY_LIMIT, ge=1, le=1000, description="Maximum files to return.")
    include_hidden: bool = Field(default=False, description="Include dotfiles and dot-directories.")


async def find_files(execution: ToolExecution, args: FindFilesInput) -> ToolResult:
    return _refuse_in_process("file_find")


class SearchTextInput(BaseModel):
    query: str = Field(min_length=1, description="Literal text to search for.")
    path: str = Field(
        default=".",
        description="File or directory path to search. Prefer relative paths from the area default cwd when set.",
    )
    file_glob: str | None = Field(default=None, description="Optional file glob, for example '*.py'.")
    limit: int = Field(default=_DEFAULT_MATCH_LIMIT, ge=1, le=1000, description="Maximum matches to return.")


async def search_text(execution: ToolExecution, args: SearchTextInput) -> ToolResult:
    return _refuse_in_process("file_search_text")


class WriteFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Path to write. Prefer relative paths from the area default cwd when set.")
    content: str = Field(description="Full file content to write.")


def _approve_write_file_sync(args: WriteFileInput, cwd: str | None = None) -> ApprovalInfo | None:
    path = _resolve_path(args.path, cwd)
    if path.exists() and path.is_dir():
        return None
    if not path.parent.exists():
        return None
    before = _read_text(path) if path.exists() and path.is_file() else ""
    display = _display_path(path, cwd)
    diff = _unified_diff(path, before, args.content, display_path=display)
    action = "Replace" if path.exists() else "Create"
    return ApprovalInfo(
        description=f"{action} {display}",
        preview=args.content[:500],
        diff=diff,
    )


async def approve_write_file(execution: ToolExecution, args: WriteFileInput) -> ApprovalInfo | None:
    return await asyncio.to_thread(_approve_write_file_sync, args, _session_cwd(execution))


async def write_file(execution: ToolExecution, args: WriteFileInput) -> ToolResult:
    return _refuse_in_process("file_write")


class EditFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Path to edit. Prefer relative paths from the area default cwd when set.")
    old_text: str = Field(min_length=1, description="Exact existing text block to replace. Must match once.")
    new_text: str = Field(description="Replacement text.")


def _approve_edit_file_sync(args: EditFileInput, cwd: str | None = None) -> ApprovalInfo | None:
    path = _resolve_path(args.path, cwd)
    if not path.exists() or not path.is_file():
        return None
    before = _read_text(path)
    if before.count(args.old_text) != 1:
        return None
    after = before.replace(args.old_text, args.new_text, 1)
    display = _display_path(path, cwd)
    return ApprovalInfo(
        description=f"Edit {display}",
        preview=args.new_text[:500],
        diff=_unified_diff(path, before, after, display_path=display),
    )


async def approve_edit_file(execution: ToolExecution, args: EditFileInput) -> ApprovalInfo | None:
    return await asyncio.to_thread(_approve_edit_file_sync, args, _session_cwd(execution))


async def edit_file(execution: ToolExecution, args: EditFileInput) -> ToolResult:
    return _refuse_in_process("file_edit")


read_file_tool = tool(
    display_name="ReadFile",
    display_description="Read a file from the user's device.",
    description=READ_FILE_DESCRIPTION,
    input_model=ReadFileInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, placement=ToolPlacement.CLIENT),
    execute=read_file,
)

list_files_tool = tool(
    display_name="ListFiles",
    display_description="List files in a directory on the user's device.",
    description=LIST_FILES_DESCRIPTION,
    input_model=ListFilesInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, placement=ToolPlacement.CLIENT),
    execute=list_files,
)

find_files_tool = tool(
    display_name="FindFiles",
    display_description="Find files by name or pattern on the user's device.",
    description=FIND_FILES_DESCRIPTION,
    input_model=FindFilesInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, placement=ToolPlacement.CLIENT),
    execute=find_files,
)

search_text_tool = tool(
    display_name="SearchText",
    display_description="Search text across files on the user's device.",
    description=SEARCH_TEXT_DESCRIPTION,
    input_model=SearchTextInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, placement=ToolPlacement.CLIENT),
    execute=search_text,
)

write_file_tool = tool(
    display_name="WriteFile",
    display_description="Create or replace a file on the user's device.",
    description=WRITE_FILE_DESCRIPTION,
    input_model=WriteFileInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        placement=ToolPlacement.CLIENT,
        requires_approval=True,
        deferred=True,
    ),
    approval=approve_write_file,
    execute=write_file,
)

edit_file_tool = tool(
    display_name="EditFile",
    display_description="Replace exact text in a file on the user's device.",
    description=EDIT_FILE_DESCRIPTION,
    input_model=EditFileInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        placement=ToolPlacement.CLIENT,
        requires_approval=True,
        deferred=True,
    ),
    approval=approve_edit_file,
    execute=edit_file,
)
