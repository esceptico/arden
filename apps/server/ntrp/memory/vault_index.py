"""Safe, deterministic directory indexes for the open memory vault."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote, unquote
from uuid import uuid4

from ntrp.memory.frontmatter import parse_frontmatter

if TYPE_CHECKING:
    from collections.abc import Mapping

INDEX_START = "<!-- ntrp:index:start -->"
INDEX_END = "<!-- ntrp:index:end -->"
NEEDS_DESCRIPTION = "Needs description"

_RESOURCE_SUFFIXES = {".md", ".txt"}
_ENGINE_DIRS = {"raw", ".ntrp", ".index", ".maintenance"}
_GENERATED_ROOT_FILES = {"index.md", "README.md", "AGENTS.md", "health.md"}
_MANAGED_ID = re.compile(r"^(?P<line>- .*) <!-- ntrp:path=(?P<path>\S+) -->$")
_MARKDOWN_LINK_ROW = re.compile(r"^- \[[^]]*\]\((?P<target>[^)]+)\) — (?P<description>.*)$")


@dataclass(frozen=True)
class IndexEntry:
    path: str
    description: str
    is_dir: bool = False


@dataclass(frozen=True)
class IndexReport:
    updated_paths: tuple[str, ...]
    missing_descriptions: tuple[str, ...]
    health_output: str
    errors: tuple[str, ...] = ()


class VaultIndexer:
    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def entries(self) -> tuple[IndexEntry, ...]:
        files, _directories = self._resources()
        return tuple(
            IndexEntry(path=rel.as_posix(), description=self._description(self.root / rel, None), is_dir=False)
            for rel in files
        )

    def root_entries(self) -> tuple[IndexEntry, ...]:
        files, directories = self._resources()
        return self._children(Path("."), files, directories)

    def render_updates(self) -> Mapping[Path, bytes]:
        updates, _errors = self._render_plan()
        return updates

    def _render_plan(self) -> tuple[dict[Path, bytes], tuple[str, ...]]:
        files, discovered_directories = self._resources()
        directories = {Path("."), *discovered_directories}
        updates: dict[Path, bytes] = {}
        errors: list[str] = []
        for directory in sorted(directories, key=lambda p: (len(p.parts), p.as_posix().casefold())):
            target = Path("index.md") if directory == Path(".") else directory / "README.md"
            current = self._read_regular(target)
            try:
                bounds = self._marker_bounds(current)
            except ValueError:
                errors.append(f"{target.as_posix()}: invalid managed index markers")
                continue
            block = self._render_block(self._children(directory, files, discovered_directories))
            updates[target] = self._replace_managed_block(
                current,
                block,
                root=(directory == Path(".")),
                bounds=bounds,
            ).encode("utf-8")
        return updates, tuple(errors)

    def scan(self) -> IndexReport:
        return self.apply()

    def apply(self) -> IndexReport:
        updates, errors = self._render_plan()
        updated_paths: list[str] = []
        for rel, content in updates.items():
            if self._read_regular(rel).encode("utf-8") == content:
                continue
            self._write_atomic(rel, content)
            updated_paths.append(rel.as_posix())
        missing = tuple(entry.path for entry in self._indexed_entries() if entry.description == NEEDS_DESCRIPTION)
        health_lines = [*(f"- {path} — {NEEDS_DESCRIPTION}" for path in missing)]
        suffix = ": invalid managed index markers"
        health_lines.extend(f"- {error.removesuffix(suffix)} — Invalid managed index markers" for error in errors)
        return IndexReport(
            updated_paths=tuple(updated_paths),
            missing_descriptions=missing,
            health_output="\n".join(health_lines),
            errors=errors,
        )

    def _indexed_entries(self) -> tuple[IndexEntry, ...]:
        files, directories = self._resources()
        entries: list[IndexEntry] = []
        parents = (Path("."), *directories)
        for parent in sorted(parents, key=lambda path: (len(path.parts), path.as_posix().casefold(), path.as_posix())):
            for entry in self._children(parent, files, directories):
                name = entry.path.rstrip("/")
                full = (parent / name).as_posix()
                if entry.is_dir:
                    full += "/"
                entries.append(IndexEntry(full, entry.description, entry.is_dir))
        return tuple(entries)

    def _resources(self) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
        try:
            root_st = self.root.lstat()
        except FileNotFoundError:
            return (), ()
        if stat.S_ISLNK(root_st.st_mode) or not stat.S_ISDIR(root_st.st_mode):
            raise FileNotFoundError(str(self.root))
        out: list[Path] = []
        directories: list[Path] = []

        def walk(directory: Path) -> None:
            try:
                children = sorted(directory.iterdir(), key=lambda p: (p.name.casefold(), p.name))
            except OSError:
                return
            for child in children:
                try:
                    child_st = child.lstat()
                except OSError:
                    continue
                if stat.S_ISLNK(child_st.st_mode):
                    continue
                rel = child.relative_to(self.root)
                if stat.S_ISDIR(child_st.st_mode):
                    if child.name in _ENGINE_DIRS:
                        continue
                    directories.append(rel)
                    walk(child)
                    continue
                if not stat.S_ISREG(child_st.st_mode) or child.suffix.casefold() not in _RESOURCE_SUFFIXES:
                    continue
                if len(rel.parts) == 1 and child.name in _GENERATED_ROOT_FILES:
                    continue
                if child.name == "README.md":
                    continue
                out.append(rel)

        walk(self.root)
        def path_key(path: Path) -> tuple[str, str]:
            return path.as_posix().casefold(), path.as_posix()

        return tuple(sorted(out, key=path_key)), tuple(sorted(directories, key=path_key))

    def _children(
        self,
        directory: Path,
        files: tuple[Path, ...],
        directories: tuple[Path, ...],
    ) -> tuple[IndexEntry, ...]:
        existing = self._existing_descriptions(directory)
        child_dirs: list[Path] = []
        child_files: list[Path] = []
        for child in directories:
            if child.parent == directory:
                child_dirs.append(child)
        for rel in files:
            try:
                descendant = rel.relative_to(directory)
            except ValueError:
                continue
            if len(descendant.parts) == 1:
                child_files.append(rel)
        entries: list[IndexEntry] = []
        for child in sorted(child_dirs, key=lambda p: (p.name.casefold(), p.name)):
            label = f"{child.name}/"
            entries.append(IndexEntry(label, self._description(self.root / child / "README.md", existing.get(label)), True))
        for child in sorted(child_files, key=lambda p: (p.name.casefold(), p.name)):
            entries.append(IndexEntry(child.name, self._description(self.root / child, existing.get(child.name)), False))
        return tuple(entries)

    def _existing_descriptions(self, directory: Path) -> dict[str, str]:
        target = Path("index.md") if directory == Path(".") else directory / "README.md"
        text = self._read_regular(target)
        try:
            bounds = self._marker_bounds(text)
        except ValueError:
            return {}
        if bounds is None:
            return {}
        start, end = bounds
        block = text[start + len(INDEX_START) : end - len(INDEX_END)]
        descriptions: dict[str, str] = {}
        for line in block.splitlines():
            stripped = line.strip()
            identity = _MANAGED_ID.match(stripped)
            if identity:
                label = unquote(identity.group("path"))
                prefix = f"- {label} — "
                rendered = identity.group("line")
                if not rendered.startswith(prefix):
                    continue
                description = rendered[len(prefix) :].strip()
            elif link := _MARKDOWN_LINK_ROW.match(stripped):
                raw_target = link.group("target").strip().strip("<>").split("#", 1)[0]
                decoded_target = unquote(raw_target)
                target = Path(decoded_target)
                if target.is_absolute() or not target.parts or ".." in target.parts:
                    continue
                label = target.as_posix().removeprefix("./")
                if decoded_target.endswith("/"):
                    label += "/"
                description = link.group("description").strip()
            elif stripped.startswith("- ") and stripped[2:].count(" — ") == 1:
                label, _separator, description = stripped[2:].partition(" — ")
            else:
                continue
            if label and description != NEEDS_DESCRIPTION:
                descriptions[label.strip()] = description
        return descriptions

    def _description(self, path: Path, existing: str | None) -> str:
        text = ""
        try:
            text = self._read_regular(path.relative_to(self.root))
        except (OSError, UnicodeError):
            text = ""
        if path.name == "README.md":
            try:
                self._marker_bounds(text)
            except ValueError:
                return existing or NEEDS_DESCRIPTION
        try:
            frontmatter, body = parse_frontmatter(text)
        except Exception:
            frontmatter, body = {}, text
        summary = frontmatter.get("summary")
        if isinstance(summary, str) and summary.strip():
            return " ".join(summary.split())
        if existing:
            return existing
        body = self._without_managed_block(body)
        for line in body.splitlines():
            line = line.strip()
            if not line or line in {"---", INDEX_START, INDEX_END} or line.startswith("<!--"):
                continue
            heading = re.match(r"^#{1,6}\s+(.+)$", line)
            if heading:
                return " ".join(heading.group(1).strip().split())
            if line.startswith(("- ", "* ", "> ")):
                line = line[2:].strip()
            if line:
                return " ".join(line.split())
        return NEEDS_DESCRIPTION

    @staticmethod
    def _without_managed_block(text: str) -> str:
        try:
            bounds = VaultIndexer._marker_bounds(text)
        except ValueError:
            return text
        if bounds is not None:
            start, end = bounds
            return text[:start] + text[end:]
        return text

    @staticmethod
    def _render_block(entries: tuple[IndexEntry, ...]) -> str:
        if not entries:
            return "_No entries._"
        return "\n".join(
            f"- {entry.path} — {entry.description} <!-- ntrp:path={quote(entry.path, safe='')} -->"
            for entry in entries
        )

    @staticmethod
    def _marker_bounds(text: str) -> tuple[int, int] | None:
        starts = [match.start() for match in re.finditer(re.escape(INDEX_START), text)]
        ends = [match.start() for match in re.finditer(re.escape(INDEX_END), text)]
        if not starts and not ends:
            return None
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise ValueError("invalid managed index markers")
        return starts[0], ends[0] + len(INDEX_END)

    @staticmethod
    def _replace_managed_block(
        current: str,
        block: str,
        *,
        root: bool,
        bounds: tuple[int, int] | None,
    ) -> str:
        managed = f"{INDEX_START}\n{block}\n{INDEX_END}"
        if bounds is not None:
            start, end = bounds
            return current[:start] + managed + current[end:]
        if not current:
            if not root:
                return f"{managed}\n"
            return f"# Memory\n\n{managed}\n"
        separator = "" if current.endswith("\n\n") else ("\n" if current.endswith("\n") else "\n\n")
        return f"{current}{separator}{managed}\n"

    def _read_regular(self, rel: Path) -> str:
        path = self.root / rel
        try:
            st = path.lstat()
        except FileNotFoundError:
            return ""
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            return ""
        return path.read_text(encoding="utf-8")

    def _write_atomic(self, rel: Path, content: bytes) -> None:
        path = self.root / rel
        current = self.root
        for part in rel.parent.parts:
            if part in {"", ".", ".."}:
                continue
            current = current / part
            try:
                st = current.lstat()
            except FileNotFoundError:
                current.mkdir()
                st = current.lstat()
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise FileNotFoundError(rel.as_posix())
        try:
            target_st = path.lstat()
        except FileNotFoundError:
            target_st = None
        if target_st is not None and (stat.S_ISLNK(target_st.st_mode) or not stat.S_ISREG(target_st.st_mode)):
            raise FileNotFoundError(rel.as_posix())
        temp = path.parent / f".ntrp-{path.name}-{os.getpid()}-{uuid4().hex}.tmp"
        try:
            with temp.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
