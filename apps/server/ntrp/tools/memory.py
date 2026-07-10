"""Memory tools — the agent's ONLY entry to the record layer.

Plain tools over the scoped RecordStore (atomic, self-contained records in
`config.memory_db_path`):

- remember(text, kind?) -> RecordStore.add (write a record)
- forget(query)         -> hybrid-search records, delete the best hit
- recall(query)         -> hybrid record search (READ)

Records are one simple table with scope metadata for visibility, not a graph or
project hierarchy. Each tool only appears once `MEMORY_RECORDS_SERVICE` is wired
by the knowledge runtime, so they stay hidden when memory is off.

Self-correcting interface (the standing lesson): `forget` never requires the model
to reproduce an opaque id — it searches by NL query and, on a near-miss, lists the
other candidates instead of dead-ending.
"""

import asyncio
import difflib
import os
import re
import stat
from pathlib import Path

from pydantic import BaseModel, Field

from ntrp.config import get_config
from ntrp.logging import get_logger
from ntrp.memory.artifacts import ArtifactMemoryStore
from ntrp.memory.frontmatter import parse_frontmatter
from ntrp.memory.models import SourceRef
from ntrp.memory.scopes import apply_scope_to_source, scope_for_write, scopes_for_read
from ntrp.tools.core import ToolResult, tool
from ntrp.tools.core.context import ToolExecution
from ntrp.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope

_logger = get_logger(__name__)

MEMORY_RECORDS_SERVICE = "memory_records"


class RememberInput(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=20_000,
        description=(
            "A single durable, self-contained statement to remember about the "
            "user or their world, stated plainly (resolve pronouns inline)."
        ),
    )
    kind: str = Field(
        default="fact",
        pattern="^(directive|fact|source|lesson)$",
        description=(
            "The record's function: directive | fact | source | lesson. "
            "Preferences and project facts are facts with the right scope; a standing rule the user states is a directive; "
            "a working pattern YOU distilled that should change how you act next time is a lesson (rides your resident playbook)."
        ),
    )


class ForgetInput(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=20_000,
        description=(
            "A natural-language description of the memory to forget. The "
            "best-matching record is removed (no id required)."
        ),
    )


class MemoryTreeInput(BaseModel):
    path: str = Field(default="", description="Relative directory or .md file path under the memory artifact root.")
    depth: int = Field(default=4, ge=1, le=12, description="Maximum directory depth to include.")
    include_content: bool = Field(default=False, description="Include bounded artifact snippets in tree rows.")


class MemoryReadInput(BaseModel):
    path: str = Field(description="Relative .md artifact path, directory, title, file stem, or [[wikilink]] under memory artifacts.")
    offset: int = Field(default=1, ge=1, description="1-based line number to start reading.")
    limit: int = Field(default=500, ge=1, le=2000, description="Maximum lines to return.")


class MemorySearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=500, description="Text to search in artifact filenames, titles, snippets, and content.")
    path: str = Field(default="", description="Optional relative directory or .md file path under the memory artifact root.")
    limit: int = Field(default=100, ge=1, le=500, description="Maximum matches to return.")


class MemoryPatchInput(BaseModel):
    path: str = Field(description="Relative .md artifact path under the memory artifact root.")
    old_text: str = Field(min_length=1, description="Exact existing text block to replace. Must match once.")
    new_text: str = Field(description="Replacement text.")
    force_generated: bool = Field(default=False, description="Explicitly allow editing a generated read-only artifact after approval.")


class MemoryWriteInput(BaseModel):
    path: str = Field(description="Relative .md path under the memory root, e.g. feeds/pr-queue.md. Creates the page or replaces its whole content (update-in-place).")
    content: str = Field(min_length=1, max_length=40_000, description="Full markdown content for the page (a compact briefing, not an append log).")


class MemoryRebuildInput(BaseModel):
    reason: str | None = Field(default=None, max_length=500, description="Optional short reason for the rebuild audit log.")


class RecallInput(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=20_000,
        description="A natural-language query; returns the most relevant durable fact/source records by default.",
    )
    kinds: list[str] | None = Field(
        default=None,
        description="Optional kinds to search. Defaults to fact+source (topical recall). Standing "
        "directives and learned lessons are always in the resident context, so they're excluded here "
        "by default — pass kinds=['directive'] or ['lesson'] to search them explicitly. Live integration "
        "state (inbox, PR queue, …) lives on feeds/ pages — memory_read those, don't recall.",
    )


def _unavailable() -> ToolResult:
    return ToolResult(
        content="Memory is not available.",
        preview="Memory unavailable",
        is_error=True,
    )


def _render_records(records: list) -> str:
    lines = []
    for r in records:
        if r.scope_kind and r.scope_kind != "global":
            lines.append(f"- [{r.kind} {r.scope_kind}/{r.scope_key}] {r.text}")
        else:
            lines.append(f"- [{r.kind}] {r.text}")
    return "\n".join(lines)



def _artifact_store() -> ArtifactMemoryStore:
    return ArtifactMemoryStore(get_config().memory_artifacts_dir)


def _path_error(path: str) -> ToolResult:
    return ToolResult(content=f"Invalid or unavailable memory artifact path: {path}", preview="Invalid path", is_error=True)


def _validate_relative_path(raw: str, *, allow_empty: bool = False) -> str | None:
    text = (raw or "").strip()
    if not text:
        return "" if allow_empty else None
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        return None
    if any(part.startswith(".") for part in path.parts if part not in {"", "."}):
        return None
    rel = path.as_posix().strip("/")
    if not rel or rel == ".":
        return "" if allow_empty else None
    if rel.endswith("/"):
        rel = rel.rstrip("/")
    if Path(rel).suffix and not rel.endswith(".md"):
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
        if not rel.endswith(".md"):
            candidates.extend([f"{rel}/index.md", f"{rel}.md"])
        for candidate in candidates:
            try:
                store.read_artifact(candidate)
                return candidate
            except FileNotFoundError:
                continue
    else:
        unsafe = Path(ref)
        if unsafe.is_absolute() or ".." in unsafe.parts or any(part.startswith(".") for part in unsafe.parts):
            return None

    ref_lower = ref.lower()
    ref_slug = _artifact_ref_slug(ref)
    unsupported_suffix = bool(Path(ref).suffix and not ref.endswith(".md"))
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
    if not parts or any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
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


def _write_text_no_symlink(path: Path, text: str, *, create: bool = False) -> None:
    flags = os.O_WRONLY | os.O_TRUNC
    if create:
        flags |= os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
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
    except OSError as exc:
        return ToolResult(content=f"Error reading memory artifacts: {exc}", preview="Read failed", is_error=True)
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
        rows.append({"path": artifact.path, "title": artifact.title, "kind": artifact.kind, "directory": getattr(artifact, "directory", parts[0] if len(parts)>1 else "memory")})
    return ToolResult(content="\n".join(lines), preview=f"{len(rows)} artifact(s)", data={"root": str(store.root), "path": rel, "artifacts": rows})


async def memory_tree(execution: ToolExecution, args: MemoryTreeInput) -> ToolResult:
    return await asyncio.to_thread(_memory_tree_sync, args)


def _memory_read_sync(args: MemoryReadInput) -> ToolResult:
    store = _artifact_store()
    rel = _resolve_artifact_read_path(store, args.path)
    if rel is None or not rel.endswith(".md"):
        return _path_error(args.path)
    try:
        artifact = store.read_artifact(rel)
    except (FileNotFoundError, OSError):
        return _path_error(args.path)
    lines = artifact.content.splitlines()
    start = min(args.offset, len(lines) + 1)
    selected = lines[start - 1 : start - 1 + args.limit]
    content = _format_lines(selected, start) if selected else ""
    return ToolResult(content=content, preview=f"{len(selected)} line(s)", data={"path": artifact.path, "title": artifact.title, "kind": artifact.kind, "offset": args.offset, "limit": args.limit, "lines": len(lines)})


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
    except OSError as exc:
        return ToolResult(content=f"Error searching memory artifacts: {exc}", preview="Search failed", is_error=True)
    if rel and not artifacts:
        return _path_error(args.path)
    if rel and rel.endswith(".md") and not any(a.path == rel for a in artifacts):
        return _path_error(args.path)
    matches = []
    for artifact in artifacts:
        try:
            read = store.read_artifact(artifact.path)
        except (FileNotFoundError, OSError):
            continue
        haystack_fields = [read.path, read.title, read.kind, getattr(read, "directory", ""), _artifact_snippet(read, read.content) or ""]
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
    return ToolResult(content="\n".join(f"{m['path']}:{m['line']}: {m['snippet']}" for m in matches), preview=f"{len(matches)} match(es)", data={"query": args.query, "path": rel, "matches": matches})


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
    description = f"Force edit generated memory artifact {rel}" if args.force_generated and _artifact_generated(artifact) else f"Edit memory artifact {rel}"
    return ApprovalInfo(description=description, preview=args.new_text[:500], diff=_unified_diff(rel, before, after))


async def approve_memory_patch(execution: ToolExecution, args: MemoryPatchInput) -> ApprovalInfo | None:
    return await asyncio.to_thread(_approve_memory_patch_sync, args)


def _memory_patch_sync(args: MemoryPatchInput) -> ToolResult:
    preview = _patch_preview(args)
    if preview is None:
        rel = _validate_relative_path(args.path)
        if rel is None:
            return _path_error(args.path)
        store = _artifact_store()
        try:
            artifact = store.read_artifact(rel)
            count = artifact.content.count(args.old_text)
        except (FileNotFoundError, OSError):
            return _path_error(args.path)
        if count == 0:
            return ToolResult(content="Text block not found. Read the artifact and include more exact context.", preview="No match", is_error=True)
        return ToolResult(content=f"Text block matched {count} times. Include a larger exact block so the edit is unique.", preview="Ambiguous", is_error=True)
    rel, _before, after, artifact = preview
    store = _artifact_store()
    path = _safe_existing_file_path(store.root, rel)
    if path is None:
        return _path_error(args.path)
    try:
        raw = _read_text_no_symlink(path)
    except OSError:
        return _path_error(args.path)
    fm, _body = parse_frontmatter(raw)
    if bool(fm.get("generated", _artifact_generated(artifact))) and not args.force_generated:
        return ToolResult(content=f"Refusing to edit generated memory artifact {rel}; use recall/record tools for DB-backed facts or set force_generated=true with approval for projection-only edits.", preview="Generated artifact", is_error=True)
    frontmatter = raw[: len(raw) - len(_body)] if fm else ""
    try:
        _write_text_no_symlink(path, frontmatter + after)
    except OSError as exc:
        return ToolResult(content=f"Error patching memory artifact: {exc}", preview="Patch failed", is_error=True)
    return ToolResult(content=f"Patched memory artifact {rel}.", preview="Patched", data={"path": rel})


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
    if not store._allowed_artifact_rel(rel) or rel.startswith("changelog/"):
        return _path_error(args.path)
    try:
        artifact = store.read_artifact(rel)
        if artifact.generated:
            return ToolResult(content=f"{rel} is a generated report — it is rebuilt from the pages; there is nothing durable to write here.", preview="Generated artifact", is_error=True)
    except FileNotFoundError:
        pass  # creating a new page
    if (store.root / "raw" / rel).is_file():
        return ToolResult(
            content=f"{rel} is a record-backed page — its prose is compiled from records and a whole-page write would be overwritten. Use remember for new facts or memory_patch for a prose fix.",
            preview="Record-backed page",
            is_error=True,
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
        diff=_unified_diff(rel, existing, args.content),
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
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = args.content if args.content.endswith("\n") else args.content + "\n"
        _write_text_no_symlink(path, content, create=True)
    except OSError as exc:
        return ToolResult(content=f"Error writing memory page: {exc}", preview="Write failed", is_error=True)
    verb = "Updated" if existing else "Created"
    return ToolResult(content=f"{verb} memory page {rel}.", preview=verb, data={"path": rel})


async def memory_write(execution: ToolExecution, args: MemoryWriteInput) -> ToolResult:
    return await asyncio.to_thread(_memory_write_sync, args)


async def approve_memory_rebuild(execution: ToolExecution, args: MemoryRebuildInput) -> ApprovalInfo | None:
    reason = f": {args.reason}" if args.reason else ""
    return ApprovalInfo(description=f"Rebuild memory filesystem artifacts{reason}", preview=None, diff=None)


async def memory_rebuild(execution: ToolExecution, args: MemoryRebuildInput) -> ToolResult:
    # Memory is file-canonical: the markdown pages ARE the source of truth, so
    # there is no projection to rebuild — and exporting would clobber the pages.
    return ToolResult(
        content="Memory is file-canonical: pages are the source of truth, nothing to rebuild. Edit pages directly.",
        preview="no-op (file-canonical)",
        data={"rebuilt": False},
    )

def _normalize_text(text: str) -> str:
    return " ".join(text.split()).strip().lower().rstrip(".!?")


async def remember(execution: ToolExecution, args: RememberInput) -> ToolResult:
    store = execution.ctx.services.get(MEMORY_RECORDS_SERVICE)
    if store is None:
        return _unavailable()

    session_id = getattr(getattr(execution.ctx, "session_state", None), "session_id", None) or execution.ctx.session_id
    project = execution.ctx.area
    visible = [(s.kind, s.key) for s in scopes_for_read(project=project, session_id=session_id)]

    # Conservative pre-write dedup (the Curator owns the LLM-judged dedup; this is
    # the cheap guard on the hot path). If an active record over the same read
    # scopes is lexically equivalent to — or fully subsumes — the new text, confirm
    # it instead of minting a duplicate.
    normalized = _normalize_text(args.text)
    for hit in await store.search(args.text, limit=3, scopes=visible):
        existing = _normalize_text(hit.text)
        if normalized == existing or normalized in existing:
            await store.confirm(hit.id)
            return ToolResult(content=f"Already known: {hit.text}", preview="Already known")

    base = SourceRef(kind="chat_turn", ref=f"{session_id}:{execution.tool_id}")
    scope = scope_for_write(
        kind=args.kind,
        project=project,
        session_id=session_id,
        source_ref=base,
    )
    source = apply_scope_to_source(base, scope)
    await store.add(args.text, kind=args.kind, scope_kind=scope.kind, scope_key=scope.key, source_ref=source)
    return ToolResult(content="Remembered", preview="Remembered")


async def forget(execution: ToolExecution, args: ForgetInput) -> ToolResult:
    store = execution.ctx.services.get(MEMORY_RECORDS_SERVICE)
    if store is None:
        return _unavailable()

    session_id = getattr(getattr(execution.ctx, "session_state", None), "session_id", None) or execution.ctx.session_id
    visible = [
        (s.kind, s.key) for s in scopes_for_read(project=execution.ctx.area, session_id=session_id)
    ]
    hits = await store.search(args.query, limit=5, scopes=visible)
    if not hits:
        return ToolResult(content="No matching memory to forget.", preview="Not found")

    best = hits[0]
    await store.delete(best.id)
    others = hits[1:]
    content = f"Forgot: {best.text}"
    if others:
        # Self-correcting: show what else matched so the model can refine rather
        # than dead-end if it meant a different record.
        content += "\n\nOther matches (not removed):\n" + _render_records(others)
    return ToolResult(content=content, preview="Forgotten")


async def recall(execution: ToolExecution, args: RecallInput) -> ToolResult:
    store = execution.ctx.services.get(MEMORY_RECORDS_SERVICE)
    if store is None:
        return _unavailable()

    session_id = getattr(getattr(execution.ctx, "session_state", None), "session_id", None) or execution.ctx.session_id
    visible = [
        (s.kind, s.key) for s in scopes_for_read(project=execution.ctx.area, session_id=session_id)
    ]
    # Topical recall by default: facts + source pointers. Directives and lessons are
    # standing rules already in the resident context every turn — including them here
    # let high-salience rules bury the topical facts a query is actually asking for
    # (the eval went 65% -> 95% once they were excluded).
    kinds = args.kinds or ["fact", "source"]
    hits = await store.search(args.query, limit=10, scopes=visible, kinds=kinds)
    if not hits:
        return ToolResult(content="No matching memory.", preview="No matches")
    content = _render_records(hits)
    nudge = _entity_brief_nudge(args.query)
    if nudge:
        content += "\n\n" + nudge
    return ToolResult(content=content, preview=f"{len(hits)} match(es)")


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
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=frozenset({MEMORY_RECORDS_SERVICE})),
    execute=memory_tree,
)

memory_read_tool = tool(
    display_name="MemoryRead",
    description="Read a safe relative .md memory artifact with line offsets. " + _MEMORY_FS_DESCRIPTION,
    input_model=MemoryReadInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=frozenset({MEMORY_RECORDS_SERVICE})),
    execute=memory_read,
)

memory_search_tool = tool(
    display_name="MemorySearch",
    description="Search safe memory artifact filenames, titles, snippets, and markdown content. " + _MEMORY_FS_DESCRIPTION,
    input_model=MemorySearchInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=frozenset({MEMORY_RECORDS_SERVICE})),
    execute=memory_search,
)

memory_patch_tool = tool(
    display_name="MemoryPatch",
    description="Patch a unique exact text block in a memory filesystem projection file. Requires approval; refuses generated artifacts unless force_generated is explicit. " + _MEMORY_FS_DESCRIPTION,
    input_model=MemoryPatchInput,
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, requires_approval=True, permissions=frozenset({MEMORY_RECORDS_SERVICE})),
    approval=approve_memory_patch,
    execute=memory_patch,
)

memory_write_tool = tool(
    display_name="MemoryWrite",
    description=(
        "Create or update-in-place a whole memory page (e.g. a feeds/<slug>.md briefing "
        "owned by an automation). Replaces the entire page — write the full current "
        "briefing, never an appended log. Refuses generated reports and record-backed "
        "pages (those compile from records; use remember/memory_patch). Requires approval. "
        + _MEMORY_FS_DESCRIPTION
    ),
    input_model=MemoryWriteInput,
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, requires_approval=True, permissions=frozenset({MEMORY_RECORDS_SERVICE})),
    approval=approve_memory_write,
    execute=memory_write,
)

memory_rebuild_tool = tool(
    display_name="MemoryRebuild",
    description="Rebuild the generated memory filesystem projection from canonical SQLite records. Requires approval. " + _MEMORY_FS_DESCRIPTION,
    input_model=MemoryRebuildInput,
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, requires_approval=True, permissions=frozenset({MEMORY_RECORDS_SERVICE})),
    approval=approve_memory_rebuild,
    execute=memory_rebuild,
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
        permissions=frozenset({MEMORY_RECORDS_SERVICE}),
    ),
    execute=remember,
)

forget_tool = tool(
    display_name="Forget",
    description=(
        "Remove a previously-remembered record from long-term memory. Describe "
        "what to forget in natural language; the best-matching record is removed."
    ),
    input_model=ForgetInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({MEMORY_RECORDS_SERVICE}),
    ),
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
