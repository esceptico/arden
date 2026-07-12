"""Deterministic, rebuildable wikilink and backlink index for the open vault."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

from ntrp.memory.artifacts import ArtifactMemoryStore
from ntrp.memory.frontmatter import parse_frontmatter

_SNAPSHOT_REL = Path(".ntrp/indexes/links.json")
_SCHEMA_VERSION = 1
_MAX_CONTEXT_CHARS = 280


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
        return cls(
            source_path=str(raw["source_path"]),
            target=str(raw["target"]),
            display=str(raw["display"]),
            heading=str(raw["heading"]) if raw.get("heading") is not None else None,
            context=str(raw["context"]),
            line=int(raw["line"]),
            column=int(raw["column"]),
            status=str(raw["status"]),
            resolved_path=str(raw["resolved_path"]) if raw.get("resolved_path") is not None else None,
            candidates=tuple(str(path) for path in raw.get("candidates", ())),
            source_revision=str(raw["source_revision"]),
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
        if raw.get("schema_version") != _SCHEMA_VERSION:
            raise ValueError("unsupported link index schema")
        pages = tuple(str(path) for path in raw["pages"])
        links = tuple(LinkRecord.from_dict(link) for link in raw["links"])
        return cls(revision=str(raw["revision"]), pages=pages, links=links)


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
                candidates = _resolve_candidates(lookup_target, exact_paths, names)
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
            path = self.root / _SNAPSHOT_REL
            if not _regular_under_safe_parents(self.root, _SNAPSHOT_REL):
                return LinkIndexSnapshot.empty()
            return LinkIndexSnapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (FileNotFoundError, OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return LinkIndexSnapshot.empty()

    def _write_snapshot(self, snapshot: LinkIndexSnapshot) -> None:
        directory = _ensure_safe_directory(self.root, _SNAPSHOT_REL.parent)
        path = directory / _SNAPSHOT_REL.name
        try:
            target_stat = path.lstat()
        except FileNotFoundError:
            target_stat = None
        if target_stat is not None and (stat.S_ISLNK(target_stat.st_mode) or not stat.S_ISREG(target_stat.st_mode)):
            raise FileNotFoundError(path)
        payload = (
            json.dumps(snapshot.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        temp = directory / f".links-{os.getpid()}-{uuid4().hex}.tmp"
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
            directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temp.unlink(missing_ok=True)


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
        _add_name(names, title, path)
        for alias in aliases:
            _add_name(names, alias, path)
    return paths, names


def _resolve_candidates(
    target: str,
    paths: dict[str, set[str]],
    names: dict[str, set[str]],
) -> tuple[str, ...]:
    key = _normalize(target).removeprefix("./")
    if not key:
        return ()
    exact = paths.get(key)
    if exact:
        return tuple(sorted(exact, key=_path_key))
    named = names.get(key, set())
    return tuple(sorted(named, key=_path_key))


def _extract_wikilinks(content: str) -> tuple[_ExtractedLink, ...]:
    _frontmatter, body = parse_frontmatter(content)
    lines = body.splitlines()
    output: list[_ExtractedLink] = []
    heading: str | None = None
    block: list[tuple[int, str]] = []
    fence: str | None = None

    def flush() -> None:
        if not block:
            return
        context = " ".join(line.strip() for _number, line in block if line.strip())
        for line_number, line in block:
            output.extend(_extract_inline(line, heading=heading, context=context, line_number=line_number))
        block.clear()

    for line_number, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        marker = stripped[:3]
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            continue
        if marker in {"```", "~~~"}:
            flush()
            fence = marker
            continue
        if not stripped:
            flush()
            continue
        heading_text = _atx_heading(stripped)
        if heading_text is not None:
            flush()
            heading = heading_text
            context = line.strip()
            output.extend(_extract_inline(line, heading=heading, context=context, line_number=line_number))
            continue
        block.append((line_number, line))
    flush()
    return tuple(output)


def _atx_heading(line: str) -> str | None:
    hashes = len(line) - len(line.lstrip("#"))
    if not 1 <= hashes <= 6 or len(line) <= hashes or not line[hashes].isspace():
        return None
    value = line[hashes:].strip().rstrip("#").strip()
    return value or None


def _extract_inline(
    line: str,
    *,
    heading: str | None,
    context: str,
    line_number: int,
) -> tuple[_ExtractedLink, ...]:
    output: list[_ExtractedLink] = []
    index = 0
    code_ticks = 0
    in_comment = False
    while index < len(line):
        if in_comment:
            end = line.find("-->", index)
            if end == -1:
                return tuple(output)
            in_comment = False
            index = end + 3
            continue
        if line.startswith("<!--", index) and code_ticks == 0:
            in_comment = True
            index += 4
            continue
        if line[index] == "`":
            run = 1
            while index + run < len(line) and line[index + run] == "`":
                run += 1
            if code_ticks == 0:
                code_ticks = run
            elif run == code_ticks:
                code_ticks = 0
            index += run
            continue
        if code_ticks or not line.startswith("[[", index):
            index += 1
            continue
        end = line.find("]]", index + 2)
        if end == -1:
            break
        raw = line[index + 2 : end]
        if not raw or "[" in raw or "]" in raw:
            index = end + 2
            continue
        target, separator, label = raw.partition("|")
        target = target.strip()
        display = (label if separator else target).strip()
        if target and display:
            token = line[index : end + 2]
            output.append(
                _ExtractedLink(
                    target=target,
                    display=display,
                    heading=heading,
                    context=_context_snippet(context, token),
                    line=line_number,
                    column=index + 1,
                )
            )
        index = end + 2
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


def _ensure_safe_directory(root: Path, rel: Path) -> Path:
    try:
        root_stat = root.lstat()
    except FileNotFoundError:
        root.mkdir(parents=True)
        root_stat = root.lstat()
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise FileNotFoundError(root)
    current = root
    for part in rel.parts:
        current = current / part
        try:
            current_stat = current.lstat()
        except FileNotFoundError:
            current.mkdir()
            current_stat = current.lstat()
        if stat.S_ISLNK(current_stat.st_mode) or not stat.S_ISDIR(current_stat.st_mode):
            raise FileNotFoundError(current)
    return current


def _regular_under_safe_parents(root: Path, rel: Path) -> bool:
    try:
        root_stat = root.lstat()
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
            return False
        current = root
        for part in rel.parent.parts:
            current /= part
            current_stat = current.lstat()
            if stat.S_ISLNK(current_stat.st_mode) or not stat.S_ISDIR(current_stat.st_mode):
                return False
        target_stat = (root / rel).lstat()
        return stat.S_ISREG(target_stat.st_mode) and not stat.S_ISLNK(target_stat.st_mode)
    except OSError:
        return False


__all__ = ["LinkIndex", "LinkIndexSnapshot", "LinkRecord"]
