"""Deterministic, rebuildable wikilink and backlink index for the open vault."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from markdown_it import MarkdownIt

from ntrp.memory.artifacts import ArtifactMemoryStore
from ntrp.memory.frontmatter import parse_frontmatter

if TYPE_CHECKING:
    from markdown_it.rules_inline.state_inline import StateInline

_SNAPSHOT_REL = Path(".ntrp/indexes/links.json")
_SCHEMA_VERSION = 1
_MAX_CONTEXT_CHARS = 280
_MAX_LINK_TEXT_CHARS = 1000
_ENGINE_DIRS = {"raw", ".ntrp", ".index", ".maintenance"}
_RESOURCE_SUFFIXES = {".md", ".txt"}
_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


@dataclass(frozen=True)
class LinkRecord:
    source_path: str
    target: str
    display: str
    heading: str | None
    context: str
    line: int
    column: int
    status: str
    resolved_path: str | None
    candidates: tuple[str, ...]
    source_revision: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> LinkRecord:
        if not isinstance(raw, dict):
            raise ValueError("link record must be an object")
        required = {
            "source_path",
            "target",
            "display",
            "heading",
            "context",
            "line",
            "column",
            "status",
            "resolved_path",
            "candidates",
            "source_revision",
        }
        if not required.issubset(raw):
            raise ValueError("link record is missing required fields")
        if not all(isinstance(raw[key], str) for key in ("source_path", "target", "display", "context", "status", "source_revision")):
            raise ValueError("link record has invalid string fields")
        if raw["heading"] is not None and not isinstance(raw["heading"], str):
            raise ValueError("link heading must be a string or null")
        if raw["resolved_path"] is not None and not isinstance(raw["resolved_path"], str):
            raise ValueError("resolved path must be a string or null")
        if not isinstance(raw["candidates"], list) or not all(isinstance(path, str) for path in raw["candidates"]):
            raise ValueError("link candidates must be strings")
        if any(isinstance(raw[key], bool) or not isinstance(raw[key], int) for key in ("line", "column")):
            raise ValueError("link position must be integer")
        return cls(
            source_path=raw["source_path"],
            target=raw["target"],
            display=raw["display"],
            heading=raw["heading"],
            context=raw["context"],
            line=raw["line"],
            column=raw["column"],
            status=raw["status"],
            resolved_path=raw["resolved_path"],
            candidates=tuple(raw["candidates"]),
            source_revision=raw["source_revision"],
        )


@dataclass(frozen=True)
class LinkIndexSnapshot:
    revision: str
    pages: tuple[str, ...]
    links: tuple[LinkRecord, ...]

    def outgoing(self, path: str) -> tuple[LinkRecord, ...]:
        return tuple(link for link in self.links if link.source_path == path)

    def backlinks(self, path: str) -> tuple[LinkRecord, ...]:
        return tuple(link for link in self.links if link.resolved_path == path)

    def contains(self, path: str) -> bool:
        return path in self.pages

    def to_dict(self) -> dict:
        return {
            "schema_version": _SCHEMA_VERSION,
            "revision": self.revision,
            "pages": list(self.pages),
            "links": [link.to_dict() for link in self.links],
        }

    @classmethod
    def empty(cls) -> LinkIndexSnapshot:
        return cls(revision="", pages=(), links=())

    @classmethod
    def from_dict(cls, raw: dict) -> LinkIndexSnapshot:
        if not isinstance(raw, dict):
            raise ValueError("link index snapshot must be an object")
        if raw.get("schema_version") != _SCHEMA_VERSION:
            raise ValueError("unsupported link index schema")
        if not isinstance(raw.get("revision"), str):
            raise ValueError("link index revision must be a string")
        if not isinstance(raw.get("pages"), list) or not all(isinstance(path, str) for path in raw["pages"]):
            raise ValueError("link index pages must be strings")
        if not isinstance(raw.get("links"), list):
            raise ValueError("link index links must be objects")
        snapshot = cls(
            revision=raw["revision"],
            pages=tuple(raw["pages"]),
            links=tuple(LinkRecord.from_dict(link) for link in raw["links"]),
        )
        _validate_snapshot(snapshot)
        return snapshot


@dataclass(frozen=True)
class _ExtractedLink:
    target: str
    display: str
    heading: str | None
    context: str
    line: int
    column: int


class LinkIndex:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._snapshot = self._load_snapshot()

    @property
    def snapshot(self) -> LinkIndexSnapshot:
        return self._snapshot

    def rebuild(self, artifacts: ArtifactMemoryStore, revision: str) -> LinkIndexSnapshot:
        metadata: dict[str, tuple[str, tuple[str, ...], str]] = {}
        contents: dict[str, str] = {}
        for artifact in artifacts.list_artifacts():
            try:
                content = artifacts.read_resource_bytes(artifact.path).decode("utf-8")
            except (FileNotFoundError, UnicodeDecodeError):
                continue
            frontmatter, _body = parse_frontmatter(content)
            aliases = frontmatter.get("aliases") or ()
            if isinstance(aliases, str):
                aliases = (aliases,)
            elif not isinstance(aliases, (list, tuple)):
                aliases = ()
            metadata[artifact.path] = (
                artifact.title,
                tuple(str(alias).strip() for alias in aliases if str(alias).strip()),
                artifact.path,
            )
            contents[artifact.path] = content

        pages = tuple(sorted(contents, key=_path_key))
        exact_paths, names = _resolution_maps(metadata)
        links: list[LinkRecord] = []
        for source_path in pages:
            for extracted in _extract_wikilinks(contents[source_path]):
                lookup_target = extracted.target.split("#", 1)[0].strip()
                candidates = _resolve_candidates(lookup_target, exact_paths, names, source_path=source_path)
                status = "resolved" if len(candidates) == 1 else "ambiguous" if candidates else "unresolved"
                links.append(
                    LinkRecord(
                        source_path=source_path,
                        target=extracted.target,
                        display=extracted.display,
                        heading=extracted.heading,
                        context=extracted.context,
                        line=extracted.line,
                        column=extracted.column,
                        status=status,
                        resolved_path=candidates[0] if len(candidates) == 1 else None,
                        candidates=candidates,
                        source_revision=revision,
                    )
                )
        snapshot = LinkIndexSnapshot(revision=revision, pages=pages, links=tuple(links))
        self._write_snapshot(snapshot)
        self._snapshot = snapshot
        return snapshot

    def _load_snapshot(self) -> LinkIndexSnapshot:
        try:
            directory_fd = _open_index_directory(self.root, create=False)
            try:
                file_fd = os.open(
                    _SNAPSHOT_REL.name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_fd,
                )
                try:
                    if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                        raise FileNotFoundError(_SNAPSHOT_REL.as_posix())
                    with os.fdopen(file_fd, "r", encoding="utf-8") as stream:
                        file_fd = -1
                        raw = json.load(stream)
                finally:
                    if file_fd >= 0:
                        os.close(file_fd)
            finally:
                os.close(directory_fd)
            return LinkIndexSnapshot.from_dict(raw)
        except (FileNotFoundError, OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return LinkIndexSnapshot.empty()

    def _write_snapshot(self, snapshot: LinkIndexSnapshot) -> None:
        _validate_snapshot(snapshot)
        directory_fd = _open_index_directory(self.root, create=True)
        try:
            try:
                target_stat = os.stat(_SNAPSHOT_REL.name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                target_stat = None
            if target_stat is not None and not stat.S_ISREG(target_stat.st_mode):
                raise FileNotFoundError(_SNAPSHOT_REL.as_posix())
            payload = (
                json.dumps(snapshot.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            temp = f".links-{os.getpid()}-{uuid4().hex}.tmp"
            try:
                file_fd = os.open(
                    temp,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=directory_fd,
                )
                with os.fdopen(file_fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.rename(
                    temp,
                    _SNAPSHOT_REL.name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                )
                os.fsync(directory_fd)
            finally:
                try:
                    os.unlink(temp, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
        finally:
            os.close(directory_fd)


def _path_key(path: str) -> tuple[str, str]:
    return path.casefold(), path


def _normalize(value: str) -> str:
    return value.strip().casefold()


def _add_name(names: dict[str, set[str]], name: str, path: str) -> None:
    key = _normalize(name)
    if key:
        names.setdefault(key, set()).add(path)


def _resolution_maps(
    metadata: dict[str, tuple[str, tuple[str, ...], str]],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    paths: dict[str, set[str]] = {}
    names: dict[str, set[str]] = {}
    for path, (title, aliases, _canonical) in metadata.items():
        path_without_suffix = str(Path(path).with_suffix(""))
        _add_name(paths, path, path)
        _add_name(paths, path_without_suffix, path)
        _add_name(names, Path(path).name, path)
        _add_name(names, Path(path).stem, path)
        _add_name(names, title, path)
        for alias in aliases:
            _add_name(names, alias, path)
    return paths, names


def _resolve_candidates(
    target: str,
    paths: dict[str, set[str]],
    names: dict[str, set[str]],
    *,
    source_path: str,
) -> tuple[str, ...]:
    key = _normalize(target).removeprefix("./")
    if not key:
        return (source_path,)
    exact = paths.get(key)
    if exact:
        return tuple(sorted(exact, key=_path_key))
    named = names.get(key, set())
    return tuple(sorted(named, key=_path_key))


def _wikilink_rule(state: StateInline, silent: bool) -> bool:
    start = state.pos
    if not state.src.startswith("[[", start):
        return False
    end = state.src.find("]]", start + 2)
    if end < 0:
        return False
    raw = state.src[start + 2 : end]
    if not raw or "[" in raw or "]" in raw or "\n" in raw:
        return False
    target, separator, label = raw.partition("|")
    target = target.strip()
    display = (label if separator else target).strip()
    if (
        not target
        or not display
        or len(target) > _MAX_LINK_TEXT_CHARS
        or len(display) > _MAX_LINK_TEXT_CHARS
    ):
        return False
    if not silent:
        token = state.push("wikilink", "", 0)
        token.content = display
        token.meta = {
            "target": target,
            "display": display,
            "raw": state.src[start : end + 2],
            "start": start,
        }
    state.pos = end + 2
    return True


def _markdown_parser() -> MarkdownIt:
    parser = MarkdownIt("commonmark")
    parser.inline.ruler.before("link", "wikilink", _wikilink_rule)
    return parser


def _extract_wikilinks(content: str) -> tuple[_ExtractedLink, ...]:
    _frontmatter, body = parse_frontmatter(content)
    frontmatter_lines = content[: len(content) - len(body)].count("\n")
    output: list[_ExtractedLink] = []
    heading: str | None = None
    heading_open = False
    for token in _markdown_parser().parse(body):
        if token.type == "heading_open":
            heading_open = True
            continue
        if token.type != "inline":
            continue
        context = " ".join(token.content.split())
        if heading_open:
            heading = _bounded_text(context, _MAX_CONTEXT_CHARS)
            heading_open = False
        for child in token.children or ():
            if child.type != "wikilink":
                continue
            meta = child.meta
            start = int(meta["start"])
            prefix = token.content[:start]
            line_offset = prefix.count("\n")
            last_newline = prefix.rfind("\n")
            column = start + 1 if last_newline < 0 else start - last_newline
            output.append(
                _ExtractedLink(
                    target=meta["target"],
                    display=meta["display"],
                    heading=heading,
                    context=_context_snippet(context, meta["raw"]),
                    line=(token.map[0] if token.map else 0) + line_offset + frontmatter_lines + 1,
                    column=column,
                )
            )
    return tuple(output)


def _context_snippet(context: str, token: str) -> str:
    if len(context) <= _MAX_CONTEXT_CHARS:
        return context
    inner_limit = _MAX_CONTEXT_CHARS - 2
    position = context.find(token)
    if position < 0:
        position = 0
    start = position - max((inner_limit - len(token)) // 2, 0)
    start = min(max(start, 0), len(context) - inner_limit)
    return f"…{context[start : start + inner_limit].strip()}…"


def _bounded_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def _safe_page_path(path: str) -> bool:
    candidate = Path(path)
    return (
        len(path) <= _MAX_LINK_TEXT_CHARS
        and "\x00" not in path
        and not candidate.is_absolute()
        and bool(candidate.parts)
        and candidate.as_posix() == path
        and not any(part in {"", ".", ".."} or part in _ENGINE_DIRS for part in candidate.parts)
        and candidate.suffix.casefold() in _RESOURCE_SUFFIXES
    )


def _validate_snapshot(snapshot: LinkIndexSnapshot) -> None:
    if not snapshot.revision or len(snapshot.revision) > _MAX_LINK_TEXT_CHARS:
        raise ValueError("invalid link index revision")
    if len(snapshot.pages) != len(set(snapshot.pages)) or snapshot.pages != tuple(sorted(snapshot.pages, key=_path_key)):
        raise ValueError("link index pages must be unique and sorted")
    if not all(_safe_page_path(path) for path in snapshot.pages):
        raise ValueError("link index contains an unsafe page")
    pages = set(snapshot.pages)
    for link in snapshot.links:
        if link.source_path not in pages or link.source_revision != snapshot.revision:
            raise ValueError("link source is inconsistent with snapshot")
        if not 0 < len(link.target) <= _MAX_LINK_TEXT_CHARS or not 0 < len(link.display) <= _MAX_LINK_TEXT_CHARS:
            raise ValueError("link text is out of bounds")
        if len(link.context) > _MAX_CONTEXT_CHARS:
            raise ValueError("link context is out of bounds")
        if link.heading is not None and (not link.heading or len(link.heading) > _MAX_CONTEXT_CHARS):
            raise ValueError("link heading is out of bounds")
        if link.line < 1 or link.column < 1:
            raise ValueError("link position is invalid")
        if tuple(sorted(link.candidates, key=_path_key)) != link.candidates or len(set(link.candidates)) != len(link.candidates):
            raise ValueError("link candidates must be unique and sorted")
        if not set(link.candidates).issubset(pages):
            raise ValueError("link candidate is not an indexed page")
        if link.status == "resolved":
            if link.resolved_path not in pages or link.candidates != (link.resolved_path,):
                raise ValueError("resolved link is inconsistent")
        elif link.status == "ambiguous":
            if link.resolved_path is not None or len(link.candidates) < 2:
                raise ValueError("ambiguous link is inconsistent")
        elif link.status == "unresolved":
            if link.resolved_path is not None or link.candidates:
                raise ValueError("unresolved link is inconsistent")
        else:
            raise ValueError("invalid link status")
    order = tuple(sorted(snapshot.links, key=lambda link: (*_path_key(link.source_path), link.line, link.column)))
    if snapshot.links != order:
        raise ValueError("link records must be sorted")


def _open_index_directory(root: Path, *, create: bool) -> int:
    try:
        current_fd = os.open(root, _DIRECTORY_FLAGS)
    except OSError as exc:
        raise FileNotFoundError(root) from exc
    try:
        for part in _SNAPSHOT_REL.parent.parts:
            try:
                next_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=current_fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=current_fd)
                except OSError as exc:
                    raise FileNotFoundError(part) from exc
                os.fsync(current_fd)
                try:
                    next_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=current_fd)
                except OSError as exc:
                    raise FileNotFoundError(part) from exc
            except OSError as exc:
                raise FileNotFoundError(part) from exc
            if not stat.S_ISDIR(os.fstat(next_fd).st_mode):
                os.close(next_fd)
                raise FileNotFoundError(part)
            os.close(current_fd)
            current_fd = next_fd
        result = current_fd
        current_fd = -1
        return result
    finally:
        if current_fd >= 0:
            os.close(current_fd)


__all__ = ["LinkIndex", "LinkIndexSnapshot", "LinkRecord"]
