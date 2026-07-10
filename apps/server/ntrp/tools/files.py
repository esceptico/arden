import asyncio
import difflib
import json
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from ntrp.agent.types.tools import ToolSourceRef
from ntrp.core.tool_result_files import RESULTS_BASE
from ntrp.tools.core import ToolResult, tool
from ntrp.tools.core.context import ToolExecution
from ntrp.tools.core.formatting import format_lines_with_pagination
from ntrp.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope

READ_FILE_DESCRIPTION = (
    "Read content from a file. Use for code, configs, logs, etc. "
    "For large files, use offset and limit parameters to read in chunks. "
    "When a project default cwd is set, use paths relative to it unless reading outside the project."
)
LIST_FILES_DESCRIPTION = (
    "List files in a directory with compact type/size metadata. "
    "When a project default cwd is set, use paths relative to it unless listing outside the project."
)
FIND_FILES_DESCRIPTION = (
    "Find files by glob pattern under a directory. "
    "When a project default cwd is set, use paths relative to it unless searching outside the project."
)
SEARCH_TEXT_DESCRIPTION = (
    "Search local files for literal text. "
    "When a project default cwd is set, use paths relative to it unless searching outside the project."
)
WRITE_FILE_DESCRIPTION = (
    "Write exact UTF-8 content to a file. Creates the file or replaces existing content. "
    "When a project default cwd is set, use paths relative to it unless writing outside the project."
)
EDIT_FILE_DESCRIPTION = (
    "Edit a file by replacing one exact text block with another. "
    "When a project default cwd is set, use paths relative to it unless editing outside the project."
)


_DEFAULT_OFFSET = 1
_DEFAULT_LINE_LIMIT = 500
_OFFLOAD_DIR = f"{RESULTS_BASE}/"  # durable offloaded tool-result files
_OFFLOAD_READ_LIMIT = 100
_DEFAULT_ENTRY_LIMIT = 200
_DEFAULT_MATCH_LIMIT = 100
_RG_TIMEOUT_SECONDS = 20
# Searches respect ignore files (ripgrep's default) and additionally skip the
# universal CPU sinks. ntrp used to pass --no-ignore, so every search walked
# .git/node_modules/.venv/build AND the multi-GB tool-result store — one agent
# grepping that in a loop pegs a core. Real harnesses (ripgrep default, Claude
# Code's grep) respect ignores + exclude VCS; matching that keeps every search
# bounded regardless of where the server's disk lives. The offload store is
# excluded separately via an .ignore marker (see core/tool_result_files.py), so
# even a search rooted AT the store skips it while an explicit single-file read
# still works.
_RG_EXCLUDE_GLOBS = (
    "!**/.git/**",
    "!**/node_modules/**",
    "!**/.venv/**",
    "!**/__pycache__/**",
)


def _session_cwd(execution: ToolExecution) -> str | None:
    return execution.ctx.project.default_cwd if execution.ctx.project else None


def _resolve_path(path: str, cwd: str | None = None) -> Path:
    expanded = Path(path).expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    root = Path(cwd).expanduser() if cwd else Path.cwd()
    return (root / expanded).resolve()


def _size_label(path: Path) -> str:
    if path.is_dir():
        return "dir"
    size = path.stat().st_size
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


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


def _path_data(path: Path, cwd: str | None = None) -> dict:
    data = {"path": _display_path(path, cwd)}
    if cwd:
        data["absolute_path"] = str(path)
    return data


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


# Each tool's filesystem-touching body runs through `asyncio.to_thread` so a
# slow read or a vault-wide search doesn't block the asyncio event loop —
# without this, automations and research agents (which lean heavily on
# read/search/find) would starve every other request on the server.


class ReadFileInput(BaseModel):
    path: str = Field(description="File path. Prefer relative paths from the project default cwd when set.")
    offset: int = Field(
        default=_DEFAULT_OFFSET, description=f"Line number to start from (1-based, default: {_DEFAULT_OFFSET})"
    )
    limit: int = Field(
        default=_DEFAULT_LINE_LIMIT, description=f"Maximum lines to read (default: {_DEFAULT_LINE_LIMIT})"
    )


def _read_file_sync(args: ReadFileInput, cwd: str | None = None) -> ToolResult:
    full_path = _resolve_path(args.path, cwd)
    offset = args.offset
    limit = args.limit

    # Guard: offloaded files with default params get capped to prevent
    # the agent from reading the entire offloaded result back into context
    is_offloaded = str(full_path).startswith(_OFFLOAD_DIR)
    if is_offloaded and offset == _DEFAULT_OFFSET and limit == _DEFAULT_LINE_LIMIT:
        limit = _OFFLOAD_READ_LIMIT

    if not full_path.exists():
        return ToolResult(
            content=f"File not found: {args.path}. Check the path or use list_files() to list the directory.",
            preview="Not found",
        )

    if not full_path.is_file():
        return ToolResult(
            content=f"Path is a directory, not a file: {args.path}. Use list_files(path={args.path!r}) to list contents.",
            preview="Not a file",
        )

    try:
        content = _read_text(full_path)
        formatted = format_lines_with_pagination(content, offset, limit)
        lines = len(content.split("\n"))
        source_refs = (
            ()
            if is_offloaded
            else (
                ToolSourceRef(
                    provider="filesystem",
                    kind="file",
                    ref=str(full_path),
                    title=full_path.name,
                ),
            )
        )
        return ToolResult(content=formatted, preview=f"Read {lines} lines", source_refs=source_refs)

    except PermissionError:
        return ToolResult(
            content=f"Permission denied: {args.path}. File may be protected or require elevated access.",
            preview="Denied",
        )
    except Exception as e:
        return ToolResult(content=f"Error reading file: {e}", preview="Read failed", is_error=True)


async def read_file(execution: ToolExecution, args: ReadFileInput) -> ToolResult:
    return await asyncio.to_thread(_read_file_sync, args, _session_cwd(execution))


class ListFilesInput(BaseModel):
    path: str = Field(
        default=".", description="Directory path to list. Prefer relative paths from the project default cwd when set."
    )
    limit: int = Field(default=_DEFAULT_ENTRY_LIMIT, ge=1, le=1000, description="Maximum entries to return.")
    include_hidden: bool = Field(default=False, description="Include dotfiles and dot-directories.")


def _list_files_sync(args: ListFilesInput, cwd: str | None = None) -> ToolResult:
    root = _resolve_path(args.path, cwd)
    if not root.exists():
        return ToolResult(content=f"Directory not found: {args.path}", preview="Not found", is_error=True)
    if not root.is_dir():
        return ToolResult(content=f"Path is not a directory: {args.path}", preview="Not a directory", is_error=True)

    try:
        entries = []
        for child in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if not args.include_hidden and child.name.startswith("."):
                continue
            suffix = "/" if child.is_dir() else ""
            entries.append(
                {
                    "name": f"{child.name}{suffix}",
                    **_path_data(child, cwd),
                    "kind": "directory" if child.is_dir() else "file",
                    "size": _size_label(child),
                }
            )

        visible = entries[: args.limit]
        lines = [f"{item['name']:<48} {item['size']}" for item in visible]
        if len(entries) > args.limit:
            lines.append(f"... {len(entries) - args.limit} more")
        header = f"{_display_path(root, cwd)} ({len(entries)} entries)"
        return ToolResult(
            content=header + ("\n" + "\n".join(lines) if lines else "\n(empty)"),
            preview=f"{len(entries)} entries",
            data={**_path_data(root, cwd), "entries": visible, "total": len(entries)},
        )
    except PermissionError:
        return ToolResult(content=f"Permission denied: {args.path}", preview="Denied", is_error=True)
    except OSError as e:
        return ToolResult(content=f"Error listing directory: {e}", preview="List failed", is_error=True)


async def list_files(execution: ToolExecution, args: ListFilesInput) -> ToolResult:
    return await asyncio.to_thread(_list_files_sync, args, _session_cwd(execution))


def _start_rg(args: list[str], cwd: Path) -> subprocess.Popen[str]:
    executable = shutil.which("rg")
    if executable is None:
        raise FileNotFoundError("ripgrep (rg) is required for file search")
    return subprocess.Popen(
        [executable, *args],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _stop_rg(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)


def _wait_rg(process: subprocess.Popen[str]) -> int:
    try:
        return_code = process.wait(timeout=_RG_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _stop_rg(process)
        raise TimeoutError(f"ripgrep timed out after {_RG_TIMEOUT_SECONDS}s")
    if return_code not in (0, 1):
        raise RuntimeError(f"ripgrep failed with exit code {return_code}")
    return return_code


class FindFilesInput(BaseModel):
    path: str = Field(
        default=".",
        description="Directory path to search under. Prefer relative paths from the project default cwd when set.",
    )
    pattern: str = Field(default="*", description="Glob pattern, for example '*.py' or '**/README.md'.")
    limit: int = Field(default=_DEFAULT_ENTRY_LIMIT, ge=1, le=1000, description="Maximum files to return.")
    include_hidden: bool = Field(default=False, description="Include dotfiles and dot-directories.")


def _find_files_with_rg(root: Path, args: FindFilesInput) -> list[dict]:
    command = ["--files", "--color", "never", "--glob", args.pattern]
    for glob in _RG_EXCLUDE_GLOBS:
        command.extend(["--glob", glob])
    if args.include_hidden:
        command.append("--hidden")

    process = _start_rg(command, root)
    assert process.stdout is not None

    matches = []
    for raw_line in process.stdout:
        relative = raw_line.rstrip("\n")
        if not relative:
            continue
        path = (root / relative).resolve()
        if not path.is_file():
            continue
        matches.append({"path": str(path), "relative_path": relative, "size": _size_label(path)})
        if len(matches) >= args.limit:
            _stop_rg(process)
            return matches

    _wait_rg(process)
    return matches


def _find_files_failed(root: Path, args: FindFilesInput, error: Exception) -> ToolResult:
    return ToolResult(
        content=f"Error finding files with ripgrep: {error}",
        preview="Find failed",
        is_error=True,
        data={"path": str(root), "pattern": args.pattern, "matches": []},
    )


def _format_find_files_result(
    root: Path, args: FindFilesInput, matches: list[dict], cwd: str | None = None
) -> ToolResult:
    matches = [{**item, **_path_data(Path(item["path"]), cwd)} for item in matches]
    lines = [f"{item['relative_path']:<72} {item['size']}" for item in matches]
    if not lines:
        lines = ["No files found."]
    return ToolResult(
        content=f"{_display_path(root, cwd)} / {args.pattern}\n" + "\n".join(lines),
        preview=f"{len(matches)} files",
        data={**_path_data(root, cwd), "pattern": args.pattern, "matches": matches},
    )


def _find_files_sync(args: FindFilesInput, cwd: str | None = None) -> ToolResult:
    root = _resolve_path(args.path, cwd)
    if not root.exists():
        return ToolResult(content=f"Directory not found: {args.path}", preview="Not found", is_error=True)
    if not root.is_dir():
        return ToolResult(content=f"Path is not a directory: {args.path}", preview="Not a directory", is_error=True)

    try:
        matches = _find_files_with_rg(root, args)
        return _format_find_files_result(root, args, matches, cwd)
    except (OSError, RuntimeError) as e:
        return _find_files_failed(root, args, e)


async def find_files(execution: ToolExecution, args: FindFilesInput) -> ToolResult:
    return await asyncio.to_thread(_find_files_sync, args, _session_cwd(execution))


class SearchTextInput(BaseModel):
    query: str = Field(min_length=1, description="Literal text to search for.")
    path: str = Field(
        default=".",
        description="File or directory path to search. Prefer relative paths from the project default cwd when set.",
    )
    file_glob: str | None = Field(default=None, description="Optional file glob, for example '*.py'.")
    limit: int = Field(default=_DEFAULT_MATCH_LIMIT, ge=1, le=1000, description="Maximum matches to return.")


def _format_match(match: dict) -> str:
    return f"{match['relative_path']}:{match['line']}:{match['column']}: {match['text']}"


def _search_text_with_rg(root: Path, args: SearchTextInput) -> list[dict]:
    cwd = root if root.is_dir() else root.parent
    target = "." if root.is_dir() else root.name
    command = [
        "--json",
        "--fixed-strings",
        "--line-number",
        "--column",
        "--color",
        "never",
        "--no-heading",
    ]
    for glob in _RG_EXCLUDE_GLOBS:
        command.extend(["--glob", glob])
    if args.file_glob:
        command.extend(["--glob", args.file_glob])
    command.extend(["--", args.query, target])

    process = _start_rg(command, cwd)
    assert process.stdout is not None

    matches = []
    for raw_line in process.stdout:
        event = json.loads(raw_line)
        if event.get("type") != "match":
            continue

        data = event.get("data", {})
        raw_path = data["path"]["text"]
        path = (cwd / raw_path).resolve()
        line_text = str(data["lines"]["text"]).rstrip("\r\n")
        column = line_text.find(args.query) + 1
        if column <= 0:
            column = int(data["submatches"][0]["start"]) + 1

        matches.append(
            {
                "path": str(path),
                "relative_path": _relative_path(path, root),
                "line": int(data["line_number"]),
                "column": column,
                "text": line_text,
            }
        )
        if len(matches) >= args.limit:
            _stop_rg(process)
            return matches

    _wait_rg(process)
    return matches


def _search_text_failed(root: Path, args: SearchTextInput, error: Exception) -> ToolResult:
    return ToolResult(
        content=f"Error searching files with ripgrep: {error}",
        preview="Search failed",
        is_error=True,
        data={"path": str(root), "query": args.query, "matches": []},
    )


def _format_search_text_result(root: Path, args: SearchTextInput, matches: list[dict]) -> ToolResult:
    if not matches:
        return ToolResult(
            content=f"No matches for {args.query!r} under {root}.",
            preview="0 matches",
            data={"path": str(root), "query": args.query, "matches": []},
        )
    return ToolResult(
        content="\n".join(_format_match(match) for match in matches),
        preview=f"{len(matches)} matches",
        data={"path": str(root), "query": args.query, "matches": matches},
    )


def _search_text_sync(args: SearchTextInput, cwd: str | None = None) -> ToolResult:
    root = _resolve_path(args.path, cwd)
    if not root.exists():
        return ToolResult(content=f"Path not found: {args.path}", preview="Not found", is_error=True)

    try:
        matches = _search_text_with_rg(root, args)
        return _format_search_text_result(root, args, matches)
    except (OSError, RuntimeError, KeyError, json.JSONDecodeError) as e:
        return _search_text_failed(root, args, e)


async def search_text(execution: ToolExecution, args: SearchTextInput) -> ToolResult:
    return await asyncio.to_thread(_search_text_sync, args, _session_cwd(execution))


class WriteFileInput(BaseModel):
    path: str = Field(description="Path to write. Prefer relative paths from the project default cwd when set.")
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
    return ApprovalInfo(description=f"{action} {display}", preview=args.content[:500], diff=diff)


async def approve_write_file(execution: ToolExecution, args: WriteFileInput) -> ApprovalInfo | None:
    return await asyncio.to_thread(_approve_write_file_sync, args, _session_cwd(execution))


def _write_file_sync(args: WriteFileInput, cwd: str | None = None) -> ToolResult:
    path = _resolve_path(args.path, cwd)
    if path.exists() and path.is_dir():
        return ToolResult(content=f"Path is a directory: {args.path}", preview="Is directory", is_error=True)
    if not path.parent.exists():
        return ToolResult(
            content=f"Parent directory does not exist: {_display_path(path.parent, cwd)}",
            preview="No parent",
            is_error=True,
        )

    try:
        path.write_text(args.content, encoding="utf-8")
    except PermissionError:
        return ToolResult(content=f"Permission denied: {args.path}", preview="Denied", is_error=True)
    except OSError as e:
        return ToolResult(content=f"Error writing file: {e}", preview="Write failed", is_error=True)

    lines = args.content.count("\n") + 1 if args.content else 0
    display = _display_path(path, cwd)
    return ToolResult(
        content=f"Wrote {display} ({lines} lines).",
        preview=f"Wrote {lines} lines",
        data={**_path_data(path, cwd), "lines": lines},
    )


async def write_file(execution: ToolExecution, args: WriteFileInput) -> ToolResult:
    return await asyncio.to_thread(_write_file_sync, args, _session_cwd(execution))


class EditFileInput(BaseModel):
    path: str = Field(description="Path to edit. Prefer relative paths from the project default cwd when set.")
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


def _edit_file_sync(args: EditFileInput, cwd: str | None = None) -> ToolResult:
    path = _resolve_path(args.path, cwd)
    if not path.exists():
        return ToolResult(content=f"File not found: {args.path}", preview="Not found", is_error=True)
    if not path.is_file():
        return ToolResult(content=f"Path is not a file: {args.path}", preview="Not a file", is_error=True)

    try:
        before = _read_text(path)
    except PermissionError:
        return ToolResult(content=f"Permission denied: {args.path}", preview="Denied", is_error=True)
    count = before.count(args.old_text)
    if count == 0:
        return ToolResult(
            content="Text block not found. Read the file and include more exact context.",
            preview="No match",
            is_error=True,
        )
    if count > 1:
        return ToolResult(
            content=f"Text block matched {count} times. Include a larger exact block so the edit is unique.",
            preview="Ambiguous",
            is_error=True,
        )

    after = before.replace(args.old_text, args.new_text, 1)
    try:
        path.write_text(after, encoding="utf-8")
    except PermissionError:
        return ToolResult(content=f"Permission denied: {args.path}", preview="Denied", is_error=True)
    except OSError as e:
        return ToolResult(content=f"Error editing file: {e}", preview="Edit failed", is_error=True)

    display = _display_path(path, cwd)
    return ToolResult(content=f"Edited {display}.", preview="Edited", data=_path_data(path, cwd))


async def edit_file(execution: ToolExecution, args: EditFileInput) -> ToolResult:
    return await asyncio.to_thread(_edit_file_sync, args, _session_cwd(execution))


read_file_tool = tool(
    display_name="ReadFile",
    description=READ_FILE_DESCRIPTION,
    input_model=ReadFileInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
    execute=read_file,
)

list_files_tool = tool(
    display_name="ListFiles",
    description=LIST_FILES_DESCRIPTION,
    input_model=ListFilesInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
    execute=list_files,
)

find_files_tool = tool(
    display_name="FindFiles",
    description=FIND_FILES_DESCRIPTION,
    input_model=FindFilesInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
    execute=find_files,
)

search_text_tool = tool(
    display_name="SearchText",
    description=SEARCH_TEXT_DESCRIPTION,
    input_model=SearchTextInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
    execute=search_text,
)

write_file_tool = tool(
    display_name="WriteFile",
    description=WRITE_FILE_DESCRIPTION,
    input_model=WriteFileInput,
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, requires_approval=True),
    approval=approve_write_file,
    execute=write_file,
)

edit_file_tool = tool(
    display_name="EditFile",
    description=EDIT_FILE_DESCRIPTION,
    input_model=EditFileInput,
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, requires_approval=True),
    approval=approve_edit_file,
    execute=edit_file,
)
