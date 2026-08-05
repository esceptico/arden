"""Filesystem-backed scratchpad for research subagents.

A bounded artifact store so deep-research subagents can offload bulk (long
source inventories, draft tables, working notes) out of context and re-read
specific parts on demand. Artifacts are written under
``~/.arden/artifacts/research/<scope>/`` (or ``ARDEN_DIR``) with strict relative
paths, plus a small ``manifest.json``.
"""

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from arden.agent.types.tools import ToolSourceRef, canonical_source_title, normalize_source_refs
from arden.settings import ARDEN_DIR
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope

MAX_ARTIFACT_BYTES = 256 * 1024
MAX_ARTIFACTS_PER_SCOPE = 64
_DEFAULT_READ_CHARS = 8_000
_MAX_READ_CHARS = 20_000
_MANIFEST = "manifest.json"


class WriteResearchArtifactInput(BaseModel):
    path: str = Field(
        max_length=256, description="Relative artifact path, e.g. 'sources/inventory.md'. No absolute paths or '..'."
    )
    content: str = Field(
        max_length=MAX_ARTIFACT_BYTES,
        description="UTF-8 text to write (overwrites any existing artifact at this path).",
    )


class AppendResearchArtifactInput(BaseModel):
    path: str = Field(max_length=256, description="Relative artifact path to append to (created if absent).")
    content: str = Field(max_length=MAX_ARTIFACT_BYTES, description="UTF-8 text to append.")


class ReadResearchArtifactInput(BaseModel):
    path: str = Field(description="Relative artifact path to read.")
    offset: int = Field(default=0, ge=0, description="Character offset to start from (default 0).")
    limit: int = Field(
        default=_DEFAULT_READ_CHARS,
        ge=1,
        le=_MAX_READ_CHARS,
        description=f"Max chars to return (default {_DEFAULT_READ_CHARS}, max {_MAX_READ_CHARS}). Page with offset.",
    )


class ListResearchArtifactsInput(BaseModel):
    pass


def _scope(execution: ToolExecution) -> str:
    return execution.ctx.run.research_scope_id or execution.ctx.run.run_id


def _safe_scope(scope_id: str) -> str:
    scope = re.sub(r"[^A-Za-z0-9_.-]+", "-", scope_id.strip())[:96].strip(".-")
    digest = hashlib.sha256(scope_id.encode("utf-8")).hexdigest()[:10]
    if not scope:
        return digest
    if scope != scope_id or len(scope_id) > 96:
        return f"{scope}-{digest}"
    return scope


def artifact_root() -> Path:
    return ARDEN_DIR / "artifacts" / "research"


def artifact_scope_dir(scope_id: str) -> Path:
    return artifact_root() / _safe_scope(scope_id)


def _manifest_path(scope_id: str) -> Path:
    return artifact_scope_dir(scope_id) / _MANIFEST


def _validate_path(path: str) -> str | None:
    if not path.strip():
        return "path must be non-empty"
    if path.startswith("/"):
        return "path must be relative (no leading '/')"
    if ".." in path.split("/"):
        return "path must not contain '..'"
    if any(ord(c) < 32 for c in path):
        return "path must not contain control characters"
    if len(path) > 256:
        return "path too long (max 256 chars)"
    if path == _MANIFEST or path.endswith(f"/{_MANIFEST}"):
        return f"{_MANIFEST} is reserved"
    return None


def _artifact_path(scope_id: str, rel_path: str) -> Path:
    root = artifact_scope_dir(scope_id).resolve()
    path = (root / rel_path).resolve()
    if root != path and root not in path.parents:
        raise ValueError("artifact path escaped scope")
    return path


def _preview(content: str, chars: int = 120) -> str:
    return content[:chars]


def _stat_row(scope_id: str, rel_path: str, content: str | None = None) -> dict:
    path = _artifact_path(scope_id, rel_path)
    if content is None:
        content = path.read_text(encoding="utf-8") if path.exists() else ""
    updated_at = (
        datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
        if path.exists()
        else datetime.now(UTC).isoformat()
    )
    return {
        "path": rel_path,
        "byte_len": len(content.encode("utf-8")),
        "updated_at": updated_at,
        "preview": _preview(content),
        "fs_path": str(path),
        "artifact_dir": str(artifact_scope_dir(scope_id)),
        "scope_id": scope_id,
    }


def _read_manifest(scope_id: str) -> dict:
    path = _manifest_path(scope_id)
    try:
        path.lstat()
    except FileNotFoundError:
        return {"scope_id": scope_id, "artifact_dir": str(artifact_scope_dir(scope_id)), "files": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Research artifact manifest must be a JSON object: {path}")
    data.setdefault("scope_id", scope_id)
    data.setdefault("artifact_dir", str(artifact_scope_dir(scope_id)))
    data.setdefault("files", [])
    return data


def _write_manifest(scope_id: str) -> None:
    root = artifact_scope_dir(scope_id)
    root.mkdir(parents=True, exist_ok=True)
    rows = _list_fs_artifacts_sync(scope_id)
    now = datetime.now(UTC).isoformat()
    existing = _read_manifest(scope_id)
    manifest = {
        "scope_id": scope_id,
        "safe_scope_id": _safe_scope(scope_id),
        "artifact_dir": str(root),
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
        "files": rows,
    }
    _manifest_path(scope_id).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def _list_fs_artifacts_sync(scope_id: str) -> list[dict]:
    root = artifact_scope_dir(scope_id)
    if not root.exists():
        return []
    rows: list[dict] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel == _MANIFEST:
            continue
        if _validate_path(rel):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rows.append(_stat_row(scope_id, rel, content))
    rows.sort(key=lambda row: row.get("updated_at") or "")
    return rows


async def list_scope_artifacts(scope_id: str) -> list[dict]:
    return await asyncio.to_thread(_list_fs_artifacts_sync, scope_id)


async def write_scope_artifact(scope_id: str, rel_path: str, content: str) -> Path:
    return await _put_fs_artifact(scope_id, rel_path, content)


async def _get_fs_artifact(scope_id: str, rel_path: str) -> str | None:
    def _read() -> str | None:
        path = _artifact_path(scope_id, rel_path)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    return await asyncio.to_thread(_read)


async def _put_fs_artifact(scope_id: str, rel_path: str, content: str) -> Path:
    def _write() -> Path:
        path = _artifact_path(scope_id, rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        _write_manifest(scope_id)
        return path

    return await asyncio.to_thread(_write)


def _invalid(err: str) -> ToolResult:
    return ToolResult.failure(
        code="invalid_ref",
        message=f"Invalid research artifact path: {err}",
        preview="Invalid path",
        recovery_action="Use a short relative path without '..' or control characters.",
    )


def _artifact_ref(scope: str, path: str) -> ToolSourceRef:
    artifact_ref = f"{scope}:{path}"
    return ToolSourceRef(
        provider="research",
        kind="artifact",
        ref=artifact_ref,
        title=canonical_source_title(f"Research artifact {path}", fallback=artifact_ref),
    )


async def write_research_artifact(execution: ToolExecution, args: WriteResearchArtifactInput) -> ToolResult:
    if err := _validate_path(args.path):
        return _invalid(err)
    if len(args.content.encode("utf-8")) > MAX_ARTIFACT_BYTES:
        return ToolResult.failure(
            code="result_too_large",
            message=f"Artifact too large (max {MAX_ARTIFACT_BYTES} bytes).",
            preview="Too large",
            recovery_action="Split the content across multiple bounded artifacts.",
        )
    scope = _scope(execution)
    existing = await list_scope_artifacts(scope)
    if args.path not in {a["path"] for a in existing} and len(existing) >= MAX_ARTIFACTS_PER_SCOPE:
        return ToolResult.failure(
            code="limit_exceeded",
            message=f"Too many artifacts in this research scope (max {MAX_ARTIFACTS_PER_SCOPE}).",
            preview="Too many",
            recovery_action="Append to or overwrite an existing artifact instead.",
        )
    fs_path = await write_scope_artifact(scope, args.path, args.content)
    return ToolResult(
        content=f"Wrote research artifact {args.path} ({len(args.content)} chars) at {fs_path}.",
        preview=f"Wrote {args.path}",
        source_refs=(_artifact_ref(scope, args.path),),
    )


async def append_research_artifact(execution: ToolExecution, args: AppendResearchArtifactInput) -> ToolResult:
    if err := _validate_path(args.path):
        return _invalid(err)
    scope = _scope(execution)
    existing = await _get_fs_artifact(scope, args.path)
    if existing is None:
        listed = await list_scope_artifacts(scope)
        if len(listed) >= MAX_ARTIFACTS_PER_SCOPE:
            return ToolResult.failure(
                code="limit_exceeded",
                message=f"Too many artifacts in this research scope (max {MAX_ARTIFACTS_PER_SCOPE}).",
                preview="Too many",
                recovery_action="Append to or overwrite an existing artifact instead.",
            )
    content = (existing or "") + args.content
    new_len = len(content.encode("utf-8"))
    if new_len > MAX_ARTIFACT_BYTES:
        return ToolResult.failure(
            code="result_too_large",
            message=f"Artifact would exceed max size ({MAX_ARTIFACT_BYTES} bytes).",
            preview="Too large",
            recovery_action="Start another artifact or replace this one with a compact summary.",
        )
    fs_path = await _put_fs_artifact(scope, args.path, content)
    return ToolResult(
        content=f"Appended to {args.path} ({new_len} bytes total) at {fs_path}.",
        preview=f"Appended {args.path}",
        source_refs=(_artifact_ref(scope, args.path),),
    )


async def read_research_artifact(execution: ToolExecution, args: ReadResearchArtifactInput) -> ToolResult:
    if err := _validate_path(args.path):
        return _invalid(err)
    scope = _scope(execution)
    content = await _get_fs_artifact(scope, args.path)
    if content is None:
        return ToolResult.failure(
            code="not_found",
            message=f"No research artifact at {args.path} in scope {scope}.",
            preview="Not found",
            recovery_action="Call list_research_artifacts and retry with an exact returned path.",
        )
    total = len(content)
    chunk = content[args.offset : args.offset + args.limit]
    end = args.offset + len(chunk)
    fs_path = _artifact_path(scope, args.path)
    header = f"[{args.path} — {total} chars, showing {args.offset}-{end}; fs_path={fs_path}]\n"
    footer = f"\n... [{total - end} more chars; call again with offset={end}]" if end < total else ""
    return ToolResult(
        content=f"{header}{chunk}{footer}",
        preview=f"Read {len(chunk)} of {total} chars",
        source_refs=(_artifact_ref(scope, args.path),),
    )


async def list_research_artifacts(execution: ToolExecution, args: ListResearchArtifactsInput) -> ToolResult:
    scope = _scope(execution)
    artifacts = await list_scope_artifacts(scope)
    if not artifacts:
        return ToolResult(content=f"No research artifacts in scope {scope} yet.", preview="0 artifacts")
    artifact_dir = artifact_scope_dir(scope)
    lines = [
        f"- {a['path']} ({a['byte_len']} bytes)" + (f" — {a['fs_path']}" if a.get("fs_path") else "") for a in artifacts
    ]
    return ToolResult(
        content=f"Research artifacts for scope {scope}:\nArtifact dir: {artifact_dir}\n" + "\n".join(lines),
        preview=f"{len(artifacts)} artifacts",
        source_refs=normalize_source_refs(_artifact_ref(scope, artifact["path"]) for artifact in artifacts),
    )


_READ_POLICY = ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, offload=False)
_OVERWRITE_POLICY = ToolPolicy(
    action=ToolAction.WRITE,
    scope=ToolScope.INTERNAL,
    offload=False,
    destructive=True,
    open_world=False,
    idempotent=True,
)
_APPEND_POLICY = ToolPolicy(
    action=ToolAction.WRITE,
    scope=ToolScope.INTERNAL,
    offload=False,
    destructive=False,
    open_world=False,
    idempotent=False,
)

write_research_artifact_tool = tool(
    display_name="WriteResearchArtifact",
    description="Write (overwrite) a filesystem-backed scratchpad artifact for this research run. Use to offload long source inventories, tables, or draft reports out of context; return a compact manifest instead. Stored under ~/.arden/artifacts/research/<scope>/ with safe relative paths.",
    input_model=WriteResearchArtifactInput,
    policy=_OVERWRITE_POLICY,
    execute=write_research_artifact,
)

append_research_artifact_tool = tool(
    display_name="AppendResearchArtifact",
    description="Append text to a filesystem-backed scratchpad artifact for this research run (created if absent). Good for incrementally building a source inventory.",
    input_model=AppendResearchArtifactInput,
    policy=_APPEND_POLICY,
    execute=append_research_artifact,
)

read_research_artifact_tool = tool(
    display_name="ReadResearchArtifact",
    description="Read back a scratchpad artifact from this research run, paging with offset/limit. Artifacts are also readable from ~/.arden/artifacts/research/<scope>/.",
    input_model=ReadResearchArtifactInput,
    policy=_READ_POLICY,
    execute=read_research_artifact,
)

list_research_artifacts_tool = tool(
    display_name="ListResearchArtifacts",
    description="List scratchpad artifacts written so far in this research run, including filesystem paths.",
    input_model=ListResearchArtifactsInput,
    policy=_READ_POLICY,
    execute=list_research_artifacts,
)
