"""Safe, deterministic directory indexes for the open memory vault."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
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
_MANAGED_LINE = re.compile(r"^-\s+(?:\[[^]]+\]\([^)]+\)|(.+?))\s+—\s+(.+?)\s*$")


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


class VaultIndexer:
    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def entries(self) -> tuple[IndexEntry, ...]:
        files = self._resource_files()
        return tuple(
            IndexEntry(path=rel.as_posix(), description=self._description(self.root / rel, None), is_dir=False)
            for rel in files
        )

    def root_entries(self) -> tuple[IndexEntry, ...]:
        return self._children(Path("."), self._resource_files())

    def render_updates(self) -> Mapping[Path, bytes]:
        files = self._resource_files()
        directories = {Path("."), *self._managed_directories()}
        for rel in files:
            parent = rel.parent
            while parent != Path("."):
                directories.add(parent)
                parent = parent.parent
        updates: dict[Path, bytes] = {}
        for directory in sorted(directories, key=lambda p: (len(p.parts), p.as_posix().casefold())):
            target = Path("index.md") if directory == Path(".") else directory / "README.md"
            current = self._read_regular(target)
            block = self._render_block(self._children(directory, files))
            updates[target] = self._replace_managed_block(current, block, root=(directory == Path("."))).encode("utf-8")
        return updates

    def _managed_directories(self) -> set[Path]:
        directories: set[Path] = set()
        for path in self.root.rglob("README.md"):
            try:
                rel = path.relative_to(self.root)
                st = path.lstat()
            except (OSError, ValueError):
                continue
            if (
                stat.S_ISREG(st.st_mode)
                and not stat.S_ISLNK(st.st_mode)
                and not any(part in _ENGINE_DIRS or part.startswith(".") for part in rel.parts[:-1])
            ):
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                if INDEX_START in text and INDEX_END in text:
                    directories.add(rel.parent)
        return directories

    def scan(self) -> IndexReport:
        return self.apply()

    def apply(self) -> IndexReport:
        updates = self.render_updates()
        for rel, content in updates.items():
            self._write_atomic(rel, content)
        missing = tuple(entry.path for entry in self.entries if entry.description == NEEDS_DESCRIPTION)
        health = "\n".join(f"- {path} — {NEEDS_DESCRIPTION}" for path in missing)
        return IndexReport(
            updated_paths=tuple(path.as_posix() for path in updates),
            missing_descriptions=missing,
            health_output=health,
        )

    def _resource_files(self) -> tuple[Path, ...]:
        try:
            root_st = self.root.lstat()
        except FileNotFoundError:
            return ()
        if stat.S_ISLNK(root_st.st_mode) or not stat.S_ISDIR(root_st.st_mode):
            raise FileNotFoundError(str(self.root))
        out: list[Path] = []

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
        return tuple(sorted(out, key=lambda p: (p.as_posix().casefold(), p.as_posix())))

    def _children(self, directory: Path, files: tuple[Path, ...]) -> tuple[IndexEntry, ...]:
        existing = self._existing_descriptions(directory)
        child_dirs: set[Path] = set()
        child_files: list[Path] = []
        for rel in files:
            try:
                descendant = rel.relative_to(directory)
            except ValueError:
                continue
            if len(descendant.parts) == 1:
                child_files.append(rel)
            elif descendant.parts:
                child_dirs.add(directory / descendant.parts[0])
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
        if INDEX_START not in text or INDEX_END not in text:
            return {}
        block = text.split(INDEX_START, 1)[1].split(INDEX_END, 1)[0]
        descriptions: dict[str, str] = {}
        for line in block.splitlines():
            match = _MANAGED_LINE.match(line.strip())
            if not match:
                continue
            label = match.group(1)
            if label is None:
                label_match = re.match(r"^-\s+\[([^]]+)\]", line.strip())
                label = label_match.group(1) if label_match else None
            description = match.group(2).strip()
            if label and description != NEEDS_DESCRIPTION:
                descriptions[label.strip()] = description
        return descriptions

    def _description(self, path: Path, existing: str | None) -> str:
        text = ""
        try:
            st = path.lstat()
            if stat.S_ISREG(st.st_mode) and not stat.S_ISLNK(st.st_mode):
                text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            text = ""
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
        if INDEX_START in text and INDEX_END in text:
            before, rest = text.split(INDEX_START, 1)
            _managed, after = rest.split(INDEX_END, 1)
            return before + after
        return text

    @staticmethod
    def _render_block(entries: tuple[IndexEntry, ...]) -> str:
        if not entries:
            return "_No entries._"
        return "\n".join(f"- {entry.path} — {entry.description}" for entry in entries)

    @staticmethod
    def _replace_managed_block(current: str, block: str, *, root: bool) -> str:
        managed = f"{INDEX_START}\n{block}\n{INDEX_END}"
        if INDEX_START in current and INDEX_END in current:
            before, rest = current.split(INDEX_START, 1)
            _old, after = rest.split(INDEX_END, 1)
            return before + managed + after
        prefix = current.rstrip()
        if not prefix:
            if not root:
                return f"{managed}\n"
            prefix = "# Memory"
        return f"{prefix}\n\n{managed}\n"

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
