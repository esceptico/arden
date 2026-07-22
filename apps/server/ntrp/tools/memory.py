"""Memory tools — the agent's ONLY entry to the record layer.

Plain tools over the scoped RecordStore (atomic, self-contained records in
`config.memory_db_path`):

- remember(text, kind?) -> RecordStore.add (write a record)
- search_memory_candidates(query) -> find exact revisioned deletion candidates
- forget(memory_ref, expected_version) -> delete one exact candidate
- recall(query)         -> hybrid record search (READ)

Records are one simple table with scope metadata for visibility, not a graph or
project hierarchy. Each tool only appears once `MEMORY_RECORDS_SERVICE` is wired
by the knowledge runtime, so they stay hidden when memory is off.

Deletion is deliberately two-step: search proposes stable references, then an
approval-gated forget call compares the selected revision before mutating.
"""

import asyncio
import difflib
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ntrp.agent.types.tools import ToolEffect, ToolOutcome, ToolOutcomeStatus, ToolSourceRef, normalize_source_refs
from ntrp.config import get_config
from ntrp.logging import get_logger
from ntrp.memory.artifacts import ARTIFACT_DIR_KINDS, ROOT_ARTIFACTS, ArtifactMemoryStore
from ntrp.memory.frontmatter import parse_frontmatter
from ntrp.memory.models import SourceRef, source_time
from ntrp.memory.reconciler import RecordOperation, validate_operations
from ntrp.memory.scopes import MemoryScope, apply_scope_to_source, scope_for_write, scopes_for_read
from ntrp.tools.core import ToolResult, tool
from ntrp.tools.core.context import ToolExecution
from ntrp.tools.core.file_mutation import RevisionConflict, atomic_compare_and_swap, revision_or_absent
from ntrp.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope

_logger = get_logger(__name__)

MEMORY_RECORDS_SERVICE = "memory_records"
MEMORY_RECONCILER_SERVICE = "memory_reconciler"
_ENGINE_MEMORY_PATHS = {"raw", ".ntrp", ".index", ".maintenance"}


class RememberInput(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=20_000,
        description=(
            "A single durable, self-contained statement to remember about the "
            "user or their world, stated plainly (resolve pronouns inline)."
        ),
    )
    kind: Literal["directive", "fact", "source", "lesson"] = Field(
        default="fact",
        description=(
            "The record's function: directive | fact | source | lesson. "
            "Preferences and project facts are facts with the right scope; a standing rule the user states is a directive; "
            "a working pattern YOU distilled that should change how you act next time is a lesson (rides your resident playbook)."
        ),
    )


class SearchMemoryCandidatesInput(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=20_000,
        description="A natural-language description used only to find possible memories to forget.",
    )
    limit: int = Field(default=5, ge=1, le=20, description="Maximum deletion candidates to return.")


class ForgetInput(BaseModel):
    memory_ref: str = Field(min_length=1, description="Exact memory_ref returned by search_memory_candidates.")
    expected_version: str = Field(min_length=1, description="Exact version returned with that memory_ref.")


class MemoryTreeInput(BaseModel):
    path: str = Field(default="", description="Relative directory, Markdown, or text path under the memory root.")
    depth: int = Field(default=4, ge=1, le=12, description="Maximum directory depth to include.")
    include_content: bool = Field(default=False, description="Include bounded artifact snippets in tree rows.")


class MemoryReadInput(BaseModel):
    path: str = Field(
        description="Relative Markdown/text path, directory, title, file stem, or [[wikilink]] under memory."
    )
    offset: int = Field(default=1, ge=1, description="1-based line number to start reading.")
    limit: int = Field(default=500, ge=1, le=2000, description="Maximum lines to return.")


class MemorySearchInput(BaseModel):
    query: str = Field(
        min_length=1, max_length=500, description="Text to search in artifact filenames, titles, snippets, and content."
    )
    path: str = Field(
        default="", description="Optional relative directory, Markdown, or text path under the memory root."
    )
    limit: int = Field(default=100, ge=1, le=500, description="Maximum matches to return.")


class MemoryPatchInput(BaseModel):
    path: str = Field(description="Relative .md artifact path under the memory artifact root.")
    old_text: str = Field(min_length=1, description="Exact existing text block to replace. Must match once.")
    new_text: str = Field(description="Replacement text.")
    force_generated: bool = Field(
        default=False, description="Explicitly allow editing a generated read-only artifact after approval."
    )
    expected_sha256: str = Field(description="SHA-256 returned by memory_read for the artifact being edited.")


class MemoryWriteInput(BaseModel):
    path: str = Field(
        description="Relative .md path under the memory root, e.g. feeds/pr-queue.md. Creates the page or replaces its whole content (update-in-place)."
    )
    content: str = Field(
        min_length=1,
        max_length=40_000,
        description="Full markdown content for the page (a compact briefing, not an append log).",
    )
    expected_sha256: str = Field(
        description="SHA-256 returned by memory_read, or the literal 'absent' when creating a new page."
    )


class RecallInput(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=20_000,
        description="A natural-language query; returns the most relevant durable fact/source records by default.",
    )
    kind: Literal["directive", "fact", "source", "lesson"] | None = Field(
        default=None,
        description="Optional kind to search. Defaults to fact+source (topical recall). Standing "
        "directives and learned lessons are always in the resident context, so they're excluded here "
        "by default — pass kind='directive' or kind='lesson' to search them explicitly. Live integration "
        "state (inbox, PR queue, …) lives on feeds/ pages — memory_read those, don't recall.",
    )


def _unavailable() -> ToolResult:
    return ToolResult.failure(
        code="not_configured",
        message="Memory is not available.",
        preview="Memory unavailable",
        recovery_action="Enable the memory record service before retrying.",
    )


def _render_records(records: list) -> str:
    lines = []
    for r in records:
        if r.scope_kind and r.scope_kind != "global":
            lines.append(f"- [{r.kind} {r.scope_kind}/{r.scope_key}] {r.text}")
        else:
            lines.append(f"- [{r.kind}] {r.text}")
    return "\n".join(lines)


def _artifact_source_ref(path: str, title: str | None = None) -> ToolSourceRef:
    return ToolSourceRef(provider="memory", kind="artifact", ref=path, title=title or path)


def _record_source_refs(records: list) -> tuple[ToolSourceRef, ...]:
    return normalize_source_refs(
        ToolSourceRef(
            provider="memory",
            kind="record",
            ref=str(record.id),
            title=str(record.text)[:256],
        )
        for record in records
        if getattr(record, "id", None)
    )


def _record_version(record: Any) -> str:
    sources = []
    for source in getattr(record, "sources", ()) or ():
        sources.append(source.to_dict() if hasattr(source, "to_dict") else str(source))
    payload = {
        "id": getattr(record, "id", None),
        "text": getattr(record, "text", None),
        "kind": getattr(record, "kind", None),
        "scope_kind": getattr(record, "scope_kind", None),
        "scope_key": getattr(record, "scope_key", None),
        "created_at": getattr(record, "created_at", None),
        "last_confirmed_at": getattr(record, "last_confirmed_at", None),
        "superseded_by": getattr(record, "superseded_by", None),
        "pinned": getattr(record, "pinned", False),
        "sources": sources,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _candidate_data(record: Any) -> dict[str, object]:
    return {
        "memory_ref": record.id,
        "version": _record_version(record),
        "text": record.text,
        "kind": record.kind,
        "scope_kind": getattr(record, "scope_kind", None),
        "scope_key": getattr(record, "scope_key", None),
    }


def _artifact_store() -> ArtifactMemoryStore:
    return ArtifactMemoryStore(get_config().memory_artifacts_dir)


def _path_error(path: str) -> ToolResult:
    return ToolResult.failure(
        code="not_found",
        message=f"Invalid or unavailable memory artifact path: {path}",
        preview="Invalid path",
        recovery_action="Call memory_tree and retry with an exact returned relative path.",
    )


def _memory_filesystem_failure(operation: str) -> ToolResult:
    return ToolResult.failure(
        code="filesystem_error",
        message=f"Could not {operation} the memory artifact.",
        preview=f"{operation.title()} failed",
        retryable=True,
        recovery_action="Retry once; if it repeats, inspect memory health and filesystem permissions.",
    )


def _validate_relative_path(raw: str, *, allow_empty: bool = False) -> str | None:
    text = (raw or "").strip()
    if not text:
        return "" if allow_empty else None
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        return None
    if any(part in _ENGINE_MEMORY_PATHS for part in path.parts):
        return None
    rel = path.as_posix().strip("/")
    if not rel or rel == ".":
        return "" if allow_empty else None
    if rel.endswith("/"):
        rel = rel.rstrip("/")
    if Path(rel).suffix.casefold() not in {"", ".md", ".txt"}:
        return None
    return rel


def _artifact_ref_slug(text: str) -> str:
    text = text.strip()
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text)
    return text.strip(".-_").lower()[:60].strip(".-_")


def _normalize_artifact_ref(raw: str) -> str:
    text = (raw or "").strip().strip("`").strip()
    if text.startswith("[[") and text.endswith("]]"):
        text = text[2:-2].strip()
    return text.strip()


def _preferred_artifact_match(paths: list[str], slug: str) -> str | None:
    unique = set(paths)
    if len(unique) == 1:
        return next(iter(unique))
    for candidate in (
        f"topics/{slug}.md",
        f"entities/{slug}.md",  # legacy fallback (pre-unification leftovers)
        f"projects/{slug}.md",
        f"context/integrations/{slug}.md",
    ):
        if candidate in unique:
            return candidate
    return None


def _resolve_artifact_read_path(store: ArtifactMemoryStore, raw: str) -> str | None:
    ref = _normalize_artifact_ref(raw)
    if not ref:
        return None

    rel = _validate_relative_path(ref, allow_empty=False)
    if rel is not None:
        candidates = [rel]
        if Path(rel).suffix.casefold() not in {".md", ".txt"}:
            candidates.extend([f"{rel}/README.md", f"{rel}/index.md", f"{rel}.md", f"{rel}.txt"])
        for candidate in candidates:
            try:
                store.read_artifact(candidate)
                return candidate
            except FileNotFoundError:
                continue
    else:
        unsafe = Path(ref)
        if unsafe.is_absolute() or ".." in unsafe.parts or any(part in _ENGINE_MEMORY_PATHS for part in unsafe.parts):
            return None

    ref_lower = ref.lower()
    ref_slug = _artifact_ref_slug(ref)
    unsupported_suffix = bool(Path(ref).suffix and Path(ref).suffix.casefold() not in {".md", ".txt"})
    matches: list[str] = []
    for artifact in store.list_artifacts():
        path = artifact.path
        stem = Path(path).stem
        title_keys = {
            artifact.title.lower(),
            _artifact_ref_slug(artifact.title),
        }
        path_keys = {
            path.lower(),
            path.removesuffix(".md").lower(),
            stem.lower(),
            _artifact_ref_slug(stem),
        }
        keys = title_keys if unsupported_suffix else title_keys | path_keys
        if ref_lower in keys or ref_slug in keys:
            matches.append(path)
    return _preferred_artifact_match(matches, ref_slug)


def _artifact_generated(artifact) -> bool:
    return bool(getattr(artifact, "generated", True))


def _artifact_snippet(artifact, content: str) -> str | None:
    snippet = getattr(artifact, "snippet", None)
    if snippet:
        return str(snippet)
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:240]
    return None


def _safe_existing_file_path(root: Path, rel: str) -> Path | None:
    parts = Path(rel).parts
    if not parts or any(part in {"", ".", ".."} or part in _ENGINE_MEMORY_PATHS for part in parts):
        return None
    path = root.joinpath(*parts)
    try:
        root_real = root.resolve()
        if root_real not in path.resolve().parents:
            return None
        current = root
        for part in parts[:-1]:
            current = current / part
            st = current.lstat()
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                return None
        st = path.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return None
    return path


def _read_text_no_symlink(path: Path) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        os.close(fd)
        raise


def _unified_diff(rel: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
    )


def _revision_bound_diff(diff: str, expected_sha256: str) -> str:
    body = diff.rstrip() + "\n\n" if diff else ""
    return f"{body}Expected SHA-256: {expected_sha256}"


def _write_conflict(rel: str, expected_sha256: str, observed: str) -> ToolResult:
    return ToolResult.failure(
        code="write_conflict",
        message=(
            f"Memory artifact {rel} changed since it was read. "
            f"Expected {expected_sha256}, observed {observed}. Read it again before writing."
        ),
        preview="Write conflict",
        recovery_action="Call memory_read, recompute the change, and retry with its new sha256.",
    )


def _format_lines(lines: list[str], start_line: int) -> str:
    return "\n".join(f"{i:6}| {line}" for i, line in enumerate(lines, start=start_line))


def _list_artifacts_for_path(store: ArtifactMemoryStore, rel: str):
    artifacts = store.list_artifacts()
    if not rel:
        return artifacts
    prefix = rel.rstrip("/") + "/"
    return [a for a in artifacts if a.path == rel or a.path.startswith(prefix)]


def _memory_tree_sync(args: MemoryTreeInput) -> ToolResult:
    rel = _validate_relative_path(args.path, allow_empty=True)
    if rel is None:
        return _path_error(args.path)
    store = _artifact_store()
    try:
        artifacts = _list_artifacts_for_path(store, rel)
    except OSError:
        return _memory_filesystem_failure("read")
    if rel and not artifacts:
        return _path_error(args.path)
    root_label = rel or "memory"
    lines = [root_label]
    rows = []
    dirs: set[str] = set()
    base_depth = len(Path(rel).parts) if rel else 0
    max_abs_depth = base_depth + args.depth
    for artifact in artifacts:
        parts = Path(artifact.path).parts
        max_parts = min(len(parts), max_abs_depth)
        for i in range(1, max_parts):
            dirs.add("/".join(parts[:i]))
    for d in sorted(dirs):
        if rel and not (d == rel or d.startswith(rel.rstrip("/") + "/") or rel.startswith(d.rstrip("/") + "/")):
            continue
        indent = "  " * len(Path(d).parts)
        lines.append(f"{indent}{Path(d).name}/")
    for artifact in artifacts:
        parts = Path(artifact.path).parts
        if len(parts) - base_depth > args.depth:
            continue
        content = ""
        snippet = getattr(artifact, "snippet", None)
        if args.include_content:
            try:
                read = store.read_artifact(artifact.path)
                content = read.content
                snippet = _artifact_snippet(read, content)
            except FileNotFoundError:
                continue
        indent = "  " * len(parts)
        meta = f" [{artifact.kind}]"
        if getattr(artifact, "generated", True):
            meta += " generated"
        if getattr(artifact, "editable", False):
            meta += " editable"
        suffix = f" — {snippet[:160]}" if args.include_content and snippet else ""
        lines.append(f"{indent}{parts[-1]}{meta}{suffix}")
        rows.append(
            {
                "path": artifact.path,
                "title": artifact.title,
                "kind": artifact.kind,
                "directory": getattr(artifact, "directory", parts[0] if len(parts) > 1 else "memory"),
            }
        )
    return ToolResult(
        content="\n".join(lines),
        preview=f"{len(rows)} artifact(s)",
        data={"root": str(store.root), "path": rel, "artifacts": rows},
        source_refs=normalize_source_refs(
            _artifact_source_ref(str(row["path"]), str(row.get("title") or row["path"])) for row in rows
        ),
    )


async def memory_tree(execution: ToolExecution, args: MemoryTreeInput) -> ToolResult:
    return await asyncio.to_thread(_memory_tree_sync, args)


def _memory_read_sync(args: MemoryReadInput) -> ToolResult:
    store = _artifact_store()
    rel = _resolve_artifact_read_path(store, args.path)
    if rel is None or Path(rel).suffix.casefold() not in {".md", ".txt"}:
        return _path_error(args.path)
    try:
        artifact = store.read_artifact(rel)
    except (FileNotFoundError, OSError):
        return _path_error(args.path)
    lines = artifact.content.splitlines()
    raw_content = artifact.raw_content or artifact.content
    start = min(args.offset, len(lines) + 1)
    selected = lines[start - 1 : start - 1 + args.limit]
    content = _format_lines(selected, start) if selected else ""
    return ToolResult(
        content=content,
        preview=f"{len(selected)} line(s)",
        data={
            "path": artifact.path,
            "title": artifact.title,
            "kind": artifact.kind,
            "offset": args.offset,
            "limit": args.limit,
            "lines": len(lines),
            "sha256": artifact.revision,
            "size": len(raw_content.encode("utf-8")),
        },
        source_refs=(_artifact_source_ref(artifact.path, artifact.title),),
    )


async def memory_read(execution: ToolExecution, args: MemoryReadInput) -> ToolResult:
    return await asyncio.to_thread(_memory_read_sync, args)


def _memory_search_sync(args: MemorySearchInput) -> ToolResult:
    rel = _validate_relative_path(args.path, allow_empty=True)
    if rel is None:
        return _path_error(args.path)
    needle = args.query.lower()
    store = _artifact_store()
    try:
        artifacts = _list_artifacts_for_path(store, rel)
    except OSError:
        return _memory_filesystem_failure("search")
    if rel and not artifacts:
        return _path_error(args.path)
    if rel and Path(rel).suffix.casefold() in {".md", ".txt"} and not any(a.path == rel for a in artifacts):
        return _path_error(args.path)
    matches = []
    for artifact in artifacts:
        try:
            read = store.read_artifact(artifact.path)
        except (FileNotFoundError, OSError):
            continue
        haystack_fields = [
            read.path,
            read.title,
            read.kind,
            getattr(read, "directory", ""),
            _artifact_snippet(read, read.content) or "",
        ]
        if any(needle in str(field).lower() for field in haystack_fields):
            matches.append({"path": read.path, "line": 1, "snippet": (read.title or read.path)[:300]})
            if len(matches) >= args.limit:
                break
            continue
        for lineno, line in enumerate(read.content.splitlines(), start=1):
            if needle in line.lower():
                matches.append({"path": read.path, "line": lineno, "snippet": line.strip()[:300]})
                break
        if len(matches) >= args.limit:
            break
    if not matches:
        return ToolResult(content="0 matches", preview="0 matches", data={"query": args.query, "matches": []})
    return ToolResult(
        content="\n".join(f"{m['path']}:{m['line']}: {m['snippet']}" for m in matches),
        preview=f"{len(matches)} match(es)",
        data={"query": args.query, "path": rel, "matches": matches},
        source_refs=normalize_source_refs(_artifact_source_ref(str(match["path"])) for match in matches),
    )


async def memory_search(execution: ToolExecution, args: MemorySearchInput) -> ToolResult:
    return await asyncio.to_thread(_memory_search_sync, args)


def _patch_preview(args: MemoryPatchInput) -> tuple[str, str, str, object] | None:
    rel = _validate_relative_path(args.path)
    if rel is None or not rel.endswith(".md"):
        return None
    store = _artifact_store()
    try:
        artifact = store.read_artifact(rel)
    except (FileNotFoundError, OSError):
        return None
    before = artifact.content
    if before.count(args.old_text) != 1:
        return None
    after = before.replace(args.old_text, args.new_text, 1)
    return rel, before, after, artifact


def _approve_memory_patch_sync(args: MemoryPatchInput) -> ApprovalInfo | None:
    preview = _patch_preview(args)
    if preview is None:
        return None
    rel, before, after, artifact = preview
    description = (
        f"Force edit generated memory artifact {rel}"
        if args.force_generated and _artifact_generated(artifact)
        else f"Edit memory artifact {rel}"
    )
    return ApprovalInfo(
        description=description,
        preview=args.new_text[:500],
        diff=_revision_bound_diff(_unified_diff(rel, before, after), args.expected_sha256),
    )


async def approve_memory_patch(execution: ToolExecution, args: MemoryPatchInput) -> ApprovalInfo | None:
    return await asyncio.to_thread(_approve_memory_patch_sync, args)


def _memory_patch_sync(args: MemoryPatchInput) -> ToolResult:
    rel = _validate_relative_path(args.path)
    if rel is None:
        return _path_error(args.path)
    store = _artifact_store()
    path = _safe_existing_file_path(store.root, rel)
    if path is None:
        return _path_error(args.path)
    observed = revision_or_absent(path)
    if observed != args.expected_sha256:
        return _write_conflict(rel, args.expected_sha256, observed)
    preview = _patch_preview(args)
    if preview is None:
        try:
            artifact = store.read_artifact(rel)
            count = artifact.content.count(args.old_text)
        except (FileNotFoundError, OSError):
            return _path_error(args.path)
        if count == 0:
            return ToolResult.failure(
                code="not_found",
                message="Text block not found.",
                preview="No match",
                recovery_action="Read the artifact and include more exact context.",
            )
        return ToolResult.failure(
            code="ambiguous_ref",
            message=f"Text block matched {count} times.",
            preview="Ambiguous",
            recovery_action="Include a larger exact block so the edit is unique.",
        )
    rel, _before, after, artifact = preview
    try:
        raw = _read_text_no_symlink(path)
    except OSError:
        return _path_error(args.path)
    fm, _body = parse_frontmatter(raw)
    if bool(fm.get("generated", _artifact_generated(artifact))) and not args.force_generated:
        return ToolResult.failure(
            code="permission_denied",
            message=f"Refusing to edit generated memory artifact {rel}.",
            preview="Generated artifact",
            recovery_action="Use record tools for durable facts or set force_generated=true with approval.",
        )
    frontmatter = raw[: len(raw) - len(_body)] if fm else ""
    try:
        revision = atomic_compare_and_swap(path, frontmatter + after, args.expected_sha256)
    except RevisionConflict as conflict:
        return _write_conflict(rel, conflict.expected, conflict.observed)
    except OSError:
        return _memory_filesystem_failure("patch")
    return ToolResult(
        content=f"Patched memory artifact {rel}.",
        preview="Patched",
        data={"path": rel, "sha256": revision.sha256, "size": revision.size},
        outcome=ToolOutcome(
            status=ToolOutcomeStatus.SUCCEEDED,
            effect=ToolEffect(
                operation="edit",
                target=str(path),
                before_ref=args.expected_sha256,
                after_ref=revision.sha256,
            ),
        ),
    )


async def memory_patch(execution: ToolExecution, args: MemoryPatchInput) -> ToolResult:
    return await asyncio.to_thread(_memory_patch_sync, args)


def _write_target(args: MemoryWriteInput) -> tuple[str, str] | ToolResult:
    """Validate a memory_write target; returns (rel, existing_content) or an error.

    Feed pages (any page WITHOUT a raw/ timeline sidecar) are whole-page writable —
    they are the automation-owned, update-in-place layer. Record-backed pages are
    compiled from their records, so a whole-page write would be clobbered by the
    next synthesis; those keep flowing through remember/memory_patch."""
    rel = _validate_relative_path(args.path)
    if rel is None or not rel.endswith(".md"):
        return _path_error(args.path)
    store = _artifact_store()
    parts = Path(rel).parts
    allowed_write = rel in ROOT_ARTIFACTS or (len(parts) > 1 and parts[0] in ARTIFACT_DIR_KINDS)
    if not allowed_write or not store._allowed_artifact_rel(rel) or rel.startswith("changelog/"):
        return _path_error(args.path)
    try:
        artifact = store.read_artifact(rel)
        if artifact.generated:
            return ToolResult.failure(
                code="permission_denied",
                message=f"{rel} is a generated report rebuilt from source pages.",
                preview="Generated artifact",
                recovery_action="Edit the source page or record instead.",
            )
    except FileNotFoundError:
        pass  # creating a new page
    if (store.root / "raw" / rel).is_file():
        return ToolResult.failure(
            code="permission_denied",
            message=f"{rel} is a record-backed page and cannot be replaced wholesale.",
            preview="Record-backed page",
            recovery_action="Use remember for new facts or memory_patch for a prose correction.",
        )
    existing = ""
    path = _safe_existing_file_path(store.root, rel)
    if path is not None:
        try:
            existing = _read_text_no_symlink(path)
        except OSError:
            return _path_error(args.path)
    return rel, existing


def _approve_memory_write_sync(args: MemoryWriteInput) -> ApprovalInfo | None:
    target = _write_target(args)
    if isinstance(target, ToolResult):
        return None
    rel, existing = target
    verb = "Update" if existing else "Create"
    return ApprovalInfo(
        description=f"{verb} memory page {rel}",
        preview=args.content[:500],
        diff=_revision_bound_diff(_unified_diff(rel, existing, args.content), args.expected_sha256),
    )


async def approve_memory_write(execution: ToolExecution, args: MemoryWriteInput) -> ApprovalInfo | None:
    return await asyncio.to_thread(_approve_memory_write_sync, args)


def _memory_write_sync(args: MemoryWriteInput) -> ToolResult:
    target = _write_target(args)
    if isinstance(target, ToolResult):
        return target
    rel, existing = target
    store = _artifact_store()
    path = store.root.joinpath(*Path(rel).parts)
    observed = revision_or_absent(path)
    if observed != args.expected_sha256:
        return _write_conflict(rel, args.expected_sha256, observed)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = args.content if args.content.endswith("\n") else args.content + "\n"
        revision = atomic_compare_and_swap(path, content, args.expected_sha256)
    except RevisionConflict as conflict:
        return _write_conflict(rel, conflict.expected, conflict.observed)
    except OSError:
        return _memory_filesystem_failure("write")
    verb = "Updated" if existing else "Created"
    return ToolResult(
        content=f"{verb} memory page {rel}.",
        preview=verb,
        data={"path": rel, "sha256": revision.sha256, "size": revision.size},
        outcome=ToolOutcome(
            status=ToolOutcomeStatus.SUCCEEDED,
            effect=ToolEffect(
                operation="create" if args.expected_sha256 == "absent" else "replace",
                target=str(path),
                before_ref=args.expected_sha256,
                after_ref=revision.sha256,
            ),
        ),
    )


async def memory_write(execution: ToolExecution, args: MemoryWriteInput) -> ToolResult:
    return await asyncio.to_thread(_memory_write_sync, args)


def _normalize_text(text: str) -> str:
    return " ".join(text.split()).strip().lower().rstrip(".!?")


async def _direct_sources(
    execution: ToolExecution,
    scope: MemoryScope,
) -> tuple[SourceRef, ...]:
    session_id = getattr(getattr(execution.ctx, "session_state", None), "session_id", None) or execution.ctx.session_id
    tool_source = apply_scope_to_source(
        SourceRef(
            kind="tool_call",
            ref=execution.tool_id,
            extra={"session_id": session_id, "tool_name": getattr(execution, "tool_name", "memory")},
        ),
        scope,
    )
    sources = [tool_source]
    sessions = execution.ctx.services.get("session")
    if sessions is None:
        return tuple(sources)
    try:
        page = await sessions.list_messages(session_id, limit=20)
        messages = page.get("messages", []) if isinstance(page, dict) else []
    except Exception:
        _logger.warning("failed to load triggering user evidence for memory tool", exc_info=True)
        return tuple(sources)
    triggering = next(
        (row for row in reversed(messages) if row.get("role") == "user" and row.get("message_id")),
        None,
    )
    if triggering is not None:
        occurred_at, precision = source_time(triggering.get("created_at"))
        sources.append(
            SourceRef(
                kind="chat_message",
                ref=triggering["message_id"],
                scope_kind=scope.kind,
                scope_key=scope.key,
                occurred_at=occurred_at,
                time_precision=precision,
                role="user",
                extra={"session_id": session_id, "seq": triggering.get("seq")},
            )
        )
    return tuple(sources)


async def remember(execution: ToolExecution, args: RememberInput) -> ToolResult:
    store = execution.ctx.services.get(MEMORY_RECORDS_SERVICE)
    if store is None:
        return _unavailable()

    session_id = getattr(getattr(execution.ctx, "session_state", None), "session_id", None) or execution.ctx.session_id
    project = execution.ctx.area
    visible = [(s.kind, s.key) for s in scopes_for_read(project=project, session_id=session_id)]

    base = SourceRef(kind="tool_call", ref=execution.tool_id)
    scope = scope_for_write(
        kind=args.kind,
        project=project,
        session_id=session_id,
        source_ref=base,
    )
    sources = await _direct_sources(execution, scope)

    # Exact normalized equality is the only deterministic shortcut. Every other
    # candidate is reconciled by the same typed model path as background curation.
    normalized = _normalize_text(args.text)
    for hit in await store.search(args.text, limit=20, scopes=visible):
        if normalized == _normalize_text(hit.text):
            validate_operations([RecordOperation.noop()], (), sources)
            return ToolResult(content=f"Already known: {hit.text}", preview="Already known")

    reconciler = execution.ctx.services.get(MEMORY_RECONCILER_SERVICE)
    if reconciler is None:
        return ToolResult.failure(
            code="not_configured",
            message="Memory reconciliation is unavailable; nothing was changed.",
            preview="Reconciliation unavailable",
            recovery_action="Enable the memory reconciler before retrying.",
        )
    operations = await reconciler.reconcile_direct_memory(
        text=args.text,
        kind=args.kind,
        scope=scope,
        sources=sources,
        session_id=session_id,
        tool_call_id=execution.tool_id,
    )
    if operations is None:
        return ToolResult.failure(
            code="temporarily_unavailable",
            message="Memory reconciliation is unavailable; nothing was changed.",
            preview="Reconciliation unavailable",
            retryable=True,
            recovery_action="Retry once; if it repeats, inspect memory health.",
        )
    if not operations:
        return ToolResult.failure(
            code="reconciliation_failed",
            message="Memory reconciliation returned no decision; nothing was changed.",
            preview="Reconciliation unavailable",
            recovery_action="Rephrase the memory as one self-contained statement and retry.",
        )
    if question := next((operation.question for operation in operations if operation.op == "ASK"), None):
        return ToolResult(
            content=question,
            preview="Clarification required",
            data={"clarification_required": True},
        )
    if all(operation.op == "NOOP" for operation in operations):
        return ToolResult(content="No memory change was needed.", preview="Already known")
    return ToolResult(content="Remembered", preview="Remembered")


def _visible_memory_scopes(execution: ToolExecution) -> list[tuple[str | None, str | None]]:
    session_id = getattr(getattr(execution.ctx, "session_state", None), "session_id", None) or execution.ctx.session_id
    return [(scope.kind, scope.key) for scope in scopes_for_read(project=execution.ctx.area, session_id=session_id)]


def _record_is_visible(record: Any, visible: list[tuple[str | None, str | None]]) -> bool:
    scope = (getattr(record, "scope_kind", None), getattr(record, "scope_key", None))
    return scope in visible or (scope == (None, None) and ("global", None) in visible)


async def search_memory_candidates(
    execution: ToolExecution,
    args: SearchMemoryCandidatesInput,
) -> ToolResult:
    store = execution.ctx.services.get(MEMORY_RECORDS_SERVICE)
    if store is None:
        return _unavailable()

    hits = await store.search(args.query, limit=args.limit, scopes=_visible_memory_scopes(execution))
    if not hits:
        return ToolResult(content="No matching memory candidates.", preview="0 candidates", data={"candidates": []})

    candidates = [_candidate_data(hit) for hit in hits]
    lines = [
        f"- [{candidate['kind']}] {candidate['text']}\n"
        f"  memory_ref: {candidate['memory_ref']}\n"
        f"  version: {candidate['version']}"
        for candidate in candidates
    ]
    return ToolResult(
        content="\n".join(lines),
        preview=f"{len(candidates)} candidate(s)",
        data={"candidates": candidates},
        source_refs=_record_source_refs(hits),
    )


async def _forget_record(execution: ToolExecution, args: ForgetInput) -> tuple[Any | None, ToolResult | None]:
    store = execution.ctx.services.get(MEMORY_RECORDS_SERVICE)
    if store is None:
        return None, _unavailable()
    record = await store.get(args.memory_ref)
    if record is None:
        return None, ToolResult.failure(
            code="write_conflict",
            message="The selected memory no longer exists. Search again before deleting.",
            preview="Memory changed",
            recovery_action="Call search_memory_candidates again and retry with a current reference and version.",
        )
    if not _record_is_visible(record, _visible_memory_scopes(execution)):
        return None, ToolResult.failure(
            code="permission_denied",
            message="The selected memory is outside the current readable scope.",
            preview="Outside scope",
            recovery_action="Search within the current session or Area scope.",
        )
    observed = _record_version(record)
    if observed != args.expected_version:
        return None, ToolResult.failure(
            code="write_conflict",
            message="The selected memory changed after it was proposed. Nothing was deleted.",
            preview="Memory changed",
            recovery_action="Call search_memory_candidates again and retry with the new version.",
        )
    return record, None


async def approve_forget(execution: ToolExecution, args: ForgetInput) -> ApprovalInfo | None:
    record, failure = await _forget_record(execution, args)
    if failure is not None or record is None:
        return None
    return ApprovalInfo(
        description=f"Forget memory {args.memory_ref} at version {args.expected_version}",
        preview=str(record.text)[:1_500],
        diff=f"- {record.text}\n  memory_ref: {args.memory_ref}\n  version: {args.expected_version}",
    )


async def forget(execution: ToolExecution, args: ForgetInput) -> ToolResult:
    record, failure = await _forget_record(execution, args)
    if failure is not None or record is None:
        return failure or ToolResult.failure(
            code="not_found",
            message="The selected memory was not found.",
            preview="Not found",
        )

    store = execution.ctx.services[MEMORY_RECORDS_SERVICE]
    session_id = getattr(getattr(execution.ctx, "session_state", None), "session_id", None) or execution.ctx.session_id
    scope = MemoryScope(getattr(record, "scope_kind", None) or "user", getattr(record, "scope_key", None))
    sources = await _direct_sources(execution, scope)
    records = tuple(await store.list(limit=None, scopes=None))
    operations = validate_operations([RecordOperation.retract(record.id)], records, sources)

    if hasattr(store, "apply_operations"):
        store.apply_operations(
            operations,
            sources,
            batch_key=f"direct-forget:{session_id}:{execution.tool_id}",
        )
    else:
        # Test-only legacy SQLite compatibility. Runtime memory is the schema-v2
        # FilePageStore above, which journals the evidenced RETRACT operation.
        await store.delete(record.id)

    deleted = _candidate_data(record)
    return ToolResult(
        content=f"Forgot: {record.text}",
        preview="Forgotten",
        data={
            "deleted": deleted,
            "undo": {"tool": "remember", "text": record.text, "kind": record.kind},
        },
        outcome=ToolOutcome(
            status=ToolOutcomeStatus.SUCCEEDED,
            effect=ToolEffect(
                operation="delete",
                target=args.memory_ref,
                before_ref=args.expected_version,
                after_ref="absent",
            ),
        ),
    )


async def recall(execution: ToolExecution, args: RecallInput) -> ToolResult:
    store = execution.ctx.services.get(MEMORY_RECORDS_SERVICE)
    if store is None:
        return _unavailable()

    session_id = getattr(getattr(execution.ctx, "session_state", None), "session_id", None) or execution.ctx.session_id
    visible = [(s.kind, s.key) for s in scopes_for_read(project=execution.ctx.area, session_id=session_id)]
    # Topical recall by default: facts + source pointers. Directives and lessons are
    # standing rules already in the resident context every turn — including them here
    # let high-salience rules bury the topical facts a query is actually asking for
    # (the eval went 65% -> 95% once they were excluded).
    kinds = [args.kind] if args.kind else ["fact", "source"]
    hits = await store.search(args.query, limit=10, scopes=visible, kinds=kinds)
    if not hits:
        return ToolResult(content="No matching memory.", preview="No matches")
    content = _render_records(hits)
    nudge = _entity_brief_nudge(args.query)
    if nudge:
        content += "\n\n" + nudge
    return ToolResult(content=content, preview=f"{len(hits)} match(es)", source_refs=_record_source_refs(hits))


def _entity_brief_nudge(query: str) -> str | None:
    """If the query slug matches an existing compiled topic page, point the
    agent at it. Read-only filesystem probe; never raises into the recall path."""
    from ntrp.memory.artifacts import _slug

    slug = _slug(query, fallback="")
    if not slug:
        return None
    try:
        if (get_config().memory_artifacts_dir / "topics" / f"{slug}.md").is_file():
            return f'_Compiled brief: memory_read("topics/{slug}.md")_'
    except Exception:
        return None
    return None


_MEMORY_FS_DESCRIPTION = (
    "The memory wiki is plain markdown: me.md (profile), directives.md, topics/<slug>.md "
    "(one page per subject — people, products, projects), feeds/ (automation-owned "
    "briefings, updated in place), insights/, daily/. "
    "Use recall for atomic facts; use memory_tree for the live file tree, then memory_read a "
    "page by path/title to go deep."
)

memory_tree_tool = tool(
    display_name="MemoryTree",
    description="Browse the memory artifact filesystem tree. " + _MEMORY_FS_DESCRIPTION,
    input_model=MemoryTreeInput,
    policy=ToolPolicy(
        action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=frozenset({MEMORY_RECORDS_SERVICE})
    ),
    execute=memory_tree,
)

memory_read_tool = tool(
    display_name="MemoryRead",
    description=(
        "Read a safe relative .md memory artifact with line offsets and its SHA-256 revision. " + _MEMORY_FS_DESCRIPTION
    ),
    input_model=MemoryReadInput,
    policy=ToolPolicy(
        action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=frozenset({MEMORY_RECORDS_SERVICE})
    ),
    execute=memory_read,
)

memory_search_tool = tool(
    display_name="MemorySearch",
    description="Search safe memory artifact filenames, titles, snippets, and markdown content. "
    + _MEMORY_FS_DESCRIPTION,
    input_model=MemorySearchInput,
    policy=ToolPolicy(
        action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=frozenset({MEMORY_RECORDS_SERVICE})
    ),
    execute=memory_search,
)

memory_patch_tool = tool(
    display_name="MemoryPatch",
    description=(
        "Patch a unique exact text block using the sha256 from memory_read. Requires approval; "
        "refuses generated artifacts unless force_generated is explicit. " + _MEMORY_FS_DESCRIPTION
    ),
    input_model=MemoryPatchInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=frozenset({MEMORY_RECORDS_SERVICE}),
    ),
    approval=approve_memory_patch,
    execute=memory_patch,
)

memory_write_tool = tool(
    display_name="MemoryWrite",
    description=(
        "Create or update-in-place a whole memory page (e.g. a feeds/<slug>.md briefing "
        "owned by an automation). Replaces the entire page — write the full current "
        "briefing, never an appended log. Pass memory_read's sha256 when replacing, or "
        "expected_sha256='absent' when creating. Refuses generated reports and record-backed "
        "pages (those compile from records; use remember/memory_patch). Requires approval. " + _MEMORY_FS_DESCRIPTION
    ),
    input_model=MemoryWriteInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=frozenset({MEMORY_RECORDS_SERVICE}),
    ),
    approval=approve_memory_write,
    execute=memory_write,
)

remember_tool = tool(
    display_name="Remember",
    description=(
        "Durably remember a single self-contained statement about the user or "
        "their world. Use for stable preferences, decisions, and facts worth "
        "recalling in future sessions — not transient task state. State one "
        "statement per call; set `kind` to its function "
        "(directive | fact | source | lesson)."
    ),
    input_model=RememberInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({MEMORY_RECORDS_SERVICE, MEMORY_RECONCILER_SERVICE}),
    ),
    execute=remember,
)

search_memory_candidates_tool = tool(
    display_name="Search Memory Candidates",
    description=(
        "Find possible long-term memories to delete without changing anything. "
        "Returns exact memory_ref and version values required by forget."
    ),
    input_model=SearchMemoryCandidatesInput,
    policy=ToolPolicy(
        action=ToolAction.READ,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({MEMORY_RECORDS_SERVICE}),
    ),
    execute=search_memory_candidates,
)

forget_tool = tool(
    display_name="Forget",
    description=(
        "Delete one exact long-term memory previously returned by search_memory_candidates. "
        "Requires its stable memory_ref and version and fails if the record changed."
    ),
    input_model=ForgetInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        permissions=frozenset({MEMORY_RECORDS_SERVICE}),
    ),
    approval=approve_forget,
    execute=forget,
)

recall_tool = tool(
    display_name="Recall",
    description=(
        "Search long-term memory for records relevant to a natural-language "
        "query (hybrid lexical + semantic). Read-only; use it to look up what "
        "the user has decided, prefers, or done before."
    ),
    input_model=RecallInput,
    policy=ToolPolicy(
        action=ToolAction.READ,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({MEMORY_RECORDS_SERVICE}),
    ),
    execute=recall,
)
