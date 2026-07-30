import asyncio
import difflib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from arden.agent.types.tools import ToolEffect, ToolOutcome, ToolOutcomeStatus, ToolSourceRef, normalize_source_refs
from arden.constants import OFFLOAD_THRESHOLD
from arden.core.tool_result_files import RESULTS_BASE
from arden.tools.core import ToolResult, tool
from arden.tools.core.collections import format_timestamp, paginate
from arden.tools.core.context import ToolExecution
from arden.tools.core.file_mutation import FileRevision, RevisionConflict, atomic_compare_and_swap, read_file_snapshot
from arden.tools.core.formatting import format_lines_with_pagination
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope

READ_FILE_DESCRIPTION = (
    "Read content from a file. Use for code, configs, logs, etc. "
    "For large files, use offset and limit parameters to read in chunks. "
    "When an area default cwd is set, use paths relative to it unless reading outside the area."
)
LIST_FILES_DESCRIPTION = (
    "List files in a directory with compact type/size metadata. "
    "When an area default cwd is set, use paths relative to it unless listing outside the area."
)
FIND_FILES_DESCRIPTION = (
    "Find files by glob pattern under a directory. "
    "When an area default cwd is set, use paths relative to it unless searching outside the area."
)
SEARCH_TEXT_DESCRIPTION = (
    "Search local files for literal text. "
    "When an area default cwd is set, use paths relative to it unless searching outside the area."
)
WRITE_FILE_DESCRIPTION = (
    "Write exact UTF-8 content to a file. Creates the file or replaces existing content. "
    "Read an existing file completely before replacing it. "
    "When an area default cwd is set, use paths relative to it unless writing outside the area."
)
EDIT_FILE_DESCRIPTION = (
    "Edit a file by replacing one exact text block with another. "
    "The old text must match exactly once. "
    "When an area default cwd is set, use paths relative to it unless editing outside the area."
)


_DEFAULT_OFFSET = 1
_DEFAULT_LINE_LIMIT = 500
_OFFLOAD_DIR = f"{RESULTS_BASE}/"  # durable offloaded tool-result files
_OFFLOAD_READ_LIMIT = 100
_DEFAULT_ENTRY_LIMIT = 200
_DEFAULT_MATCH_LIMIT = 100
_RG_TIMEOUT_SECONDS = 20
# Searches respect ignore files (ripgrep's default) and additionally skip the
# universal CPU sinks. arden used to pass --no-ignore, so every search walked
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
    return execution.ctx.area.default_cwd if execution.ctx.area else None


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


def _observation_id(path: Path) -> str:
    return f"file:{path}"


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


def _write_conflict(path: Path, cwd: str | None = None) -> ToolResult:
    return ToolResult.failure(
        code="write_conflict",
        message=f"File changed since it was read: {_display_path(path, cwd)}.",
        preview="Write conflict",
        recovery_action="Read the current file, recompute the edit, and retry.",
    )


# Each tool's filesystem-touching body runs through `asyncio.to_thread` so a
# slow read or a vault-wide search doesn't block the asyncio event loop —
# without this, automations and research agents (which lean heavily on
# read/search/find) would starve every other request on the server.


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


def _read_file_sync(
    args: ReadFileInput, cwd: str | None = None
) -> tuple[ToolResult, Path | None, FileRevision | None, bool]:
    full_path = _resolve_path(args.path, cwd)
    offset = args.offset
    limit = args.limit

    # Guard: offloaded files with default params get capped to prevent
    # the agent from reading the entire offloaded result back into context
    is_offloaded = str(full_path).startswith(_OFFLOAD_DIR)
    if is_offloaded and offset == _DEFAULT_OFFSET and limit == _DEFAULT_LINE_LIMIT:
        limit = _OFFLOAD_READ_LIMIT

    if not full_path.exists():
        return (
            ToolResult.failure(
                code="not_found",
                message=f"File not found: {args.path}. Check the path or use list_files() to list the directory.",
                preview="Not found",
                recovery_action="Call list_files on the parent directory and retry with an existing path.",
            ),
            None,
            None,
            False,
        )

    if not full_path.is_file():
        return (
            ToolResult.failure(
                code="invalid_ref",
                message=f"Path is a directory, not a file: {args.path}. Use list_files(path={args.path!r}) to list contents.",
                preview="Not a file",
                recovery_action="Call list_files for directories or retry with a file path.",
            ),
            None,
            None,
            False,
        )

    try:
        raw, revision = read_file_snapshot(full_path)
        content = raw.decode("utf-8", errors="replace")
        formatted = format_lines_with_pagination(content, offset, limit)
        lines = len(content.split("\n"))
        source_refs = ()
        if not is_offloaded:
            source_refs = normalize_source_refs(
                (
                    ToolSourceRef(
                        provider="filesystem",
                        kind="file",
                        ref=str(full_path),
                        title=full_path.name,
                    ),
                )
            )
        result = ToolResult(
            content=formatted,
            preview=f"Read {lines} lines",
            data={**_path_data(full_path, cwd), "size": revision.size},
            source_refs=source_refs,
        )
        return (
            result,
            full_path,
            revision,
            offset == 1 and limit >= lines and len(result.serialized_payload().encode("utf-8")) <= OFFLOAD_THRESHOLD,
        )

    except PermissionError:
        return (
            ToolResult.failure(
                code="permission_denied",
                message=f"Permission denied: {args.path}. File may be protected or require elevated access.",
                preview="Denied",
                recovery_action="Choose a readable path or request the required filesystem access.",
            ),
            None,
            None,
            False,
        )
    except OSError:
        return (
            ToolResult.failure(
                code="read_failed",
                message=f"Could not read file: {args.path}",
                preview="Read failed",
                retryable=True,
                recovery_action="Retry the read or inspect the path with list_files.",
            ),
            None,
            None,
            False,
        )


async def read_file(execution: ToolExecution, args: ReadFileInput) -> ToolResult:
    result, path, revision, content_read = await asyncio.to_thread(_read_file_sync, args, _session_cwd(execution))
    if path is not None and revision is not None:
        execution.ctx.run.observe_resource(
            _observation_id(path),
            version=revision.sha256,
            container_version=None,
            content_read=content_read,
        )
    return result


class ListFilesInput(BaseModel):
    path: str = Field(
        default=".", description="Directory path to list. Prefer relative paths from the area default cwd when set."
    )
    limit: int = Field(default=_DEFAULT_ENTRY_LIMIT, ge=1, le=1000, description="Maximum entries to return.")
    include_hidden: bool = Field(default=False, description="Include dotfiles and dot-directories.")
    cursor: str | None = Field(
        default=None, max_length=500, description="Opaque cursor from a previous list_files page."
    )


def _list_files_sync(args: ListFilesInput, cwd: str | None = None) -> ToolResult:
    root = _resolve_path(args.path, cwd)
    if not root.exists():
        return ToolResult.failure(
            code="not_found",
            message=f"Directory not found: {args.path}",
            preview="Not found",
            recovery_action="List the parent directory and retry with an existing path.",
        )
    if not root.is_dir():
        return ToolResult.failure(
            code="invalid_ref",
            message=f"Path is not a directory: {args.path}",
            preview="Not a directory",
            recovery_action="Retry with a directory path.",
        )

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
                    "modified_at": format_timestamp(datetime.fromtimestamp(child.stat().st_mtime, tz=UTC)),
                }
            )

        try:
            page = paginate(entries, limit=args.limit, cursor=args.cursor)
        except ValueError:
            return ToolResult.failure(
                code="invalid_ref",
                message="Invalid list_files pagination cursor.",
                preview="Invalid cursor",
                recovery_action="Call list_files again without a cursor.",
            )
        visible = list(page.items)
        lines = [f"{item['name']:<48} {item['size']:<8} {item['modified_at']}" for item in visible]
        if page.has_more:
            lines.append(f"... more available; next_cursor: {page.next_cursor}")
        header = f"{_display_path(root, cwd)} ({len(entries)} entries)"
        return ToolResult(
            content=header + ("\n" + "\n".join(lines) if lines else "\n(empty)"),
            preview=f"{len(entries)} entries",
            data={**_path_data(root, cwd), "entries": visible, **page.to_dict()},
        )
    except PermissionError:
        return ToolResult.failure(
            code="permission_denied",
            message=f"Permission denied: {args.path}",
            preview="Denied",
            recovery_action="Choose a readable directory or request filesystem access.",
        )
    except OSError:
        return ToolResult.failure(
            code="read_failed",
            message=f"Could not list directory: {args.path}",
            preview="List failed",
            retryable=True,
            recovery_action="Retry or inspect the parent directory.",
        )


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
        description="Directory path to search under. Prefer relative paths from the area default cwd when set.",
    )
    pattern: str = Field(default="*", description="Glob pattern, for example '*.py' or '**/README.md'.")
    limit: int = Field(default=_DEFAULT_ENTRY_LIMIT, ge=1, le=1000, description="Maximum files to return.")
    include_hidden: bool = Field(default=False, description="Include dotfiles and dot-directories.")


def _find_files_with_rg(root: Path, args: FindFilesInput) -> list[dict]:
    command = ["--files", "--color", "never", "--sort", "path", "--glob", args.pattern]
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
        if len(matches) > args.limit:
            _stop_rg(process)
            return matches

    _wait_rg(process)
    return matches


def _find_files_failed(root: Path, args: FindFilesInput) -> ToolResult:
    return ToolResult.failure(
        code="search_failed",
        message="File-name search with ripgrep failed.",
        preview="Find failed",
        data={"path": str(root), "pattern": args.pattern, "matches": []},
        retryable=True,
        recovery_action="Check that ripgrep is installed, then narrow the path or pattern and retry.",
    )


def _format_find_files_result(
    root: Path, args: FindFilesInput, matches: list[dict], cwd: str | None = None
) -> ToolResult:
    has_more = len(matches) > args.limit
    visible = matches[: args.limit]
    visible = [{**item, **_path_data(Path(item["path"]), cwd)} for item in visible]
    lines = [f"{item['relative_path']:<72} {item['size']}" for item in visible]
    if not lines:
        lines = ["No files found."]
    elif has_more:
        lines.append(f"Showing {len(visible)} files; more exist. Narrow path/pattern to continue.")
    return ToolResult(
        content=f"{_display_path(root, cwd)} / {args.pattern}\n" + "\n".join(lines),
        preview=f"{len(visible)} files" + (" (capped)" if has_more else ""),
        data={**_path_data(root, cwd), "pattern": args.pattern, "matches": visible, "has_more": has_more},
    )


def _find_files_sync(args: FindFilesInput, cwd: str | None = None) -> ToolResult:
    root = _resolve_path(args.path, cwd)
    if not root.exists():
        return ToolResult.failure(
            code="not_found",
            message=f"Directory not found: {args.path}",
            preview="Not found",
            recovery_action="List the parent directory and retry with an existing path.",
        )
    if not root.is_dir():
        return ToolResult.failure(
            code="invalid_ref",
            message=f"Path is not a directory: {args.path}",
            preview="Not a directory",
            recovery_action="Retry with a directory path.",
        )

    try:
        matches = _find_files_with_rg(root, args)
        return _format_find_files_result(root, args, matches, cwd)
    except (OSError, RuntimeError):
        return _find_files_failed(root, args)


async def find_files(execution: ToolExecution, args: FindFilesInput) -> ToolResult:
    return await asyncio.to_thread(_find_files_sync, args, _session_cwd(execution))


class SearchTextInput(BaseModel):
    query: str = Field(min_length=1, description="Literal text to search for.")
    path: str = Field(
        default=".",
        description="File or directory path to search. Prefer relative paths from the area default cwd when set.",
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
        "--sort",
        "path",
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
        if len(matches) > args.limit:
            _stop_rg(process)
            return matches

    _wait_rg(process)
    return matches


def _search_text_failed(root: Path, args: SearchTextInput) -> ToolResult:
    return ToolResult.failure(
        code="search_failed",
        message="Text search with ripgrep failed.",
        preview="Search failed",
        data={"path": str(root), "query": args.query, "matches": []},
        retryable=True,
        recovery_action="Check that ripgrep is installed, then narrow the path or query and retry.",
    )


def _format_search_text_result(root: Path, args: SearchTextInput, matches: list[dict]) -> ToolResult:
    if not matches:
        return ToolResult(
            content=f"No matches for {args.query!r} under {root}.",
            preview="0 matches",
            data={"path": str(root), "query": args.query, "matches": []},
        )
    has_more = len(matches) > args.limit
    visible = matches[: args.limit]
    content = "\n".join(_format_match(match) for match in visible)
    if has_more:
        content += f"\nShowing {len(visible)} matches; more exist. Narrow path/query to continue."
    return ToolResult(
        content=content,
        preview=f"{len(visible)} matches" + (" (capped)" if has_more else ""),
        data={"path": str(root), "query": args.query, "matches": visible, "has_more": has_more},
    )


def _search_text_sync(args: SearchTextInput, cwd: str | None = None) -> ToolResult:
    root = _resolve_path(args.path, cwd)
    if not root.exists():
        return ToolResult.failure(
            code="not_found",
            message=f"Path not found: {args.path}",
            preview="Not found",
            recovery_action="List the parent directory and retry with an existing path.",
        )

    try:
        matches = _search_text_with_rg(root, args)
        return _format_search_text_result(root, args, matches)
    except (OSError, RuntimeError, KeyError, json.JSONDecodeError):
        return _search_text_failed(root, args)


async def search_text(execution: ToolExecution, args: SearchTextInput) -> ToolResult:
    return await asyncio.to_thread(_search_text_sync, args, _session_cwd(execution))


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


def _fresh_read_required(path: Path, cwd: str | None = None) -> ToolResult:
    return ToolResult.failure(
        code="fresh_read_required",
        message=f"Read {_display_path(path, cwd)} completely before replacing it.",
        preview="Read file first",
        recovery_action="Call read_file for the complete file, then retry.",
    )


def _write_file_sync(
    args: WriteFileInput,
    cwd: str | None,
    observed_version: str | None,
    content_read: bool,
) -> tuple[ToolResult, FileRevision | None, bool]:
    path = _resolve_path(args.path, cwd)
    if path.exists() and path.is_dir():
        return (
            ToolResult.failure(
                code="invalid_ref",
                message=f"Path is a directory: {args.path}",
                preview="Is directory",
                recovery_action="Retry with a file path.",
            ),
            None,
            False,
        )
    if not path.parent.exists():
        return (
            ToolResult.failure(
                code="not_found",
                message=f"Parent directory does not exist: {_display_path(path.parent, cwd)}",
                preview="No parent",
                recovery_action="Create or choose an existing parent directory before writing.",
            ),
            None,
            False,
        )

    try:
        if path.exists():
            current, current_revision = read_file_snapshot(path)
            if current.decode("utf-8", errors="replace") == args.content:
                return (
                    ToolResult(
                        content=f"{_display_path(path, cwd)} already has the requested content.",
                        preview="File unchanged",
                        data={
                            **_path_data(path, cwd),
                            "lines": args.content.count("\n") + 1 if args.content else 0,
                            "size": current_revision.size,
                        },
                    ),
                    current_revision,
                    content_read,
                )
            if not content_read or observed_version is None:
                return _fresh_read_required(path, cwd), None, False
            if current_revision.sha256 != observed_version:
                return _write_conflict(path, cwd), None, False
            expected = observed_version
            operation = "replace"
        else:
            expected = "absent"
            operation = "create"
        revision = atomic_compare_and_swap(path, args.content, expected)
    except RevisionConflict:
        return _write_conflict(path, cwd), None, False
    except PermissionError:
        return (
            ToolResult.failure(
                code="permission_denied",
                message=f"Permission denied: {args.path}",
                preview="Denied",
                recovery_action="Choose a writable path or request filesystem access.",
            ),
            None,
            False,
        )
    except OSError:
        return (
            ToolResult.failure(
                code="write_failed",
                message=f"Could not write file: {args.path}",
                preview="Write failed",
                retryable=True,
                recovery_action="Inspect the path and retry after resolving the filesystem error.",
            ),
            None,
            False,
        )

    lines = args.content.count("\n") + 1 if args.content else 0
    display = _display_path(path, cwd)
    return (
        ToolResult(
            content=f"Wrote {display} ({lines} lines).",
            preview=f"Wrote {lines} lines",
            data={
                **_path_data(path, cwd),
                "lines": lines,
                "size": revision.size,
            },
            outcome=ToolOutcome(
                status=ToolOutcomeStatus.SUCCEEDED,
                effect=ToolEffect(
                    operation=operation,
                    target=str(path),
                ),
            ),
        ),
        revision,
        True,
    )


async def write_file(execution: ToolExecution, args: WriteFileInput) -> ToolResult:
    path = _resolve_path(args.path, _session_cwd(execution))
    observation = execution.ctx.run.resource_observation(_observation_id(path))
    result, revision, complete = await asyncio.to_thread(
        _write_file_sync,
        args,
        _session_cwd(execution),
        None if observation is None else observation.version,
        False if observation is None else observation.content_read,
    )
    if revision is not None and complete:
        execution.ctx.run.observe_resource(
            _observation_id(path),
            version=revision.sha256,
            container_version=None,
            content_read=True,
        )
    return result


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


def _edit_file_sync(args: EditFileInput, cwd: str | None = None) -> tuple[ToolResult, Path | None, FileRevision | None]:
    path = _resolve_path(args.path, cwd)
    if not path.exists():
        return (
            ToolResult.failure(
                code="not_found",
                message=f"File not found: {args.path}",
                preview="Not found",
                recovery_action="Call list_files and read_file, then retry with an exact path.",
            ),
            None,
            None,
        )
    if not path.is_file():
        return (
            ToolResult.failure(
                code="invalid_ref",
                message=f"Path is not a file: {args.path}",
                preview="Not a file",
                recovery_action="Retry with a file path.",
            ),
            None,
            None,
        )

    try:
        raw, current_revision = read_file_snapshot(path)
        before = raw.decode("utf-8", errors="replace")
    except PermissionError:
        return (
            ToolResult.failure(
                code="permission_denied",
                message=f"Permission denied: {args.path}",
                preview="Denied",
                recovery_action="Choose a readable file or request filesystem access.",
            ),
            None,
            None,
        )
    count = before.count(args.old_text)
    if count == 0:
        return (
            ToolResult.failure(
                code="not_found",
                message="Text block not found. Read the file and include more exact context.",
                preview="No match",
                recovery_action="Call read_file and retry with an exact text block from the current revision.",
            ),
            None,
            None,
        )
    if count > 1:
        return (
            ToolResult.failure(
                code="invalid_ref",
                message=f"Text block matched {count} times. Include a larger exact block so the edit is unique.",
                preview="Ambiguous",
                recovery_action="Call read_file and retry with a larger unique text block.",
            ),
            None,
            None,
        )

    after = before.replace(args.old_text, args.new_text, 1)
    try:
        revision = atomic_compare_and_swap(path, after, current_revision.sha256)
    except RevisionConflict:
        return _write_conflict(path, cwd), None, None
    except PermissionError:
        return (
            ToolResult.failure(
                code="permission_denied",
                message=f"Permission denied: {args.path}",
                preview="Denied",
                recovery_action="Choose a writable file or request filesystem access.",
            ),
            None,
            None,
        )
    except OSError:
        return (
            ToolResult.failure(
                code="write_failed",
                message=f"Could not edit file: {args.path}",
                preview="Edit failed",
                retryable=True,
                recovery_action="Inspect the path and retry after resolving the filesystem error.",
            ),
            None,
            None,
        )

    display = _display_path(path, cwd)
    return (
        ToolResult(
            content=f"Edited {display}.",
            preview="Edited",
            data={**_path_data(path, cwd), "size": revision.size},
            outcome=ToolOutcome(
                status=ToolOutcomeStatus.SUCCEEDED,
                effect=ToolEffect(
                    operation="edit",
                    target=str(path),
                ),
            ),
        ),
        path,
        revision,
    )


async def edit_file(execution: ToolExecution, args: EditFileInput) -> ToolResult:
    result, path, revision = await asyncio.to_thread(_edit_file_sync, args, _session_cwd(execution))
    if path is not None and revision is not None:
        observation = execution.ctx.run.resource_observation(_observation_id(path))
        execution.ctx.run.observe_resource(
            _observation_id(path),
            version=revision.sha256,
            container_version=None,
            content_read=False if observation is None else observation.content_read,
        )
    return result


read_file_tool = tool(
    display_name="ReadFile",
    display_description="Read a file from the workspace.",
    description=READ_FILE_DESCRIPTION,
    input_model=ReadFileInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
    execute=read_file,
)

list_files_tool = tool(
    display_name="ListFiles",
    display_description="List files in a directory.",
    description=LIST_FILES_DESCRIPTION,
    input_model=ListFilesInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
    execute=list_files,
)

find_files_tool = tool(
    display_name="FindFiles",
    display_description="Find files by name or pattern.",
    description=FIND_FILES_DESCRIPTION,
    input_model=FindFilesInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
    execute=find_files,
)

search_text_tool = tool(
    display_name="SearchText",
    display_description="Search text across local files.",
    description=SEARCH_TEXT_DESCRIPTION,
    input_model=SearchTextInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
    execute=search_text,
)

write_file_tool = tool(
    display_name="WriteFile",
    display_description="Create or replace a file.",
    description=WRITE_FILE_DESCRIPTION,
    input_model=WriteFileInput,
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, requires_approval=True),
    approval=approve_write_file,
    execute=write_file,
)

edit_file_tool = tool(
    display_name="EditFile",
    display_description="Replace exact text in a file.",
    description=EDIT_FILE_DESCRIPTION,
    input_model=EditFileInput,
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, requires_approval=True),
    approval=approve_edit_file,
    execute=edit_file,
)
