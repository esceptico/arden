"""FilePageStore — canonical memory backed by plain markdown pages, layered
Karpathy-style: the visible page file is compiled prose (the wiki), while its
append-only record timeline lives in a raw/<same-path>.md sidecar. Raw sidecars
are the live record-discovery boundary; visible-only Markdown/text stays a
resource and is never parsed into records.

Duck-types the slice of RecordStore that tools/profile/curator actually call
(open/close/attach_search_index, add/update/supersede_with/supersede/confirm/
set_pinned/delete, set_labels/labels_for/labels_of/list_labels, get/search/list/
count_active). Mounted under MEMORY_RECORDS_SERVICE in place of RecordStore so
canonicality flips with one assignment — no tool, prompt, or scope changes.

Retrieval is an in-memory token-overlap scan: at ~80 records this beats any index
(ponytail: no sqlite-vec, no FTS DB, no refresh-on-write bookkeeping). The .md
files are the single source of truth; nothing here is derived state to reconcile.
No git: durability is the files themselves + an external backup before destructive
passes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from ntrp.constants import MEMORY_MIN_ENTITY_RECORDS, RRF_K
from ntrp.database import serialize_embedding
from ntrp.logging import get_logger
from ntrp.memory.journal import JournalConflictError, VaultJournal
from ntrp.memory.ledger import LedgerEntry, LedgerMeta
from ntrp.memory.models import TRUST_DEFAULT, TRUST_LEVEL, Kind, Record, SourceRef, now_iso, union_source_refs
from ntrp.memory.pages import Line, Page, merge_split, parse_page, render_page, render_raw
from ntrp.memory.reconciler import RecordOperation, validate_operations
from ntrp.memory.scorer import salience
from ntrp.search.retrieval import rrf_merge

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

_logger = get_logger(__name__)

_MEMORY_LINE_SOURCE = "memory_line"  # search.db partition for per-line vectors (own source, never transcripts)
_DIRECTIVES = "directives.md"
_REFERENCES = "references.md"
_ME = "me.md"
_LESSONS = "lessons.md"  # continual-learning playbook (distilled lesson records)
_ENTITIES = "topics"  # one folder for every emergent subject (people/products/projects/topics)
_LEGACY_SUBJECT_DIRS = ("entities", "projects")  # folded into topics/ at open() (migration)
_OBSERVATIONS = "observations"  # RETIRED raw integration streams — folded away at open() (feeds/ replaced them)
_RAW = "raw"  # machine layer: per-page record timelines (raw/<page-path>.md sidecars)
_INSIGHTS = "insights"  # cross-domain DREAM outputs (OKF insights/), kept out of facts/entities
_GENERATED_FILES = {"index.md", "AGENTS.md", "health.md"}  # generated reports, not record pages
_RESOURCE_SUFFIXES = {".md", ".txt"}
_INTERNAL_DIRS = {".ntrp", ".index", ".maintenance"}
_OBSERVED_STATE = Path(".ntrp/maintenance/observed-pages.json")
_OBSERVED_BASES = Path(".ntrp/maintenance/observed-page-bases")
# Canonical, properly-cased titles for the fixed structural pages (root). Keeps the
# index + Obsidian note titles clean ("Me", not "me") and self-heals contamination.
_STRUCTURAL_TITLES = {
    _ME: "Me",
    _DIRECTIVES: "Directives",
    _LESSONS: "Playbook",
    _REFERENCES: "References",
    "active-work.md": "Active work",
}

_CONVENTIONS_TEMPLATE = """\
# Memory conventions (AGENTS.md)

This directory is a personal memory wiki — plain markdown, the single source of
truth (no DB). An agent reads it to understand the user and act on their behalf.

## Layers (page = compiled prose; raw/ = record timeline)
Each visible page is **compiled prose** — the current, human-readable briefing.
Its append-only **timeline** of atomic records lives in `raw/<same-path>.md`:

    - 2026-06-21 ^a1b2c3d4 [fact] [imp:6] (src:curator) Tim rides a gravel bike.

A timeline line is the canonical record. Tags: `[pin]` (never dropped),
`[imp:1-10]` (poignancy), `[ent:slug]` (primary entity), `[superseded]`.
`(src:…)` is provenance. Never edit `raw/` by hand — it is machine-owned;
pages are compiled from it.

## Record kinds (by FUNCTION, not subject)
- `directive` — a standing behaviour rule the USER stated.
- `fact` — a stable, durable truth about the user or their world.
- `source` — a re-findable pointer (receipt), evidence for a fact.
- `lesson` — a working-pattern the agent DISTILLED (the continual-learning playbook).
- `changelog` — housekeeping; ignore for synthesis.

## Layout
- `me.md` — the user's profile (root of the wiki).
- `directives.md` — standing behaviour rules.   `lessons.md` — learned playbook.
- `active-work.md` — current work, synthesized across the store.
- `topics/<slug>.md` — one page per emergent subject (people, products, projects,
  topics). A subject emerges once it has ≥2 records (else parked on me.md); a page
  with a `scope_key` is a project workstream. No separate people/ or projects/ split.
- `references.md` — source pointers.
- `feeds/<slug>.md` — automation-owned briefings: a feed automation rewrites its page
  in place each run (memory_write). No records, no append log — the page IS the state.
- `insights/<month>.md` — cross-domain dream outputs (provisional, cited).
- `daily/<date>.md` — per-day activity log, synthesized prose only (browsable history).
- `health.md` — generated self-audit of gaps (stale topics, idle sources).
- `raw/<page-path>.md` — machine layer: the page's record timeline (never hand-edit).
- `.index/` — throwaway search index (rebuildable, never canonical).

Navigate by `index.md` — one line of meaning per page. Read it first, follow the
few relevant pages, and synthesize from those; don't load the whole vault.
`health.md` is the lint report: stale topics, orphans, pending synthesis, idle
sources. Both are regenerated on load and after synthesis.

## Source trust
When sources conflict, the higher-trust source wins — update the claim in place. A
lower-trust source never overrides a higher one. Integration- and dream-sourced claims
are phrased tentatively; never launder them into user-stated confidence.

| trust | source | how to treat it |
|-------|--------|-----------------|
{trust_rows}

## Grounding
Cite only real record ids you were given — never invent, reformat, or guess one. Assert
only what the cited records support; bring in no outside knowledge. On conflict between
records: directive > fact > source. Pinned records are never dropped; changelog records
are ignored for synthesis. Never leak a record id or file path into user-facing prose.
Cite dialects: synthesis passes emit `(record:<8hex>)`, which the store verifies and
then renders as a readable source tag — `(from chat)`, `(from gmail)`, `(inferred)` —
keeping the verified id list in the page's raw sidecar (`prose_cites`); dream insights
write `(because of ^id1, ^id2)`.

## Authoring
Re-read a page before editing it. Update prose IN PLACE — don't append corrections as new
sentences. Edit only the page prose, never the `raw/` timelines. Prune stale claims.
Two learnings channels — not parallel systems: `lessons.md` (the distilled, agent-facing
playbook) rides the resident profile into every turn; `.maintenance/<automation>-learnings.md`
holds per-automation operational notes read ONLY by that automation, never shown in chat.
"""


def _trust_rows() -> str:
    """Render the source-trust table FROM models.TRUST_LEVEL so the manual can't drift
    from the code that enforces it. Descending trust; the default tier (integration/
    unknown) is synthesized from TRUST_DEFAULT."""
    notes = {
        4: "direct statements & corrections — always win",
        3: "distilled from the user's own conversations",
        2: "passive signals — verify before acting",
        1: "inferred cross-domain — hold loosely",
    }
    tiers: dict[int, list[str]] = {TRUST_DEFAULT: ["integration:*", "unknown"]}
    for src, lvl in TRUST_LEVEL.items():
        tiers.setdefault(lvl, [])
        if src not in tiers[lvl]:
            tiers[lvl].append(src)
    return "\n".join(
        f"| {lvl} | {', '.join(tiers[lvl])} | {notes.get(lvl, 'weigh by trust level')} |"
        for lvl in sorted(tiers, reverse=True)
    )


def _build_conventions() -> str:
    return _CONVENTIONS_TEMPLATE.replace("{trust_rows}", _trust_rows())


_CONVENTIONS = _build_conventions()  # the written AGENTS.md AND what load_conventions() serves


def load_conventions() -> str:
    """The operating manual the maintenance LLM passes prepend as shared context — the
    same bytes _write_conventions() writes to AGENTS.md (single source of truth)."""
    return _CONVENTIONS


_PARKABLE = (_ME, _REFERENCES)  # generic pages whose records may promote to an entity page


class CanonicalFileRole(StrEnum):
    USER_PAGE = "user_page"
    EVENT = "event"
    PROJECTION = "projection"


@dataclass(frozen=True)
class ObservedFileChange:
    """An external editable-page change with its exact durable base."""

    observation_id: str
    path: str
    before: bytes | None
    after: bytes | None
    base_revision: str
    result_revision: str
    origin: Literal["external", "desktop", "agent", "synthesis"] = "external"


def _slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return s or "untitled"


def _deslug(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.split("-")) if slug else slug


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _norm(text: str) -> str:
    """Collapse whitespace (incl. newlines) — a timeline line is one physical line,
    so embedded newlines would otherwise truncate on reload."""
    return " ".join(text.split())


def _iso(date: str) -> str:
    return f"{date}T00:00:00+00:00" if date else now_iso()


class FilePageStore:
    def __init__(
        self,
        root: Path,
        search_index: object | None = None,
        project_names: dict[str, str] | None = None,
        post_canonical_commit: Callable[[], None] | None = None,
    ) -> None:
        self._root = Path(root)
        self._search_index = search_index  # optional semantic leg (search.db); lexical-only when None
        self._project_names = project_names or {}  # scope_key -> human project name (page naming)
        self._scorer = None  # optional async (text, kind, pinned) -> int(1..10); set by knowledge
        self._pages: dict[Path, Page] = {}
        self._loc: dict[str, Path] = {}  # record id -> page path
        # Live-vault state: mtimes of every tracked .md/.txt resource and raw sidecar as
        # last seen/written by THIS store, so the watch loop reloads only external
        # edits (Obsidian, feeds, git) — our own writes never echo.
        self._file_state: dict[Path, tuple[str, int, int, int]] = {}
        self._watch_task: asyncio.Task | None = None
        self._journal = VaultJournal(self._root)
        self._post_canonical_commit = post_canonical_commit

    @property
    def canonical_revision(self) -> str:
        return self._journal.canonical_revision

    def vault_health(self):
        from ntrp.memory.migrate_ledger_v2 import validate_vault

        return validate_vault(self._root)

    # -- lifecycle -----------------------------------------------------------

    async def open(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        root_st = self._root.lstat()
        if stat.S_ISLNK(root_st.st_mode) or not stat.S_ISDIR(root_st.st_mode):
            raise NotADirectoryError(str(self._root))
        self._load_canonical_pages()
        if self._ledger_mode():
            self._active_ledger_entries()  # validate identity + relationship targets before serving reads
            self._write_conventions()
            self._file_state = self._scan_files()
            self._load_or_initialize_observed_pages()
            await self._sync_index()
            return
        self._migrate_insights()  # relocate pre-insights/ dream records (one-time, idempotent)
        self._retire_observations()  # drop the retired raw integration streams (feeds/ replaced them)
        self._migrate_to_topics()  # fold entities/+projects/ into one topics/ folder (idempotent)
        self._heal_structural_pages()  # repair cross-contaminated identity + canonical titles
        self._backfill_entities()
        from ntrp.memory.synthesize import _rename_project_pages

        _rename_project_pages(self)  # opaque scope-id -> human name, so a renamed project doesn't split
        stats = await self.reconcile_entities()
        self._write_conventions()  # AGENTS.md (OKF conventions) — static, once
        self._write_health()       # health.md (self-audit / surfaced gaps) — deterministic
        self._write_index()        # index.md — one line of meaning per page (navigate by index)
        self._file_state = self._scan_files()  # live-vault baseline (post-migration state)
        self._load_or_initialize_observed_pages()
        _logger.info("file memory ready", pages=len(self._pages), lines=len(self._loc), root=str(self._root), **stats)
        await self._sync_index()

    async def close(self) -> None:
        if self._watch_task is not None and not self._watch_task.done():
            self._watch_task.cancel()
            await asyncio.gather(self._watch_task, return_exceptions=True)
        self._watch_task = None

    # -- live vault (Obsidian-style: disk is truth, the store follows) --------

    def _walk_regular_files(
        self,
        directory: Path,
        *,
        suffixes: set[str],
        excluded_dirs: set[str] | None = None,
        directories: list[Path] | None = None,
    ) -> list[Path]:
        excluded = excluded_dirs or set()
        try:
            root_st = self._root.lstat()
            directory_st = directory.lstat()
        except OSError:
            return []
        if (
            stat.S_ISLNK(root_st.st_mode)
            or not stat.S_ISDIR(root_st.st_mode)
            or stat.S_ISLNK(directory_st.st_mode)
            or not stat.S_ISDIR(directory_st.st_mode)
        ):
            return []
        out: list[Path] = []

        def walk(current: Path) -> None:
            try:
                children = sorted(current.iterdir(), key=lambda path: (path.name.casefold(), path.name))
            except OSError:
                return
            for child in children:
                try:
                    child_st = child.lstat()
                except OSError:
                    continue
                if stat.S_ISLNK(child_st.st_mode):
                    continue
                if stat.S_ISDIR(child_st.st_mode):
                    if child.name not in excluded:
                        if directories is not None:
                            directories.append(child)
                        walk(child)
                elif stat.S_ISREG(child_st.st_mode) and child.suffix.casefold() in suffixes:
                    out.append(child)

        walk(directory)
        return out

    def _safe_read_bytes(self, path: Path) -> bytes | None:
        try:
            relative = path.relative_to(self._root)
            root_st = self._root.lstat()
        except (OSError, ValueError):
            return None
        if stat.S_ISLNK(root_st.st_mode) or not stat.S_ISDIR(root_st.st_mode):
            return None
        current = self._root
        for part in relative.parts[:-1]:
            current = current / part
            try:
                current_st = current.lstat()
            except OSError:
                return None
            if stat.S_ISLNK(current_st.st_mode) or not stat.S_ISDIR(current_st.st_mode):
                return None
        try:
            target_st = path.lstat()
        except OSError:
            return None
        if stat.S_ISLNK(target_st.st_mode) or not stat.S_ISREG(target_st.st_mode):
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(path, flags)
        except OSError:
            return None
        try:
            opened_st = os.fstat(fd)
            if not stat.S_ISREG(opened_st.st_mode):
                return None
            with os.fdopen(fd, "rb") as handle:
                fd = -1
                return handle.read()
        except OSError:
            return None
        finally:
            if fd >= 0:
                os.close(fd)

    def _safe_read_text(self, path: Path) -> str | None:
        content = self._safe_read_bytes(path)
        if content is None:
            return None
        try:
            return content.decode("utf-8")
        except UnicodeError:
            return None

    def _canonical_raw_files(self) -> list[Path]:
        return self._walk_regular_files(
            self._root / _RAW,
            suffixes={".md"},
            excluded_dirs={*_INTERNAL_DIRS, _RAW},
        )

    def _load_canonical_pages(self) -> None:
        self._pages.clear()
        self._loc.clear()
        raw_root = self._root / _RAW
        for raw_path in self._canonical_raw_files():
            page_path = self._root / raw_path.relative_to(raw_root)
            raw_text = self._safe_read_text(raw_path)
            if raw_text is None:
                continue
            visible_text = self._safe_read_text(page_path) or ""
            try:
                page = merge_split(parse_page(visible_text), raw_text)
            except Exception:
                _logger.warning("skip unparseable canonical memory page", path=str(page_path))
                continue
            self._pages[page_path] = page
            for line in page.lines:
                self._loc[line.id] = page_path

    def _scan_files(self) -> dict[Path, tuple[str, int, int, int]]:
        """Identity + mtime for safe user resources/directories and raw sidecars."""
        out: dict[Path, tuple[str, int, int, int]] = {}
        resource_directories: list[Path] = []
        paths = self._walk_regular_files(
            self._root,
            suffixes=_RESOURCE_SUFFIXES,
            excluded_dirs={*_INTERNAL_DIRS, _RAW},
            directories=resource_directories,
        )
        paths.extend(self._canonical_raw_files())
        for path in paths:
            if path.parent == self._root and path.name in _GENERATED_FILES:
                continue
            try:
                path_st = path.lstat()
                if stat.S_ISREG(path_st.st_mode) and not stat.S_ISLNK(path_st.st_mode):
                    out[path] = ("file", path_st.st_dev, path_st.st_ino, path_st.st_mtime_ns)
            except OSError:
                continue
        for directory in resource_directories:
            try:
                directory_st = directory.lstat()
                if stat.S_ISDIR(directory_st.st_mode) and not stat.S_ISLNK(directory_st.st_mode):
                    out[directory] = ("directory", directory_st.st_dev, directory_st.st_ino, directory_st.st_mtime_ns)
            except OSError:
                continue
        return out

    @staticmethod
    def _content_revision(content: bytes | None) -> str:
        return hashlib.sha256(content or b"").hexdigest()

    def _editable_page_bytes(self) -> dict[str, bytes]:
        pages: dict[str, bytes] = {}
        for path in self._walk_regular_files(
            self._root,
            suffixes={".md"},
            excluded_dirs={*_INTERNAL_DIRS, _RAW},
        ):
            rel = path.relative_to(self._root)
            if path.parent == self._root and path.name in _GENERATED_FILES:
                continue
            content = self._safe_read_bytes(path)
            if content is not None:
                pages[rel.as_posix()] = content
        return pages

    def _observed_path(self, rel: Path) -> Path:
        return self._root.joinpath(*rel.parts)

    def _write_observed_file(self, rel: Path, content: bytes) -> None:
        from ntrp.memory.artifacts import ArtifactMemoryStore

        resources = ArtifactMemoryStore(self._root)
        parent_fd, name = resources._open_anchored_parent(rel, create_parents=True)
        temp = f".{name}.{uuid4().hex}.tmp"
        descriptor = os.open(
            temp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                target_st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                if stat.S_ISLNK(target_st.st_mode) or not stat.S_ISREG(target_st.st_mode):
                    raise ValueError(f"observed page metadata target is unsafe: {rel}")
            os.rename(temp, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            temp = ""
            os.fsync(parent_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temp:
                try:
                    os.unlink(temp, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            os.close(parent_fd)

    def _read_observed_file(self, rel: Path) -> bytes | None:
        from ntrp.memory.artifacts import ArtifactMemoryStore

        resources = ArtifactMemoryStore(self._root)
        try:
            descriptor = resources._open_anchored_regular(rel, os.O_RDONLY, create_parents=False)
        except FileNotFoundError:
            current = self._root
            for part in rel.parts[:-1]:
                current /= part
                try:
                    current_st = current.lstat()
                except FileNotFoundError:
                    break
                if stat.S_ISLNK(current_st.st_mode) or not stat.S_ISDIR(current_st.st_mode):
                    raise ValueError(f"observed page metadata parent is unsafe: {current}")
            return None
        with os.fdopen(descriptor, "rb") as stream:
            return stream.read()

    def _read_observed_state(self) -> dict:
        raw = self._read_observed_file(_OBSERVED_STATE)
        if raw is None:
            return {}
        try:
            state = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("invalid observed page state") from exc
        if state.get("version") != 1 or not isinstance(state.get("pages"), dict) or not isinstance(
            state.get("engine_writes"), list
        ) or not isinstance(state.get("pending_changes", {}), dict):
            raise RuntimeError("invalid observed page state")
        state.setdefault("pending_changes", {})
        return state

    def _write_observed_state(self, state: dict) -> None:
        payload = (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode()
        self._write_observed_file(_OBSERVED_STATE, payload)

    def _store_observed_base(self, content: bytes) -> str:
        revision = self._content_revision(content)
        rel = _OBSERVED_BASES / revision
        existing = self._read_observed_file(rel)
        if existing is None:
            self._write_observed_file(rel, content)
        elif existing != content:
            raise RuntimeError(f"invalid observed page base: {revision}")
        return revision

    def _load_or_initialize_observed_pages(self) -> None:
        if self._read_observed_state():
            return
        pages = self._editable_page_bytes()
        revisions = {path: self._store_observed_base(content) for path, content in pages.items()}
        self._write_observed_state(
            {"version": 1, "pages": revisions, "engine_writes": [], "pending_changes": {}}
        )

    def register_engine_write(
        self,
        path: str,
        content: bytes | None,
        *,
        origin: Literal["desktop", "agent", "synthesis"],
    ) -> None:
        result_revision = self._store_observed_base(content or b"")
        state = self._read_observed_state()
        if not state:
            return
        marker = {"origin": origin, "path": path, "result_revision": result_revision}
        state["engine_writes"].append(marker)
        self._write_observed_state(state)

    def acknowledge_observed_change(self, change: ObservedFileChange) -> None:
        current = self._editable_page_bytes().get(change.path)
        if current != change.after:
            raise JournalConflictError(f"observed page changed before acknowledgement: {change.path}")
        state = self._read_observed_state()
        if change.after is None:
            state["pages"].pop(change.path, None)
        else:
            state["pages"][change.path] = self._store_observed_base(change.after)
        state["pending_changes"].pop(change.path, None)
        self._write_observed_state(state)

    def _observed_page_changes(self) -> list[ObservedFileChange]:
        state = self._read_observed_state()
        pages: dict[str, str] = state["pages"]
        current = self._editable_page_bytes()
        changes: list[ObservedFileChange] = []
        markers_pruned = False
        pending_changed = False
        for path in sorted(set(pages) | set(current)):
            before_revision = pages.get(path)
            after = current.get(path)
            result_revision = self._content_revision(after)
            if before_revision == result_revision and (path in pages) == (path in current):
                continue
            before = None
            if before_revision is not None:
                before = self._read_observed_file(_OBSERVED_BASES / before_revision)
                if before is None:
                    raise RuntimeError(f"missing observed page base: {before_revision}")
                if self._content_revision(before) != before_revision:
                    raise RuntimeError(f"invalid observed page base: {before_revision}")
            origin: Literal["external", "desktop", "agent", "synthesis"] = "external"
            path_markers = [
                (index, marker)
                for index, marker in enumerate(state["engine_writes"])
                if marker.get("path") == path
            ]
            marker_index = next(
                (
                    index for index, marker in path_markers if marker.get("result_revision") == result_revision
                ),
                None,
            )
            if marker_index is not None:
                marker = state["engine_writes"][marker_index]
                origin = marker["origin"]
            if path_markers:
                markers_pruned = True
                state["engine_writes"] = [
                    marker for marker in state["engine_writes"] if marker.get("path") != path
                ]
            pending = state["pending_changes"].get(path)
            base_revision = before_revision or self._content_revision(None)
            if not isinstance(pending, dict) or (
                pending.get("base_revision") != base_revision
                or pending.get("result_revision") != result_revision
                or pending.get("after_exists") != (after is not None)
            ):
                pending = {
                    "id": uuid4().hex,
                    "base_revision": base_revision,
                    "result_revision": result_revision,
                    "after_exists": after is not None,
                }
                state["pending_changes"][path] = pending
                if after is not None:
                    self._store_observed_base(after)
                pending_changed = True
            change = ObservedFileChange(
                observation_id=pending["id"],
                path=path,
                before=before,
                after=after,
                base_revision=base_revision,
                result_revision=result_revision,
                origin=origin,
            )
            if marker_index is not None:
                if after is None:
                    state["pages"].pop(path, None)
                else:
                    state["pages"][path] = self._store_observed_base(after)
                state["pending_changes"].pop(path, None)
                pending_changed = True
            changes.append(change)
        if any(change.origin != "external" for change in changes) or markers_pruned or pending_changed:
            self._write_observed_state(state)
        return changes

    def _reload_page(self, path: Path) -> None:
        """Re-read one page (+ its raw sidecar) from disk, replacing the in-memory
        state. Synchronous mutation — no await between evicting the old page and
        installing the new one, so concurrent store ops never see a half-state."""
        old = self._pages.pop(path, None)
        if old is not None:
            for ln in old.lines:
                if self._loc.get(ln.id) == path:
                    self._loc.pop(ln.id, None)
                    self._unindex_line(ln.id)
        try:
            raw_path = self._raw_path(path)
            raw_text = self._safe_read_text(raw_path)
            page_text = self._safe_read_text(path) or ""
        except ValueError:
            raw_text = None
            page_text = ""
        if raw_text is None:
            return  # page deleted on disk
        try:
            page = merge_split(parse_page(page_text), raw_text)
        except Exception:
            _logger.warning("skip unparseable memory page on reload", path=str(path))
            return
        self._pages[path] = page
        for ln in page.lines:
            self._loc[ln.id] = path
            if not self._ledger_mode() and ln in page.active_lines():
                self._index_line(ln)

    def _reload_canonical_state(self) -> None:
        """Replace live record and index state from the recovered vault bytes."""
        old_ids = set(self._loc)
        self._load_canonical_pages()
        active = self._active_ledger_entries() if self._ledger_mode() else tuple(line for page in self._pages.values() for line in page.active_lines())
        for record_id in old_ids:
            self._unindex_line(record_id)
        for line in active:
            self._index_line(line)
        self._file_state = self._scan_files()

    async def refresh_from_disk(self) -> list[str | ObservedFileChange]:
        """Absorb external edits (Obsidian, feeds, git, memory_write): reload every
        page whose file or raw sidecar changed since we last saw/wrote it. Returns
        the vault-relative paths of reloaded pages (empty = nothing changed)."""
        current = self._scan_files()
        raw_root = self._root / _RAW
        observed = {change.path: change for change in self._observed_page_changes()}
        changed: dict[str, str | ObservedFileChange] = dict(observed)
        canonical_changed: set[Path] = set()
        for p in current.keys() | self._file_state.keys():
            if self._file_state.get(p) == current.get(p):
                continue
            rel = p.relative_to(self._root)
            state = current.get(p) or self._file_state[p]
            if state[0] == "directory":
                directory = rel.as_posix().rstrip("/") + "/"
                changed[directory] = directory
                continue
            if rel.parts[0] == _RAW:
                page_path = self._root / p.relative_to(raw_root)
                page_rel = page_path.relative_to(self._root).as_posix()
                changed.setdefault(page_rel, page_rel)
                canonical_changed.add(page_path)
            else:
                page_rel = rel.as_posix()
                changed.setdefault(page_rel, page_rel)
                if p.suffix.casefold() == ".md" and self._safe_read_text(self._raw_path(p)) is not None:
                    canonical_changed.add(p)
        for page_rel in observed:
            page_path = self._root / page_rel
            if self._safe_read_text(self._raw_path(page_path)) is not None:
                canonical_changed.add(page_path)
        for page_path in sorted(canonical_changed):
            self._reload_page(page_path)
        self._file_state = current
        if canonical_changed:
            if self._ledger_mode():
                self._active_ledger_entries()
                await self._sync_index()
            else:
                self._write_index()
                self._write_health()
        return [changed[path] for path in sorted(changed)]

    def start_watch(self, on_change=None, *, interval: float = 2.0) -> None:
        """Poll the vault for external edits (ponytail: mtime scan of ~100 files
        beats fsevents plumbing at this scale). `on_change(paths)` fires after a
        batch is absorbed — the server publishes it to the global event bus."""

        async def _loop() -> None:
            while True:
                await asyncio.sleep(interval)
                try:
                    paths = await self.refresh_from_disk()
                except Exception:
                    _logger.warning("vault watch pass failed", exc_info=True)
                    continue
                if paths:
                    _logger.info("vault absorbed external edits", pages=len(paths))
                    if on_change is not None:
                        try:
                            await on_change(paths)
                        except Exception:
                            _logger.debug("vault change sink failed", exc_info=True)

        if self._watch_task is None or self._watch_task.done():
            self._watch_task = asyncio.ensure_future(_loop())

    def attach_search_index(self, search_index: object | None) -> None:
        self._search_index = search_index

    def attach_scorer(self, scorer) -> None:
        """scorer: async (text, kind, pinned) -> int(1..10). Set by knowledge wiring."""
        self._scorer = scorer

    # -- vector index sync (search.db partition; throwaway/derived) ----------

    def _track(self, coro) -> None:
        async def _run():
            try:
                await coro
            except Exception:
                _logger.warning("memory_line index op failed", exc_info=True)

        asyncio.ensure_future(_run())

    def _index_line(self, line: Line | LedgerEntry) -> None:
        if (
            self._search_index is None
            or (isinstance(line, LedgerEntry) and line.meta.operation == "retract")
            or (isinstance(line, Line) and line.superseded)
            or not line.text.strip()
        ):
            return
        self._track(
            self._search_index.upsert(
                source=_MEMORY_LINE_SOURCE,
                source_id=line.id,
                title=f"{line.kind} line",
                content=line.text,
                metadata={"record_id": line.id, "kind": line.kind},
            )
        )

    def _unindex_line(self, record_id: str) -> None:
        if self._search_index is None:
            return
        self._track(self._search_index.delete(_MEMORY_LINE_SOURCE, record_id))

    async def _sync_index(self) -> None:
        """Reconcile search.db vectors with current page state at open() (bulk).
        upsert hash-dedups, so unchanged lines cost a hash check and zero embeds."""
        index = self._search_index
        if index is None:
            return
        try:
            active: dict[str, Line | LedgerEntry] = {}
            lines: Sequence[Line | LedgerEntry]
            if self._ledger_mode():
                lines = self._active_ledger_entries()
            else:
                lines = tuple(line for page in self._pages.values() for line in page.active_lines())
            for line in lines:
                if line.text.strip():
                    active[line.id] = line
            indexed = await index.store.get_indexed_hashes(_MEMORY_LINE_SOURCE)
            for stale in set(indexed) - set(active):
                await index.delete(_MEMORY_LINE_SOURCE, stale)
            for line in active.values():
                await index.upsert(
                    source=_MEMORY_LINE_SOURCE,
                    source_id=line.id,
                    title=f"{line.kind} line",
                    content=line.text,
                    metadata={"record_id": line.id, "kind": line.kind},
                )
        except Exception:
            _logger.warning("memory_line index sync failed", exc_info=True)

    async def score_pending(self) -> int:
        """Backfill importance on unscored lines via the attached scorer. Off the
        hot path (curator sweep). No-op when no scorer is attached."""
        if self._scorer is None:
            return 0
        scored = 0
        for path, page in self._pages.items():
            dirty = False
            for line in page.lines:
                if isinstance(line, LedgerEntry):
                    continue
                if line.superseded or line.imp is not None:
                    continue
                try:
                    line.imp = await self._scorer(line.text, line.kind, line.pinned)
                    dirty = True
                    scored += 1
                except Exception:
                    _logger.warning("importance scoring failed", exc_info=True)
            if dirty:
                self._persist(path)
        return scored

    # -- internals -----------------------------------------------------------

    def _new_id(self) -> str:
        while True:
            rid = uuid4().hex[:8]
            if rid not in self._loc:
                return rid

    def _ledger_mode(self) -> bool:
        return any(page.records_header is not None for page in self._pages.values())

    def _ledger_entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(
            line
            for page in self._pages.values()
            for line in page.lines
            if isinstance(line, LedgerEntry)
        )

    @staticmethod
    def _validate_ledger_entries(entries: Sequence[LedgerEntry]) -> None:
        Page(lines=list(entries)).active_entries()
        ids = {entry.id for entry in entries}
        for entry in entries:
            for target in entry.meta.supersedes:
                if target not in ids:
                    raise ValueError(f"missing supersedes target: {target}")
            if entry.meta.successor_id is not None and entry.meta.successor_id not in ids:
                raise ValueError(f"missing successor target: {entry.meta.successor_id}")

    def _active_ledger_entries(self) -> tuple[LedgerEntry, ...]:
        entries = self._ledger_entries()
        self._validate_ledger_entries(entries)
        return Page(lines=list(entries)).active_entries()

    def _next_sequence(self) -> int:
        return max((entry.meta.sequence for entry in self._ledger_entries()), default=0) + 1

    def _replace_ledger_entry(self, path: Path, entry: LedgerEntry) -> None:
        page = self._pages[path]
        page.lines = [entry if line.id == entry.id else line for line in page.lines]
        self._loc[entry.id] = path
        self._persist(path)

    def _entity_labels(self, path: Path) -> list[str]:
        return list(self._pages[path].frontmatter.get("entity_labels", [])) if path in self._pages else []

    def _meta_labels(self, path: Path) -> list[str]:
        return list(self._pages[path].frontmatter.get("meta_labels", [])) if path in self._pages else []

    def _legacy_scope_for(self, path: Path, kind: str) -> tuple[str | None, str | None]:
        # Scope is a property of the page (frontmatter scope_key), not its folder — a
        # project page and an emergent topic both live in topics/; only the scope_key
        # tells them apart. This keeps active-work's project view working after the
        # entities/+projects/ folders were unified.
        page = self._pages.get(path)
        key = page.frontmatter.get("scope_key") if page else None
        if key:
            return ("area", str(key))
        if kind in (Kind.DIRECTIVE, Kind.LESSON):
            return ("global", None)  # behaviour rules + distilled playbook apply everywhere
        return ("user", None)

    def _to_record(self, line: Line | LedgerEntry, path: Path) -> Record:
        if isinstance(line, LedgerEntry):
            successor = next(
                (
                    str(entry.meta.successor_id or entry.id)
                    for entry in self._ledger_entries()
                    if line.id in entry.meta.supersedes
                ),
                None,
            )
            return Record(
                id=line.id,
                text=line.text,
                kind=line.kind,
                scope_kind=line.meta.scope_kind,
                scope_key=line.meta.scope_key,
                created_at=line.occurred_at or line.meta.recorded_at,
                last_confirmed_at=line.meta.recorded_at,
                superseded_by=successor,
                pinned=line.pinned,
                source_ref=(line.meta.sources[0] if line.meta.sources else None),
                sources=line.meta.sources,
                imp=line.imp,
            )
        scope_kind, scope_key = self._legacy_scope_for(path, line.kind)
        return Record(
            id=line.id,
            text=line.text,
            kind=line.kind,
            scope_kind=scope_kind,
            scope_key=scope_key,
            created_at=_iso(line.date),
            last_confirmed_at=_iso(line.date),
            superseded_by=("superseded" if line.superseded else None),
            pinned=line.pinned,
            source_ref=SourceRef(kind=line.src, ref=line.id),
            imp=line.imp,
        )

    def _entity_path(self, slug: str) -> Path:
        return self._root / _ENTITIES / f"{slug}.md"

    def _page_for(self, kind: str, scope_kind: str | None, scope_key: str | None) -> Path:
        """The BASE page for a fresh record by kind+scope. Entity placement is NOT
        decided here — an entity-labeled record lands on its base page (me/references)
        and is promoted to entities/<slug>.md only once the entity crosses
        MEMORY_MIN_ENTITY_RECORDS, via _reconcile_entity."""
        if kind == Kind.DIRECTIVE:
            return self._root / _DIRECTIVES
        if kind == Kind.LESSON:
            return self._root / _LESSONS
        if scope_kind in ("area", "project") and scope_key:
            return self._root / _ENTITIES / f"{_slug(self._project_names.get(scope_key, scope_key))}.md"
        if kind == Kind.SOURCE:
            return self._root / _REFERENCES
        return self._root / _ME

    def _park_path(self, line: Line) -> Path:
        """Where a sub-threshold entity record lives: its kind-appropriate generic
        page (references for source pointers, me.md otherwise)."""
        return self._root / (_REFERENCES if line.kind == Kind.SOURCE else _ME)

    def _entity_members(self, slug: str) -> list[tuple[Path, Line]]:
        return [(p, ln) for p, page in self._pages.items() for ln in page.lines if ln.entity == slug]

    def _entity_display(self, slug: str) -> str:
        """Human label for a slug: the entity page's title when one exists, else a
        de-slugged guess. Stable under slugify so the curator's reused label maps
        back to the same page."""
        ep = self._pages.get(self._entity_path(slug))
        return (ep.frontmatter.get("title") if ep else None) or _deslug(slug)

    def _reconcile_entity(self, slug: str | None, *, display: str | None = None) -> Path | None:
        """Place every record of one entity on the right page: its own
        entities/<slug>.md once the entity has >= MEMORY_MIN_ENTITY_RECORDS active
        records, else parked on its kind-appropriate generic page. Lifecycle follows
        the ACTIVE RECORD COUNT, not prose: a page's synthesized prose is a derived
        projection (regenerated nightly from the records), so folding a sub-threshold
        entity discards that prose while the canonical records move intact to me.md and
        re-synthesize there — no data is lost. Idempotent and write-frugal (persists
        only pages it changes); moves never touch the vector index (id+text unchanged)."""
        if not slug:
            return None
        entity_page = self._entity_path(slug)
        existing = self._pages.get(entity_page)
        members = self._entity_members(slug)
        if not members:
            # No records carry this slug (e.g. its last record was deleted) -> the page is
            # a dead file; drop it. Its prose described records that no longer exist.
            if existing is not None and not existing.lines:
                self._pages.pop(entity_page, None)
                self._remove_page_files(entity_page)
            return None
        if display is None:
            display = (existing.frontmatter.get("title") if existing else None) or _deslug(slug)
        # A project page (frontmatter scope_key) is a real workstream — its lifecycle
        # follows the project, not the entity-tag count, so it is never demoted/parked.
        is_project = existing is not None and bool(existing.frontmatter.get("scope_key"))
        promoted = is_project or sum(1 for _, ln in members if not ln.superseded) >= MEMORY_MIN_ENTITY_RECORDS
        touched: set[Path] = set()
        for path, line in members:
            dest = entity_page if promoted else self._park_path(line)
            if path == dest:
                continue
            self._pages[path].lines = [ln for ln in self._pages[path].lines if ln.id != line.id]
            self._ensure_page(dest, title=(display if dest == entity_page else None)).lines.append(line)
            self._loc[line.id] = dest
            touched.add(path)
            touched.add(dest)
        if promoted:
            page = self._ensure_page(entity_page, title=display)
            # entity_labels is the page's OWN subject — exactly [display], NOT a growing
            # union of every secondary subject mentioned in its records (that made the
            # frontmatter a junk drawer: dex.md carrying GitHub/NTRP/Obsidian) and kept
            # casing duplicates (NTRP + ntrp) alive. Reset to the canonical display.
            want = [display]
            if page.frontmatter.get("title") != display or page.frontmatter.get("entity_labels") != want:
                page.frontmatter["title"] = display
                page.frontmatter["entity_labels"] = want
                touched.add(entity_page)  # frontmatter drift -> needs a write
        for p in touched:
            # A folded-away entity page with no records left is a dead file (its prose
            # regenerates for whichever page now holds the records); a promoted page
            # still holds its records so it survives this.
            if p.parent.name == _ENTITIES and not self._pages[p].lines:
                self._pages.pop(p, None)
                self._remove_page_files(p)
            else:
                self._persist(p)
        return entity_page if promoted else None

    async def reconcile_entities(self) -> dict[str, int]:
        """Full sweep: enforce the promotion threshold for every entity. Cheap +
        deterministic (no LLM, no index churn). Run at open() and after retention
        so a supersede that thins a page folds it back the same night. Sweeps both
        the slugs carried by lines AND existing entity-page files, so a page emptied
        by delete/prune/wipe (no tagged line left to name it) still gets reclaimed."""
        tagged = {ln.entity for page in self._pages.values() for ln in page.lines if ln.entity}
        files = {p.stem for p in self._pages if p.parent.name == _ENTITIES and p.name not in ("index.md", "needs-triage.md")}
        slugs = sorted(tagged | files)
        existed = {s for s in slugs if self._entity_path(s) in self._pages}
        for slug in slugs:
            self._reconcile_entity(slug)
        now = {s for s in slugs if self._entity_path(s) in self._pages}
        return {"entities": len(slugs), "promoted": len(now - existed), "demoted": len(existed - now)}

    def _heal_structural_pages(self) -> None:
        """Repair page identity contamination, normalize structural titles, and keep
        Obsidian wikilinks resolvable. Idempotent: a `scope_key` belongs only to a
        project page, so strip it from any non-project page (a project-scoped directive
        used to stamp it onto the global directives.md); give the fixed root pages
        canonical, properly-cased titles; and ensure each page's human title is in its
        `aliases` so prose `[[Title]]` links resolve to the dash-slug filename in
        Obsidian (preserving any aliases the user added in the vault)."""
        for path, page in self._pages.items():
            rel = path.relative_to(self._root)
            changed = False
            # scope_key belongs only on a topics/ subject page; strip it elsewhere (a
            # project-scoped directive used to stamp it onto the global directives.md).
            if rel.parts[0] != _ENTITIES and page.frontmatter.pop("scope_key", None) is not None:
                changed = True
            want = _STRUCTURAL_TITLES.get(rel.name) if len(rel.parts) == 1 else None
            if want and page.frontmatter.get("title") != want:
                page.frontmatter["title"] = want
                changed = True
            title = page.frontmatter.get("title")
            if title:
                aliases = page.frontmatter.get("aliases") or []
                if isinstance(aliases, str):
                    aliases = [aliases]
                needs_alias = title.lower() != rel.stem  # Obsidian's case-insensitive match would miss
                if needs_alias and title not in aliases:
                    page.frontmatter["aliases"] = [*aliases, title]
                    changed = True
                elif not needs_alias and aliases == [title]:
                    # redundant auto-alias (e.g. "Dex" on dex.md) — Obsidian resolves it already; drop the noise
                    del page.frontmatter["aliases"]
                    changed = True
            if changed:
                self._persist(path)

    def _migrate_to_topics(self) -> None:
        """Fold the legacy entities/ + projects/ folders into one topics/ folder.
        The split was incoherent: projects/ pages existed only when a CHAT was tagged
        to a project workspace, while entities/ emerged from labels — so the same
        subject (e.g. Dex) landed in BOTH, and real workstreams (e.g. MATS) hid under
        entities/. A subject now has exactly one topics/<slug>.md; scope lives in
        frontmatter (scope_key), not the folder. Idempotent: a no-op once migrated."""
        legacy = [p for p in list(self._pages.keys()) if p.parent.name in _LEGACY_SUBJECT_DIRS]
        for src in legacy:
            page = self._pages[src]
            target = self._root / _ENTITIES / src.name
            if target == src:
                continue
            existing = self._pages.get(target)
            if existing is None:  # reparent
                self._pages[target] = page
                del self._pages[src]
                for ln in page.lines:
                    self._loc[ln.id] = target
                page.frontmatter["type"] = "project" if page.frontmatter.get("scope_key") else "topic"
                self._persist(target)
            else:  # collision (e.g. Dex as both entity + project) — merge onto one page
                existing.lines.extend(page.lines)
                for ln in page.lines:
                    self._loc[ln.id] = target
                for key in ("scope_key", "title", "aliases", "entity_labels", "meta_labels"):
                    if key not in existing.frontmatter and key in page.frontmatter:
                        existing.frontmatter[key] = page.frontmatter[key]
                existing.frontmatter["type"] = "project" if existing.frontmatter.get("scope_key") else "topic"
                del self._pages[src]
                self._persist(target)
            self._remove_page_files(src)
        for name in _LEGACY_SUBJECT_DIRS:  # drop the now-empty legacy folders
            d = self._root / name
            if d.is_dir() and not any(d.iterdir()):
                try:
                    d.rmdir()
                except OSError:
                    pass

    def _retire_observations(self) -> None:
        """One-time/idempotent: the raw per-source integration streams
        (observations/<source>.md) are retired — targeted feed automations (feeds/)
        replaced them. Their records were 90d-TTL noise by design; drop the pages,
        their raw/ sidecars, and the records outright. Dream insights that cited
        them lose those cites via the synthesis dangling-cite pass."""
        pages = [p for p in list(self._pages.keys()) if p.parent.name == _OBSERVATIONS]
        dropped = 0
        for path in pages:
            for line in self._pages[path].lines:
                self._loc.pop(line.id, None)
                self._unindex_line(line.id)
                dropped += 1
            del self._pages[path]
            self._remove_page_files(path)
        for root in (self._root / _OBSERVATIONS, self._root / _RAW / _OBSERVATIONS):
            if root.is_dir() and not any(root.iterdir()):
                try:
                    root.rmdir()
                except OSError:
                    pass
        if pages:
            _logger.info("retired observation streams", pages=len(pages), records=dropped)

    def _migrate_insights(self) -> None:
        """One-time/idempotent: dream insights used to file to entities/insights.md via
        [ent:Insights]; they now belong in insights/<month>.md. Relocate any stray
        src=dreamer record so the emptied entity page is then dropped by reconcile."""
        for path in list(self._pages.keys()):
            if path.parent.name == _INSIGHTS:
                continue
            page = self._pages.get(path)
            if page is None:
                continue
            movers = [ln for ln in page.lines if ln.src == "dreamer"]
            if not movers:
                continue
            page.lines = [ln for ln in page.lines if ln.src != "dreamer"]
            for ln in movers:
                ln.entity = None
                month = (ln.date or now_iso())[:7]
                dest = self._root / _INSIGHTS / f"{month}.md"
                self._ensure_page(dest, title=f"Insights {month}").lines.append(ln)
                self._loc[ln.id] = dest
                self._persist(dest)
            self._persist(path)

    def _backfill_entities(self) -> None:
        """One-time: entity pages predate the per-line `entity` tag. Stamp each
        entities/<slug>.md record with its page slug so the promotion model sees it."""
        for path, page in self._pages.items():
            if path.parent.name != _ENTITIES or path.name in ("index.md", "needs-triage.md"):
                continue
            slug = path.stem
            changed = False
            for line in page.lines:
                if line.entity is None:
                    line.entity = slug
                    changed = True
            if changed:
                self._persist(path)

    def _write_conventions(self) -> None:
        """AGENTS.md (OKF conventions) — how this memory dir is shaped, so any agent
        reading it understands the format. Deterministic; refreshed each open() so the
        doc never drifts from the code."""
        path = self._root / "AGENTS.md"
        current = self._safe_read_text(path)
        if current != _CONVENTIONS:
            self._write_atomic(path, _CONVENTIONS)

    def _page_blurb(self, page: Page, *, record_list: bool = False) -> str:
        """One index line per page: the first real sentence of its prose (the honest
        summary — it was synthesized from the records), else its record count."""
        for raw in page.prose.splitlines():
            s = raw.strip()
            if not s or s.startswith("#") or s.startswith("---"):
                continue
            s = re.sub(r"\[\[(?:[^\]|]+\|)?([^\]]+)\]\]", r"\1", s)  # unwrap wikilinks
            s = re.sub(r"\s*\((?:from |inferred)[^)]*\)", "", s)  # source tags are page furniture, not index meaning
            s = s.lstrip("-• ").strip()
            if s:
                return s[:117] + "…" if len(s) > 120 else s
        active = len(page.active_lines())
        if not active:
            return "empty"
        # record-list pages (directives/lessons/references/insights) are the records —
        # they are never prose-synthesized, so "pending" would be a lie
        return f"{active} entries" if record_list else f"{active} records — synthesis pending"

    def _write_index(self) -> None:
        """index.md — the navigational backbone (Karpathy VII: navigate by index).
        Not a file tree (the browser shows that): one line of MEANING per page, so
        an agent can pick the right pages without loading the vault. Regenerated on
        open() and after synthesis."""
        def link(path: Path) -> str:
            rel = path.relative_to(self._root)
            title = str(self._pages[path].frontmatter.get("title") or path.stem)
            target = rel.as_posix().removesuffix(".md")
            return f"[[{target}|{title}]]"

        roots = [self._root / n for n in (_ME, "active-work.md", _DIRECTIVES, _LESSONS, _REFERENCES)]
        sections: list[tuple[str, list[Path]]] = [
            ("", [p for p in roots if p in self._pages]),
            ("Topics", sorted(p for p in self._pages if p.parent.name == _ENTITIES)),
            ("Feeds", sorted(p for p in self._pages if p.parent.name == "feeds")),
            ("Insights", sorted(p for p in self._pages if p.parent.name == _INSIGHTS)),
        ]
        record_list_pages = {self._root / _DIRECTIVES, self._root / _LESSONS, self._root / _REFERENCES}
        parts = ["# Memory index", ""]
        for heading, paths in sections:
            if not paths:
                continue
            if heading:
                parts += [f"## {heading}", ""]
            parts += [
                f"- {link(p)} — {self._page_blurb(self._pages[p], record_list=p in record_list_pages or heading == 'Insights')}"
                for p in paths
            ]
            parts.append("")
        dailies = sorted(p.stem for p in self._pages if p.parent.name == "daily")
        if dailies:
            parts += ["## Daily", "", f"- `daily/` — {len(dailies)} dated logs, newest [[daily/{dailies[-1]}|{dailies[-1]}]]", ""]
        self._write_atomic(self._root / "index.md", "\n".join(parts).rstrip() + "\n")

    def _write_health(self) -> None:
        """health.md — a deterministic self-audit that surfaces blind spots (doc
        principle 11): stale topics, idle integration sources, and whether the dream/
        synthesis have run. Makes gaps visible instead of silently rotting."""
        today = datetime.now(UTC).date()
        STALE_DAYS, DREAM_DAYS = 90, 7

        def _age(d: str) -> int | None:
            try:
                return (today - date.fromisoformat(d[:10])).days
            except ValueError:
                return None

        records = [ln for pg in self._pages.values() for ln in pg.active_lines()]
        by_kind: dict[str, int] = {}
        for ln in records:
            by_kind[ln.kind] = by_kind.get(ln.kind, 0) + 1
        last_dream = max((ln.date for ln in records if ln.src == "dreamer"), default=None)
        last_synth = max((str(pg.frontmatter.get("prose_synced")) for pg in self._pages.values()
                          if pg.frontmatter.get("prose_synced")), default=None)

        gaps: list[str] = []
        dream_age = _age(last_dream) if last_dream else None  # None != 0 — don't let a same-day dream read as "never"
        if dream_age is None or dream_age > DREAM_DAYS:
            gaps.append(f"- Cross-domain dream hasn't run recently (last: {last_dream or 'never'}) — fewer net-new insights.")
        # Inbound wikilinks across all prose — a topic no page links to is an orphan
        # (Karpathy VIII: the value of the wiki is in the edges, not the nodes).
        linked = {
            m.group(1).strip().lower()
            for pg in self._pages.values()
            for m in re.finditer(r"\[\[([^\]#|]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]", pg.prose)
        }
        live_ids = {ln.id.lower() for ln in records}
        for path, pg in sorted(self._pages.items()):
            rel = path.relative_to(self._root).as_posix()
            grounding = pg.frontmatter.get("prose_cites") or []
            dead = [i for i in grounding if str(i).lower() not in live_ids]
            if dead:
                gaps.append(f"- Dangling grounding: `{rel}` cites {len(dead)} pruned record(s) — re-synthesis pending.")
            if path.parent.name == _ENTITIES:
                newest = max((ln.date for ln in pg.active_lines()), default="")
                a = _age(newest)
                if a is not None and a > STALE_DAYS:
                    gaps.append(f"- Stale topic: `topics/{path.stem}.md` — no update in {a}d (since {newest}).")
                if not pg.prose and pg.active_lines():
                    gaps.append(f"- Pending synthesis: `{rel}` has {len(pg.active_lines())} records but no compiled prose yet.")
                title = str(pg.frontmatter.get("title") or path.stem).lower()
                refs = {title, path.stem.lower(), rel.removesuffix(".md").lower()}
                aliases = pg.frontmatter.get("aliases") or []
                refs |= {str(al).lower() for al in (aliases if isinstance(aliases, list) else [aliases])}
                if not (refs & linked):
                    gaps.append(f"- Orphan topic: `topics/{path.stem}.md` — no other page links to it.")

        parts = [
            "# Memory health", "",
            f"{len(records)} active records across {len(self._pages)} pages — "
            + (", ".join(f"{k} {v}" for k, v in sorted(by_kind.items())) or "empty"),
            "",
            f"Last synthesis: {last_synth or 'never'} · last dream: {last_dream or 'never'}",
            "", "## Gaps", "",
            *(sorted(gaps) or ["- None — memory is current."]),
            "", "_Conflicting records are reconciled nightly by the consolidation pass "
            "(it supersedes a contradicted record into the newer one)._",
        ]
        self._write_atomic(self._root / "health.md", "\n".join(parts).rstrip() + "\n")

    def _ensure_page(self, path: Path, *, title: str | None = None) -> Page:
        page = self._pages.get(path)
        if page is None:
            try:
                rel = path.relative_to(self._root)
            except ValueError:
                rel = path
            page_type = {"entities": "entity", "projects": "project"}.get(rel.parts[0], "topic") if len(rel.parts) > 1 else "topic"
            canonical = _STRUCTURAL_TITLES.get(rel.name) if len(rel.parts) == 1 else None
            resolved = title or canonical or path.stem
            fm = {"type": page_type, "title": resolved, "updated": now_iso()[:10]}
            if resolved.lower() != path.stem:  # only when Obsidian's case-insensitive filename match fails
                fm["aliases"] = [resolved]  # e.g. [[Interaction Lab]] -> interaction-lab.md (not for "Dex"->dex.md)
            page = Page(frontmatter=fm)
            self._pages[path] = page
        return page

    def _raw_path(self, path: Path) -> Path:
        return self._root / _RAW / path.relative_to(self._root)

    def _write_atomic(self, path: Path, text: str) -> None:
        self._journal.replace_file_safely(path.relative_to(self._root), text.encode("utf-8"))
        self._file_state = self._scan_files()

    def _persist(self, path: Path) -> None:
        self._persist_many((path,))

    def _notify_post_canonical_commit(self) -> None:
        if self._post_canonical_commit is None:
            return
        try:
            self._post_canonical_commit()
        except Exception:
            _logger.warning("post-canonical projection scheduling failed", exc_info=True)

    @staticmethod
    def _validate_caller_files(
        files: Mapping[Path, bytes] | None,
        file_roles: Mapping[Path, CanonicalFileRole] | None,
    ) -> dict[Path, bytes]:
        payload = dict(files or {})
        roles = dict(file_roles or {})
        if set(payload) != set(roles):
            raise ValueError("every caller file requires exactly one matching role")
        if any(not isinstance(role, CanonicalFileRole) for role in roles.values()):
            raise ValueError("caller file role must be a CanonicalFileRole")
        if any(role not in {CanonicalFileRole.USER_PAGE, CanonicalFileRole.EVENT} for role in roles.values()):
            raise ValueError("projection caller files cannot enter a canonical commit")
        return payload

    def _persist_many(self, paths: Sequence[Path], *, files: Mapping[Path, bytes] | None = None) -> None:
        extra = dict(files or {})
        staged: dict[Path, bytes] = {}
        projections: dict[Path, str] = {}
        empty_raw: list[Path] = []
        for path in sorted(set(paths)):
            page = self._pages[path]
            page.frontmatter["updated"] = now_iso()[:10]
            visible = render_page(page)
            raw = render_raw(page)
            raw_path = self._raw_path(path)
            existing_raw = self._safe_read_text(raw_path)
            raw_changed = bool(raw) and existing_raw != raw
            if raw_changed:
                staged[path.relative_to(self._root)] = visible.encode()
                staged[raw_path.relative_to(self._root)] = raw.encode()
            else:
                projections[path] = visible
            if not raw:
                empty_raw.append(raw_path)
        staged.update(extra)
        for rel in extra:
            projections.pop(self._root / rel, None)
            if self._root / rel in empty_raw:
                empty_raw.remove(self._root / rel)
        if staged:
            self._journal.commit(staged)
        for path, text in projections.items():
            self._write_atomic(path, text)
        self._journal.unlink_files_safely(tuple(path.relative_to(self._root) for path in empty_raw))
        for path in sorted(set(paths)):
            rel = path.relative_to(self._root)
            content = self._safe_read_bytes(path)
            self.register_engine_write(rel.as_posix(), content, origin="synthesis")
        self._file_state = self._scan_files()
        self._notify_post_canonical_commit()

    def _remove_page_files(self, path: Path) -> None:
        self._journal.unlink_files_safely(
            (path.relative_to(self._root), self._raw_path(path).relative_to(self._root))
        )
        self.register_engine_write(path.relative_to(self._root).as_posix(), None, origin="synthesis")
        self._file_state = self._scan_files()

    def _find(self, record_id: str) -> tuple[Path, Line | LedgerEntry] | None:
        path = self._loc.get(record_id)
        if path is None:
            return None
        for line in self._pages[path].lines:
            if line.id == record_id:
                return path, line
        return None

    def _append(self, path: Path, line: Line | LedgerEntry, *, title: str | None = None) -> None:
        page = self._ensure_page(path, title=title)
        page.lines.append(line)
        self._loc[line.id] = path
        self._persist(path)

    def append_entries(
        self,
        entries: Sequence[LedgerEntry],
        *,
        files: Mapping[Path, bytes] | None = None,
        file_roles: Mapping[Path, CanonicalFileRole] | None = None,
    ) -> None:
        """Validate and append immutable schema-v2 entries without rewriting history."""
        caller_files = self._validate_caller_files(files, file_roles)
        additions = tuple(entries)
        if not additions:
            return
        self._validate_ledger_entries((*self._ledger_entries(), *additions))
        touched: set[Path] = set()
        for entry in additions:
            target_path = next(
                (self._loc[target] for target in entry.meta.supersedes if target in self._loc),
                None,
            )
            path = target_path or self._page_for(entry.kind, entry.meta.scope_kind, entry.meta.scope_key)
            page = self._ensure_page(path)
            if page.records_header is None:
                rel = path.relative_to(self._root).as_posix()
                page.records_header = f"<!-- ntrp:records schema=2 page={rel} -->"
            page.lines.append(entry)
            self._loc[entry.id] = path
            touched.add(path)
        try:
            self._persist_many(tuple(touched), files=caller_files)
        except Exception:
            self._journal.recover(prefer_rollback=True)
            self._reload_canonical_state()
            raise
        if file_roles and CanonicalFileRole.USER_PAGE in file_roles.values():
            self._reload_canonical_state()
        self._active_ledger_entries()

    def plan_operations(
        self,
        operations: Sequence[RecordOperation],
        source: SourceRef | Sequence[SourceRef],
        *,
        batch_key: str | None = None,
    ) -> Mapping[Path, bytes]:
        """Build a complete schema-v2 reconciliation commit without writing."""
        if not self._ledger_mode():
            raise ValueError("typed reconciliation requires a schema-v2 vault")
        active = tuple(self._to_record(entry, self._loc[entry.id]) for entry in self._active_ledger_entries())
        sources = (source,) if isinstance(source, SourceRef) else tuple(source)
        validated = validate_operations(tuple(operations), active, sources)
        pages = {path: deepcopy(page) for path, page in self._pages.items()}
        entries_by_id = {entry.id: entry for entry in self._ledger_entries()}
        additions: list[LedgerEntry] = []
        touched: set[Path] = set()
        generated_ids: set[str] = set()
        sequence = self._next_sequence()

        def new_id() -> str:
            while True:
                record_id = uuid4().hex[:8]
                if record_id not in self._loc and record_id not in generated_ids:
                    generated_ids.add(record_id)
                    return record_id

        for operation in validated:
            if operation.op in {"ASK", "NOOP"}:
                continue
            targets = [entries_by_id[target] for target in operation.target_ids]
            scope = operation.scope
            assert scope is not None
            recorded_at = now_iso()
            primary_source = sources[0]
            occurred_at = primary_source.occurred_at or recorded_at
            precision = primary_source.time_precision if primary_source.occurred_at else "millisecond"
            target_sources = tuple(src for target in targets for src in target.meta.sources)
            evidence = union_source_refs(target_sources, sources)
            kind = operation.kind or (targets[0].kind if targets else Kind.FACT)
            operation_kind = "retract" if operation.op == "RETRACT" else "record"
            text = operation.text or (targets[0].text if targets else "")
            entity = (
                tuple(_slug(label) for label in operation.entity_labels if _slug(label))
                if operation.entity_labels is not None
                else tuple(dict.fromkeys(entity for target in targets for entity in target.entity))
            )
            entry = LedgerEntry(
                id=new_id(),
                text=_norm(text),
                kind=Kind(kind),
                occurred_at=occurred_at,
                pinned=any(target.pinned for target in targets),
                entity=entity,
                meta=LedgerMeta(
                    recorded_at=recorded_at,
                    sequence=sequence,
                    time_precision=precision,
                    scope_kind=scope.kind or "user",
                    scope_key=scope.key,
                    sources=evidence,
                    supersedes=operation.target_ids,
                    operation=operation_kind,
                    extra=({"batch_key": batch_key} if batch_key else {}),
                ),
            )
            sequence += 1
            target_path = self._loc[operation.target_ids[0]] if operation.target_ids else None
            path = target_path or self._page_for(entry.kind, entry.meta.scope_kind, entry.meta.scope_key)
            page = pages.get(path)
            if page is None:
                page = Page()
                pages[path] = page
            if page.records_header is None:
                rel = path.relative_to(self._root).as_posix()
                page.records_header = f"<!-- ntrp:records schema=2 page={rel} -->"
            if operation.meta_labels is not None:
                page.frontmatter["meta_labels"] = sorted(set(operation.meta_labels))
            page.frontmatter["updated"] = now_iso()[:10]
            page.lines.append(entry)
            entries_by_id[entry.id] = entry
            additions.append(entry)
            touched.add(path)

        self._validate_ledger_entries((*self._ledger_entries(), *additions))
        planned: dict[Path, bytes] = {}
        for path in sorted(touched):
            page = pages[path]
            planned[path.relative_to(self._root)] = render_page(page).encode()
            planned[self._raw_path(path).relative_to(self._root)] = render_raw(page).encode()
        return planned

    def apply_operations(
        self,
        operations: Sequence[RecordOperation],
        source: SourceRef | Sequence[SourceRef],
        *,
        batch_key: str | None = None,
    ) -> str:
        """Validate, plan, and publish one reconciliation batch in one commit."""
        if batch_key and self.operation_batch_committed(batch_key):
            return self.canonical_revision
        planned = self.plan_operations(operations, source, batch_key=batch_key)
        if not planned:
            return self.canonical_revision
        try:
            revision = self._journal.commit(planned)
        except Exception:
            self._journal.recover(prefer_rollback=True)
            self._reload_canonical_state()
            raise
        self._reload_canonical_state()
        for rel, content in planned.items():
            if rel.parts[0] != _RAW and rel.suffix.casefold() == ".md":
                self.register_engine_write(rel.as_posix(), content, origin="synthesis")
        self._notify_post_canonical_commit()
        return revision

    def operation_batch_committed(self, batch_key: str) -> bool:
        return any(entry.meta.extra.get("batch_key") == batch_key for entry in self._ledger_entries())

    def history(self, record_id: str) -> tuple[LedgerEntry, ...]:
        """Return the complete connected lifecycle history for one ledger record."""
        entries = self._ledger_entries()
        if not any(entry.id == record_id for entry in entries):
            return ()
        related = {record_id}
        changed = True
        while changed:
            changed = False
            for entry in entries:
                links = {entry.id, *entry.meta.supersedes}
                if entry.meta.successor_id is not None:
                    links.add(entry.meta.successor_id)
                if links & related and not links <= related:
                    related.update(links)
                    changed = True
        return tuple(
            sorted(
                (entry for entry in entries if entry.id in related),
                key=lambda entry: (
                    datetime.fromisoformat(entry.meta.recorded_at.replace("Z", "+00:00")).astimezone(UTC),
                    entry.meta.sequence,
                ),
            )
        )

    # -- writes --------------------------------------------------------------

    async def add(
        self,
        text: str,
        *,
        kind: str = Kind.FACT,
        pinned: bool = False,
        source_ref: SourceRef | None = None,
        scope_kind: str | None = None,
        scope_key: str | None = None,
        record_id: str | None = None,
        entity_labels: list[str] | None = None,
        date: str | None = None,
    ) -> Record:
        rid = record_id or self._new_id()
        if self._ledger_mode():
            recorded_at = now_iso()
            precision = "day" if date else "millisecond"
            occurred_at = date or recorded_at
            entry = LedgerEntry(
                id=rid,
                text=_norm(text),
                kind=Kind(kind),
                occurred_at=occurred_at,
                pinned=pinned,
                entity=((_slug(entity_labels[0]),) if entity_labels else ()),
                meta=LedgerMeta(
                    recorded_at=recorded_at,
                    sequence=self._next_sequence(),
                    time_precision=precision,
                    scope_kind=scope_kind or "user",
                    scope_key=scope_key,
                    sources=((source_ref,) if source_ref is not None else ()),
                ),
            )
            self.append_entries((entry,))
            self._index_line(entry)
            found = self._find(entry.id)
            assert found is not None
            return self._to_record(entry, found[0])
        line = Line(
            id=rid,
            text=_norm(text),
            kind=str(kind),
            date=(date or now_iso())[:10],
            src=(source_ref.kind if source_ref else "unknown"),
            pinned=pinned,
        )
        if source_ref is not None and source_ref.kind == "dreamer":
            # Cross-domain dream insights get their own dated folder (OKF insights/),
            # separate from facts/entities; retention ages them as provisional.
            base = self._root / _INSIGHTS / f"{line.date[:7]}.md"
            title = f"Insights {line.date[:7]}"
        else:
            base = self._page_for(str(kind), scope_kind, scope_key)
            primary = _slug(entity_labels[0]) if entity_labels else None
            if primary and base.name in _PARKABLE:
                line.entity = primary  # remembered even while parked, so a later record can promote it
            # A project-scoped directive/lesson routes to the GLOBAL directives.md/
            # lessons.md by kind — its project identity must NOT stamp that page. Only a
            # subject page in topics/ takes the project title/scope_key.
            on_project_page = scope_kind in ("area", "project") and scope_key and base.parent.name == _ENTITIES
            title = self._project_names.get(scope_key, scope_key) if on_project_page else None
        self._append(base, line, title=title)
        # Persist the raw project key so non-slug-safe keys round-trip (the filename
        # is a lossy slug; _scope_for reads scope_key from frontmatter). Only on the
        # actual topics/ subject page — never on a global page a project rule landed on.
        if scope_kind in ("area", "project") and scope_key and base.parent.name == _ENTITIES:
            self._pages[base].frontmatter["scope_key"] = scope_key
            self._pages[base].frontmatter["type"] = "project"
            self._persist(base)
        self._index_line(line)
        if line.entity:
            final = self._reconcile_entity(line.entity, display=entity_labels[0]) or base
        else:
            final = base
            if entity_labels:  # project/directive-scoped + entity-labeled: tag the page, don't move it
                self._merge_labels(base, entity=entity_labels)
        return self._to_record(line, final)

    async def supersede(self, old_id: str, new_id: str) -> bool:
        found = self._find(old_id)
        if not found:
            return False
        path, line = found
        if isinstance(line, LedgerEntry):
            successor_found = self._find(new_id)
            active_ids = {entry.id for entry in self._active_ledger_entries()}
            if (
                successor_found is None
                or not isinstance(successor_found[1], LedgerEntry)
                or old_id not in active_ids
                or new_id not in active_ids
            ):
                return False
            successor = successor_found[1]
            recorded_at = now_iso()
            retract = LedgerEntry(
                id=self._new_id(),
                text=line.text,
                kind=line.kind,
                occurred_at=line.occurred_at,
                entity=line.entity,
                meta=LedgerMeta(
                    recorded_at=recorded_at,
                    sequence=self._next_sequence(),
                    time_precision=line.meta.time_precision,
                    scope_kind=line.meta.scope_kind,
                    scope_key=line.meta.scope_key,
                    sources=union_source_refs(line.meta.sources, successor.meta.sources),
                    supersedes=(old_id,),
                    successor_id=new_id,
                    operation="retract",
                ),
            )
            self.append_entries((retract,))
            self._unindex_line(old_id)
            return True
        line.superseded = True
        self._persist(path)
        self._unindex_line(old_id)
        return True

    async def supersede_with(
        self,
        old_id: str,
        *,
        text: str,
        kind: str = Kind.FACT,
        source_ref: SourceRef | None = None,
        scope_kind: str | None = None,
        scope_key: str | None = None,
    ) -> Record:
        found = self._find(old_id)
        if found and isinstance(found[1], LedgerEntry):
            old = found[1]
            recorded_at = now_iso()
            successor = LedgerEntry(
                id=self._new_id(),
                text=_norm(text),
                kind=Kind(kind),
                occurred_at=(source_ref.occurred_at if source_ref and source_ref.occurred_at else old.occurred_at),
                pinned=old.pinned,
                imp=None,
                entity=old.entity,
                meta=LedgerMeta(
                    recorded_at=recorded_at,
                    sequence=self._next_sequence(),
                    time_precision=(source_ref.time_precision if source_ref and source_ref.occurred_at else old.meta.time_precision),
                    scope_kind=scope_kind or old.meta.scope_kind,
                    scope_key=(scope_key if scope_kind is not None else old.meta.scope_key),
                    sources=union_source_refs(old.meta.sources, ((source_ref,) if source_ref else ())),
                    supersedes=(old_id,),
                ),
            )
            self.append_entries((successor,))
            self._unindex_line(old_id)
            self._index_line(successor)
            return self._to_record(successor, found[0])
        old_entity = found[1].entity if found else None
        old_display = self._pages[found[0]].frontmatter.get("title") if (found and found[0].parent.name == _ENTITIES) else None
        # Add the successor FIRST: a failure mid-op then leaves a harmless duplicate
        # rather than an old record superseded with no replacement (data loss).
        record = await self.add(text, kind=kind, source_ref=source_ref, scope_kind=scope_kind, scope_key=scope_key)
        if found:
            old_path, old_line = found
            old_line.superseded = True
            self._persist(old_path)
            self._unindex_line(old_id)
        if old_entity:
            succ = self._find(record.id)
            if succ and succ[1].entity is None:
                succ[1].entity = old_entity
                self._persist(succ[0])
            self._reconcile_entity(old_entity, display=old_display)  # place successor + fold the now-thinner old page
        return record

    async def set_kind(self, record_id: str, kind: str) -> bool:
        found = self._find(record_id)
        if not found:
            return False
        path, line = found
        if isinstance(line, LedgerEntry):
            if record_id not in {entry.id for entry in self._active_ledger_entries()}:
                return False
            successor = LedgerEntry(
                id=self._new_id(),
                text=line.text,
                kind=Kind(kind),
                occurred_at=line.occurred_at,
                pinned=line.pinned,
                imp=line.imp,
                entity=line.entity,
                meta=replace(
                    line.meta,
                    recorded_at=now_iso(),
                    sequence=self._next_sequence(),
                    supersedes=(record_id,),
                ),
            )
            self.append_entries((successor,))
            self._unindex_line(record_id)
            self._index_line(successor)
            return True
        line.kind = str(kind)
        self._persist(path)
        return True

    async def confirm(self, record_id: str) -> bool:
        found = self._find(record_id)
        if not found:
            return False
        path, line = found
        if isinstance(line, LedgerEntry):
            if record_id not in {entry.id for entry in self._active_ledger_entries()}:
                return False
            confirmed = replace(
                line,
                meta=replace(line.meta, recorded_at=now_iso(), sequence=self._next_sequence()),
            )
            self._replace_ledger_entry(path, confirmed)
            return True
        line.date = now_iso()[:10]
        self._persist(path)
        return True

    async def set_pinned(self, record_id: str, pinned: bool) -> bool:
        found = self._find(record_id)
        if not found:
            return False
        path, line = found
        if isinstance(line, LedgerEntry):
            if record_id not in {entry.id for entry in self._active_ledger_entries()}:
                return False
            self._replace_ledger_entry(path, replace(line, pinned=bool(pinned)))
            return True
        line.pinned = bool(pinned)
        self._persist(path)
        return True

    async def update(self, record_id: str, text: str, *, source_ref: SourceRef | None = None) -> bool:
        found = self._find(record_id)
        if not found:
            return False
        path, line = found
        if isinstance(line, LedgerEntry):
            recorded_at = now_iso()
            successor = LedgerEntry(
                id=self._new_id(),
                text=_norm(text),
                kind=line.kind,
                occurred_at=(source_ref.occurred_at if source_ref and source_ref.occurred_at else line.occurred_at),
                pinned=line.pinned,
                entity=line.entity,
                meta=LedgerMeta(
                    recorded_at=recorded_at,
                    sequence=self._next_sequence(),
                    time_precision=(source_ref.time_precision if source_ref and source_ref.occurred_at else line.meta.time_precision),
                    scope_kind=line.meta.scope_kind,
                    scope_key=line.meta.scope_key,
                    sources=union_source_refs(line.meta.sources, ((source_ref,) if source_ref else ())),
                    supersedes=(record_id,),
                ),
            )
            self.append_entries((successor,))
            self._unindex_line(record_id)
            self._index_line(successor)
            return True
        line.text = _norm(text)
        line.date = now_iso()[:10]
        line.imp = None  # text changed -> re-score on next sweep
        self._persist(path)
        self._index_line(line)
        return True

    async def delete(self, record_id: str, *, source_ref: SourceRef | None = None) -> None:
        found = self._find(record_id)
        if not found:
            return
        path, line = found
        if isinstance(line, LedgerEntry):
            if record_id not in {entry.id for entry in self._active_ledger_entries()}:
                return
            recorded_at = now_iso()
            initiating = source_ref or SourceRef(
                "memory_operation",
                f"delete:{record_id}",
                captured_at=recorded_at,
            )
            retract = LedgerEntry(
                id=self._new_id(),
                text=line.text,
                kind=line.kind,
                occurred_at=line.occurred_at,
                pinned=False,
                entity=line.entity,
                meta=LedgerMeta(
                    recorded_at=recorded_at,
                    sequence=self._next_sequence(),
                    time_precision=line.meta.time_precision,
                    scope_kind=line.meta.scope_kind,
                    scope_key=line.meta.scope_key,
                    sources=union_source_refs(line.meta.sources, (initiating,)),
                    supersedes=(record_id,),
                    operation="retract",
                ),
            )
            self.append_entries((retract,))
            self._unindex_line(record_id)
            return
        entity = line.entity
        self._pages[path].lines = [ln for ln in self._pages[path].lines if ln.id != record_id]
        self._loc.pop(record_id, None)
        self._persist(path)
        self._unindex_line(record_id)
        if entity:  # a delete that drops a topic below the threshold must fold it now, not next sweep
            self._reconcile_entity(entity)

    async def prune(self) -> dict[str, int]:
        """Hard-delete tombstoned (superseded) lines from their pages + evict their
        vectors. Idempotent: a store with no superseded lines prunes nothing."""
        if self._ledger_mode():
            return {"records": 0}
        removed = 0
        for path, page in list(self._pages.items()):
            dead = [ln for ln in page.lines if ln.superseded]
            if not dead:
                continue
            page.lines = [ln for ln in page.lines if not ln.superseded]
            for ln in dead:
                self._loc.pop(ln.id, None)
                self._unindex_line(ln.id)
                removed += 1
            self._persist(path)
        return {"records": removed}

    async def wipe_except_pinned(self) -> dict[str, int]:
        """/init re-derivation primitive: delete every non-pinned line across all
        pages, keeping pinned survivors. Mirrors RecordStore.wipe_except_pinned."""
        if self._ledger_mode():
            active = self._active_ledger_entries()
            victims = [entry for entry in active if not entry.pinned]
            kept = sum(1 for entry in active if entry.pinned)
            if not victims:
                return {"deleted": 0, "kept_pinned": kept}
            sequence = self._next_sequence()
            retracts = []
            for offset, entry in enumerate(victims):
                recorded_at = now_iso()
                source = SourceRef(
                    "memory_operation",
                    f"wipe:{entry.id}",
                    captured_at=recorded_at,
                )
                retracts.append(
                    LedgerEntry(
                        id=uuid4().hex[:8],
                        text=entry.text,
                        kind=entry.kind,
                        occurred_at=entry.occurred_at,
                        entity=entry.entity,
                        meta=LedgerMeta(
                            recorded_at=recorded_at,
                            sequence=sequence + offset,
                            time_precision=entry.meta.time_precision,
                            scope_kind=entry.meta.scope_kind,
                            scope_key=entry.meta.scope_key,
                            sources=union_source_refs(entry.meta.sources, (source,)),
                            supersedes=(entry.id,),
                            operation="retract",
                        ),
                    )
                )
            self.append_entries(retracts)
            for entry in victims:
                self._unindex_line(entry.id)
            return {"deleted": len(victims), "kept_pinned": kept}
        deleted = kept = 0
        for path, page in list(self._pages.items()):
            keep = [ln for ln in page.lines if ln.pinned]
            drop = [ln for ln in page.lines if not ln.pinned]
            kept += len(keep)
            if not drop:
                continue
            page.lines = keep
            for ln in drop:
                self._loc.pop(ln.id, None)
                self._unindex_line(ln.id)
                deleted += 1
            self._persist(path)
        return {"deleted": deleted, "kept_pinned": kept}

    # -- labels --------------------------------------------------------------

    def _merge_labels(self, path: Path, *, entity: list[str] | None = None, meta: list[str] | None = None) -> None:
        page = self._ensure_page(path)
        if entity:
            cur = page.frontmatter.get("entity_labels", [])
            page.frontmatter["entity_labels"] = sorted({*cur, *entity})
        if meta:
            cur = page.frontmatter.get("meta_labels", [])
            page.frontmatter["meta_labels"] = sorted({*cur, *meta})
        self._persist(path)

    async def set_labels(self, record_id: str, labels: list[str], *, entity_labels: list[str] | None = None) -> None:
        found = self._find(record_id)
        if not found:
            return
        path, line = found
        if isinstance(line, LedgerEntry):
            if record_id not in {entry.id for entry in self._active_ledger_entries()}:
                return
            updated = replace(
                line,
                entity=tuple(_slug(label) for label in (entity_labels or []) if _slug(label)),
            )
            page = self._pages[path]
            page.frontmatter["meta_labels"] = sorted(set(labels))
            self._replace_ledger_entry(path, updated)
            return
        primary = _slug(entity_labels[0]) if entity_labels else None
        # Entity-place only records on the generic pages or an existing entity page;
        # project/directive-scoped records keep their page (scope precedence).
        placeable = path.name in _PARKABLE or path.parent.name == _ENTITIES
        final, merge_entity = path, entity_labels
        if primary and placeable:
            old = line.entity
            line.entity = primary  # save the tag before reconcile, so a no-move park still records it
            self._persist(path)
            self._reconcile_entity(primary, display=entity_labels[0])
            final = self._loc.get(record_id, path)
            if old and old != primary:
                self._reconcile_entity(old)  # re-tag: fold the page it left if it went thin
            merge_entity = None  # promotion writes the entity_labels frontmatter; me.md stays clean
        if merge_entity or labels:
            self._merge_labels(final, entity=merge_entity, meta=labels)

    async def add_labels(self, record_id: str, labels: list[str], *, entity_labels: list[str] | None = None) -> None:
        found = self._find(record_id)
        if found and isinstance(found[1], LedgerEntry):
            path, line = found
            if record_id not in {entry.id for entry in self._active_ledger_entries()}:
                return
            entities = tuple(
                dict.fromkeys((*line.entity, *(_slug(label) for label in (entity_labels or []) if _slug(label))))
            )
            page = self._pages[path]
            page.frontmatter["meta_labels"] = sorted({*self._meta_labels(path), *labels})
            self._replace_ledger_entry(path, replace(line, entity=entities))
            return
        await self.set_labels(record_id, labels, entity_labels=entity_labels)

    def _record_entities(self, path: Path, line: Line | LedgerEntry) -> list[str]:
        """Entity labels for one record: its per-line entity (so a sub-threshold record
        parked on me.md still surfaces its entity) plus any on the page frontmatter."""
        ents = list(self._entity_labels(path))
        if isinstance(line, LedgerEntry):
            ents.extend(self._entity_display(entity) for entity in line.entity)
        elif line.entity:
            ents.append(self._entity_display(line.entity))
        return ents

    async def labels_of(self, record_id: str) -> list[str]:
        found = self._find(record_id)
        if not found:
            return []
        path, line = found
        return sorted({*self._record_entities(path, line), *self._meta_labels(path)})

    async def labels_for(self, record_ids: list[str], *, include_kind: bool = False) -> dict:
        out: dict[str, list] = {}
        for rid in record_ids:
            found = self._find(rid)
            ents = sorted(set(self._record_entities(*found))) if found else []
            metas = self._meta_labels(found[0]) if found else []
            if include_kind:
                out[rid] = [{"label": l, "kind": "entity"} for l in ents] + [{"label": l, "kind": "meta"} for l in metas]
            else:
                out[rid] = sorted({*ents, *metas})
        return out

    async def list_labels(self) -> list[dict]:
        counts: dict[str, dict] = {}
        # Entity labels are counted per tagged active line — accurate per-entity
        # totals, including records still parked on me.md below the promotion threshold.
        if self._ledger_mode():
            for line in self._active_ledger_entries():
                for entity in line.entity:
                    label = self._entity_display(entity)
                    row = counts.setdefault(label, {"label": label, "count": 0, "kind": "entity"})
                    row["count"] += 1
        else:
            for page in self._pages.values():
                for line in page.active_lines():
                    if not line.entity:
                        continue
                    label = self._entity_display(line.entity)
                    row = counts.setdefault(label, {"label": label, "count": 0, "kind": "entity"})
                    row["count"] += 1
        # Meta labels are page-level category tags (no per-line refinement).
        active_ids = {entry.id for entry in self._active_ledger_entries()} if self._ledger_mode() else set()
        for path, page in self._pages.items():
            active = (
                sum(1 for line in page.lines if isinstance(line, LedgerEntry) and line.id in active_ids)
                if self._ledger_mode()
                else len(page.active_lines())
            )
            if not active:
                continue
            for label in self._meta_labels(path):
                row = counts.setdefault(label, {"label": label, "count": 0, "kind": "meta"})
                if row["kind"] == "meta":
                    row["count"] += active
        return sorted(counts.values(), key=lambda r: (-r["count"], r["label"]))

    # -- consolidation primitives (the nightly Consolidate/dedup engine uses these) --

    async def neighborhood(self, record: Record, *, limit: int = 8) -> list[Record]:
        """Active records that resemble `record` (hybrid recall) minus itself — its
        consolidation neighborhood (the merge-candidate set)."""
        hits = await self.search(record.text, limit=limit + 1, scopes=None)
        return [h for h in hits if h.id != record.id][:limit]

    async def updated_since(self, watermark: str | None, *, limit: int) -> list[Record]:
        """The whole active pool, oldest-first. The `watermark` is intentionally NOT a
        skip filter: file records are DAY-granular ('<date>T00:00') with non-monotonic
        ids, so a finer-grained watermark would permanently skip records added after a
        same-day sweep. Returning the whole pool is correct + cheap because the consumer
        (Consolidate) skips unchanged neighborhoods via a content-fingerprint cache, so
        there's no per-night re-judging cost and no record-count ceiling."""
        recs = await self.list(limit=None, scopes=None)
        recs.sort(key=lambda r: (r.last_confirmed_at or "", r.id))
        return recs[:limit]

    async def merge(
        self, survivor_id: str, loser_ids: list[str], *, text: str | None = None, kind: str | None = None
    ) -> Record | None:
        """Collapse N records into ONE: each loser is superseded onto the survivor and
        evicted from the vector index; the survivor gains the union of all members' meta
        labels. `text` re-texts + re-confirms (re-scores) the survivor; `kind` retypes it.
        Aborts (None) if the survivor or ANY loser is pinned — pinned records are never
        merged away."""
        if self._ledger_mode():
            active = {entry.id: entry for entry in self._active_ledger_entries()}
            predecessor_ids = tuple(dict.fromkeys((survivor_id, *(rid for rid in loser_ids if rid != survivor_id))))
            predecessors = [active[rid] for rid in predecessor_ids if rid in active]
            if not predecessors or predecessors[0].id != survivor_id or any(entry.pinned for entry in predecessors):
                return None
            if len({(entry.meta.scope_kind, entry.meta.scope_key) for entry in predecessors}) != 1:
                return None
            survivor_entry = predecessors[0]
            meta_labels = {
                label
                for entry in predecessors
                for label in self._meta_labels(self._loc[entry.id])
            }
            recorded_at = now_iso()
            successor = LedgerEntry(
                id=self._new_id(),
                text=_norm(text if text is not None else survivor_entry.text),
                kind=Kind(kind or survivor_entry.kind),
                occurred_at=survivor_entry.occurred_at,
                pinned=False,
                entity=survivor_entry.entity,
                meta=LedgerMeta(
                    recorded_at=recorded_at,
                    sequence=self._next_sequence(),
                    time_precision=survivor_entry.meta.time_precision,
                    scope_kind=survivor_entry.meta.scope_kind,
                    scope_key=survivor_entry.meta.scope_key,
                    sources=union_source_refs(*(entry.meta.sources for entry in predecessors)),
                    supersedes=tuple(entry.id for entry in predecessors),
                ),
            )
            self.append_entries((successor,))
            successor_path = self._loc[successor.id]
            if meta_labels:
                self._pages[successor_path].frontmatter["meta_labels"] = sorted(meta_labels)
                self._persist(successor_path)
            for entry in predecessors:
                self._unindex_line(entry.id)
            self._index_line(successor)
            found = self._find(successor.id)
            assert found is not None
            return self._to_record(successor, found[0])

        survivor = await self.get(survivor_id)
        if survivor is None or survivor.pinned:
            return None
        losers: list[Record] = []
        for lid in loser_ids:
            if lid == survivor_id:
                continue
            loser = await self.get(lid)
            if loser is None:
                continue
            if loser.pinned:
                return None  # never merge a pinned record away
            losers.append(loser)
        # Entity slugs touched by this merge — reconcile them after the losers are
        # superseded so a now-thin entity page folds (and the survivor's page is correct).
        sf = self._find(survivor_id)
        survivor_entity = sf[1].entity if sf else None
        slugs: set[str] = {survivor_entity} if survivor_entity else set()
        inherited_entity: str | None = None
        for loser in losers:
            lf = self._find(loser.id)
            if lf and lf[1].entity:
                slugs.add(lf[1].entity)
                if inherited_entity is None:
                    inherited_entity = self._entity_display(lf[1].entity)
        labels = await self.labels_for([survivor_id, *[lz.id for lz in losers]], include_kind=True)
        metas = sorted({e["label"] for entries in labels.values() for e in entries if e["kind"] == "meta"})
        if text is not None:
            await self.update(survivor_id, text)  # also re-confirms (sets date) + re-indexes
        if kind is not None:
            await self.set_kind(survivor_id, kind)
        # The survivor keeps its own entity; it inherits a loser's only if it had none,
        # so a uniquely-tagged loser isn't silently de-placed by the merge.
        ent_arg = [inherited_entity] if (inherited_entity and not survivor_entity) else None
        if metas or ent_arg:
            await self.set_labels(survivor_id, metas, entity_labels=ent_arg)
        for loser in losers:
            await self.supersede(loser.id, survivor_id)
        for slug in slugs:
            self._reconcile_entity(slug)
        return await self.get(survivor_id)

    async def rename_label(self, old: str, new: str) -> None:
        """Fold the label `old` into `new` (lint canonicalization) across meta labels
        and entity tags, then reconcile the entity pages that changed."""
        old_slug, new_slug = _slug(old), _slug(new)
        if self._ledger_mode():
            for path, page in self._pages.items():
                changed = False
                updated_lines: list[Line | LedgerEntry] = []
                for line in page.lines:
                    if isinstance(line, LedgerEntry) and old_slug in line.entity:
                        entities = tuple(dict.fromkeys(new_slug if entity == old_slug else entity for entity in line.entity))
                        line = replace(line, entity=entities)
                        changed = True
                    updated_lines.append(line)
                labels = page.frontmatter.get("meta_labels") or []
                if old in labels:
                    page.frontmatter["meta_labels"] = sorted({new if label == old else label for label in labels})
                    changed = True
                if changed:
                    page.lines = updated_lines
                    self._persist(path)
            return
        touched_pages: set[Path] = set()
        touched_slugs: set[str] = set()
        for path, page in self._pages.items():
            for key in ("meta_labels", "entity_labels"):
                vals = page.frontmatter.get(key)
                if vals and old in vals:
                    page.frontmatter[key] = sorted({(new if v == old else v) for v in vals})
                    touched_pages.add(path)
            for line in page.lines:
                if line.entity == old_slug:
                    line.entity = new_slug
                    touched_pages.add(path)
                    touched_slugs.update({old_slug, new_slug})
        for path in touched_pages:
            self._persist(path)
        for slug in touched_slugs:
            self._reconcile_entity(slug)

    async def set_label_kind(self, label: str, kind: str) -> int:
        """Retype a label between 'entity' and 'meta'. Only entity->meta is well-defined
        on the file model: untag the entity lines and record `label` as a page meta tag.
        meta->entity is a NO-OP — a page-level meta tag has no per-record membership to
        promote into per-line entity tags, so we leave it as meta rather than DELETE it
        (deleting without retagging would silently drop the label). Returns pages changed."""
        if kind != "meta":
            return 0  # meta->entity: can't faithfully map; leave the label untouched
        n = 0
        slug = _slug(label)
        if self._ledger_mode():
            for path, page in self._pages.items():
                changed = False
                updated_lines: list[Line | LedgerEntry] = []
                for line in page.lines:
                    if isinstance(line, LedgerEntry) and slug in line.entity:
                        line = replace(line, entity=tuple(entity for entity in line.entity if entity != slug))
                        changed = True
                    updated_lines.append(line)
                if changed:
                    page.lines = updated_lines
                    current = page.frontmatter.get("meta_labels") or []
                    page.frontmatter["meta_labels"] = sorted({*current, label})
                    self._persist(path)
                    n += 1
            return n
        for path, page in list(self._pages.items()):
            changed = False
            tagged = [ln for ln in page.lines if ln.entity == slug]
            if tagged:
                for ln in tagged:
                    ln.entity = None
                cur = page.frontmatter.get("meta_labels", [])
                page.frontmatter["meta_labels"] = sorted({*cur, label})
                changed = True
            ents = page.frontmatter.get("entity_labels")
            if ents and label in ents:
                page.frontmatter["entity_labels"] = [e for e in ents if e != label]
                changed = True
            if changed:
                self._persist(path)
                n += 1
        if slug:
            self._reconcile_entity(slug)
        return n

    # -- reads ---------------------------------------------------------------

    async def get(self, record_id: str) -> Record | None:
        found = self._find(record_id)
        if found and isinstance(found[1], LedgerEntry):
            active_ids = {entry.id for entry in self._active_ledger_entries()}
            if record_id not in active_ids:
                return None
        return self._to_record(found[1], found[0]) if found else None

    def _iter_records(self, *, include_superseded: bool):
        active_ids = {entry.id for entry in self._active_ledger_entries()} if self._ledger_mode() else set()
        for path, page in self._pages.items():
            for line in page.lines:
                if isinstance(line, LedgerEntry):
                    if line.meta.operation == "retract":
                        continue
                    if line.id not in active_ids and not include_superseded:
                        continue
                elif line.superseded and not include_superseded:
                    continue
                yield self._to_record(line, path)

    @staticmethod
    def _scope_ok(record: Record, scopes: list[tuple[str | None, str | None]] | None) -> bool:
        if scopes is None:
            return True
        for sk, sv in scopes:
            if sk == "global" and sv is None:
                if (record.scope_kind in (None, "global")) and record.scope_key is None:
                    return True
            elif record.scope_key == sv and (
                record.scope_kind == sk or (sk == "area" and record.scope_kind == "project")
            ):
                return True
        return False

    async def search(
        self,
        query: str,
        *,
        kinds: list[str] | None = None,
        limit: int = 10,
        include_superseded: bool = False,
        scopes: list[tuple[str | None, str | None]] | None = None,
    ) -> list[Record]:
        if scopes == []:
            return []
        q_tokens = _tokens(query)
        q_lower = _norm(query).lower()  # normalized like record text, so a multi-line query still phrase-matches
        window = max(limit * 8, 80)

        # Candidate lines (id -> (line, path)), honoring superseded visibility.
        cand: dict[str, tuple[Line | LedgerEntry, Path]] = {}
        active_ids = {entry.id for entry in self._active_ledger_entries()} if self._ledger_mode() else set()
        for path, page in self._pages.items():
            for line in page.lines:
                if isinstance(line, LedgerEntry):
                    if line.meta.operation == "retract":
                        continue
                    if line.id not in active_ids and not include_superseded:
                        continue
                elif line.superseded and not include_superseded:
                    continue
                cand[line.id] = (line, path)

        # Lexical leg: token overlap + substring bonus. Kept dense (kind/scope
        # filtered AFTER fusion so RRF ranks stay stable).
        lex: list[tuple[str, float]] = []
        for rid, (line, _) in cand.items():
            tl = line.text.lower()
            score = float(len(q_tokens & _tokens(line.text)))
            if q_lower and q_lower in tl:  # query is a phrase IN the record (not the reverse:
                score += 5.0               # a 3-char record must not match every long query)
            if score > 0:
                lex.append((rid, score))
        lex.sort(key=lambda t: t[1], reverse=True)

        # Vector leg (search.db), best-effort; lexical-only on absence/failure.
        vec: list[tuple[str, float]] = []
        index = self._search_index
        if index is not None and q_lower:
            try:
                emb = await index.embedder.embed_one(query)
                raw = await index.store.vector_search(
                    serialize_embedding(emb), sources=[_MEMORY_LINE_SOURCE], limit=window
                )
                for item_id, vscore in raw:
                    item = await index.store.get_by_id(item_id)
                    meta = item.metadata if item and item.metadata else None
                    rid = meta.get("record_id") if meta else None
                    if rid in cand:
                        vec.append((rid, vscore))
            except Exception:
                _logger.warning("memory vector search failed; lexical-only", exc_info=True)

        # RRF-fuse the two legs, then rerank by salience (importance x recency).
        fused = rrf_merge([lex, vec], k=RRF_K)
        scored: list[tuple[float, str, Record]] = []
        for rid, rrf in fused.items():
            line, path = cand[rid]
            rec = self._to_record(line, path)
            if kinds and rec.kind not in kinds:
                continue
            if not self._scope_ok(rec, scopes):
                continue
            # Salience is a SOFT boost (×0.6–1.0), not a hard multiplier: recency/
            # importance break ties and lift fresh records, but can't bury an exact match
            # (a stale durable fact keeps 60% of its relevance rather than 3%).
            line_date = line.meta.recorded_at if isinstance(line, LedgerEntry) else line.date
            final = rrf * (0.6 + 0.4 * salience(line.imp, line_date))
            scored.append((final, rec.last_confirmed_at, rec))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [rec for _, _, rec in scored[:limit]]

    async def list(
        self,
        *,
        pinned_only: bool = False,
        include_superseded: bool = False,
        limit: int | None = 50,
        offset: int = 0,
        scopes: list[tuple[str | None, str | None]] | None = None,
        kinds: list[str] | None = None,
    ) -> list[Record]:
        if scopes == []:
            return []
        out = []
        for rec in self._iter_records(include_superseded=include_superseded):
            if pinned_only and not rec.pinned:
                continue
            if kinds and rec.kind not in kinds:
                continue
            if not self._scope_ok(rec, scopes):
                continue
            out.append(rec)
        out.sort(key=lambda r: r.last_confirmed_at, reverse=True)
        if limit is not None:
            out = out[offset : offset + limit]
        return out

    async def count_active(self) -> int:
        if self._ledger_mode():
            return len(self._active_ledger_entries())
        return sum(len(p.active_lines()) for p in self._pages.values())


if __name__ == "__main__":
    import asyncio
    import tempfile

    async def _demo():
        with tempfile.TemporaryDirectory() as d:
            store = FilePageStore(Path(d))
            await store.open()
            r1 = await store.add("Tim rides a gravel bike daily.", kind="fact", source_ref=SourceRef("user", ""))
            await store.set_labels(r1.id, [], entity_labels=["Bicycles"])
            await store.add("Always greet Tim by name.", kind="directive", source_ref=SourceRef("user", ""))
            await store.add("Tim's wife is Lena.", kind="fact", pinned=True, source_ref=SourceRef("user", ""))

            bike_page = Path(d) / "topics" / "bicycles.md"
            # ONE record for an entity must NOT spawn a topic page; it parks on me.md
            # remembering its entity so a second record can promote it later.
            assert not bike_page.exists(), "single-record entity must not get its own page"

            # reload from disk -> canonical files only
            store2 = FilePageStore(Path(d))
            await store2.open()
            assert await store2.count_active() == 3, await store2.count_active()
            assert not bike_page.exists(), "parked entity survives reload without a page"
            assert any(ln.entity == "bicycles" for p in store2._pages.values() for ln in p.lines), "entity tag persisted"
            # a parked sub-threshold record still surfaces its entity label (curator
            # vocabulary + UI), even though me.md frontmatter stays clean.
            assert await store2.labels_of(r1.id) == ["Bicycles"], await store2.labels_of(r1.id)
            assert any(r["label"] == "Bicycles" and r["count"] == 1 and r["kind"] == "entity" for r in await store2.list_labels())
            dirs = await store2.list(kinds=["directive"], scopes=[("global", None), ("user", None)])
            assert any("greet" in r.text for r in dirs), dirs
            pins = await store2.list(pinned_only=True, scopes=[("global", None), ("user", None)])
            assert any("Lena" in r.text for r in pins), pins
            hits = await store2.search("gravel bike", scopes=[("global", None), ("user", None)])
            assert hits and "gravel" in hits[0].text, hits

            # PROMOTION: a second Bicycles record crosses the threshold -> a real page
            # appears and BOTH records move onto it; the label browser now counts it.
            await store2.add("Tim's bike has 700c wheels.", kind="fact", source_ref=SourceRef("curator", ""), entity_labels=["Bicycles"])
            assert bike_page.exists(), "second record should promote the entity to its own page"
            promoted = FilePageStore(Path(d))
            await promoted.open()
            assert len(promoted._pages[bike_page].active_lines()) == 2, "both records move onto the promoted page"
            assert promoted._pages[bike_page].frontmatter.get("title") == "Bicycles"
            labels = await promoted.list_labels()
            assert any(r["label"] == "Bicycles" and r["count"] == 2 for r in labels), labels

            # supersede_with keeps the successor on the entity page (entity inherited).
            gravel_id = next(l.id for l in promoted._pages[bike_page].active_lines() if "gravel" in l.text)
            await promoted.supersede_with(gravel_id, text="Tim now rides a road bike.", source_ref=SourceRef("curator", ""))
            assert bike_page.exists() and len(promoted._pages[bike_page].active_lines()) == 2, "still 2 active -> stays promoted"

            # DEMOTION: drop active below the threshold -> page folds back, file removed.
            wheels_id = next(l.id for l in promoted._pages[bike_page].active_lines() if "700c" in l.text)
            await promoted.delete(wheels_id)
            await promoted.reconcile_entities()
            assert not bike_page.exists(), "page below threshold folds back to me.md and the dead file is dropped"

            again = FilePageStore(Path(d))
            await again.open()
            active_bike = [r for r in await again.list(scopes=None, limit=None) if "bike" in r.text]
            assert any("road bike" in r.text for r in active_bike)
            assert not any("gravel bike daily" in r.text for r in active_bike), "old line should be superseded"

            # DEMOTION follows ACTIVE RECORD COUNT, not prose: a synthesized dossier that
            # dips below the threshold folds back (its prose is a regenerable projection;
            # the canonical records survive on me.md and re-synthesize there).
            p1 = await again.add("Acme ships widgets.", kind="fact", source_ref=SourceRef("curator", ""), entity_labels=["Acme"])
            p2 = await again.add("Acme is based in Berlin.", kind="fact", source_ref=SourceRef("curator", ""), entity_labels=["Acme"])
            acme = Path(d) / "topics" / "acme.md"
            assert acme.exists() and len(again._pages[acme].active_lines()) == 2
            again._pages[acme].prose = f"Acme is a Berlin widget maker. (record:{p1.id})"  # simulate synthesis
            again._persist(acme)
            await again.delete(p2.id)  # 1 active < threshold -> demote
            await again.reconcile_entities()
            assert not acme.exists(), "sub-threshold entity folds back even with synthesized prose"
            assert any(ln.entity == "acme" for ln in again._pages[Path(d) / _ME].active_lines()), "record parked on me.md, tagged"

            # EMPTY PAGE RECLAIMED: deleting every record of a promoted entity leaves a
            # dead file; the sweep drops it (delete/prune/wipe don't reconcile themselves).
            q1 = await again.add("Zeta fact one.", kind="fact", source_ref=SourceRef("curator", ""), entity_labels=["Zeta"])
            q2 = await again.add("Zeta fact two.", kind="fact", source_ref=SourceRef("curator", ""), entity_labels=["Zeta"])
            zeta = Path(d) / "topics" / "zeta.md"
            assert zeta.exists()
            await again.delete(q1.id)
            await again.delete(q2.id)
            await again.reconcile_entities()
            assert not zeta.exists(), "empty entity page is reclaimed by the sweep"

            # RETIRED observation streams: legacy observations/<source>.md pages are
            # dropped (pages + raw sidecars + records) on open — feeds/ replaced them.
            legacy_obs = Path(d) / "observations" / "gmail.md"
            legacy_obs.parent.mkdir(parents=True, exist_ok=True)
            legacy_obs.write_text(
                "---\ntitle: gmail\n---\n\nOld overview.\n\n<!-- timeline (append-only; edit prose above, not below) -->\n\n"
                "- 2026-06-22 ^0bs01234 [observation] (src:gmail) Old bot mail.\n",
                encoding="utf-8",
            )
            relegacy = FilePageStore(Path(d))
            await relegacy.open()
            assert not legacy_obs.exists(), "observation stream retired on open"
            assert not (Path(d) / "observations").exists(), "empty observations/ folder removed"
            assert await relegacy.get("0bs01234") is None, "observation records dropped"
            again = relegacy

            # LESSON routing: continual-learning playbook records stream to lessons.md, global scope.
            les = await again.add("Verify against the running system before reporting status.", kind="lesson", source_ref=SourceRef("curator", ""))
            assert (Path(d) / "lessons.md").exists(), "lesson routes to lessons.md"
            lr = await again.get(les.id)
            assert lr.kind == "lesson" and lr.scope_kind == "global", (lr.kind, lr.scope_kind)

            # dream insights route to insights/<month>.md (OKF insights/), never me.md/entities
            ins = await again.add("Cross-domain insight.", kind="fact", source_ref=SourceRef("dreamer", "2026-06-23"))
            assert (await again.get(ins.id)).scope_kind == "user"
            assert any(p.parent.name == "insights" for p in again._pages), "dream insight filed under insights/"

            # conventions + self-audit + index are generated on open(); index.md carries
            # one line of meaning per page (navigate by index), overwriting any stale copy.
            assert (Path(d) / "AGENTS.md").exists(), "AGENTS.md conventions written"
            (Path(d) / "index.md").write_text("# stale\n", encoding="utf-8")
            once2 = FilePageStore(Path(d))
            await once2.open()
            idx = (Path(d) / "index.md").read_text(encoding="utf-8")
            assert idx.startswith("# Memory index") and "[[" in idx, idx
            hp = Path(d) / "health.md"
            assert hp.exists() and "# Memory health" in hp.read_text(encoding="utf-8"), "health.md self-audit generated"

            # importance: heuristic scorer fills unscored lines, persists, survives reload
            async def _heur(text, kind, pinned):
                return 8 if pinned else 4

            again.attach_scorer(_heur)
            n = await again.score_pending()
            assert n > 0, n
            assert await again.score_pending() == 0, "idempotent: no unscored lines left"
            once = FilePageStore(Path(d))
            await once.open()
            assert any(ln.imp is not None for p in once._pages.values() for ln in p.lines), "imp persisted"
            assert _norm("plan:\nstep one\n step two") == "plan: step one step two", "newlines collapsed"
            # prune drops tombstones (the superseded bike line); wipe keeps only pinned
            await once.prune()
            assert all(not ln.superseded for p in once._pages.values() for ln in p.lines), "prune cleared tombstones"
            wp = await once.wipe_except_pinned()
            assert wp["kept_pinned"] >= 1 and all(ln.pinned for p in once._pages.values() for ln in p.lines), wp

            # a PROJECT-SCOPED directive routes to the global directives.md by kind —
            # it must NOT stamp the project's title/scope_key onto that page, and the
            # heal pass gives directives.md its canonical title.
            with tempfile.TemporaryDirectory() as d2:
                st = FilePageStore(Path(d2))
                st._project_names = {"proj_abc": "Interaction Lab"}
                await st.open()
                await st.add("Always ask before deploying.", kind="directive",
                             scope_kind="project", scope_key="proj_abc", source_ref=SourceRef("user", ""))
                dpage = st._pages[Path(d2) / _DIRECTIVES]
                assert dpage.frontmatter["title"] == "Directives", dpage.frontmatter
                assert "scope_key" not in dpage.frontmatter, "project scope must not leak onto directives.md"
                # multi-word entity title gets an Obsidian alias so [[Interaction Lab]]
                # resolves to the dash-slug file interaction-lab.md.
                e1 = await st.add("Lab note one.", kind="fact", source_ref=SourceRef("user", ""))
                await st.set_labels(e1.id, [], entity_labels=["Interaction Lab"])
                e2 = await st.add("Lab note two.", kind="fact", source_ref=SourceRef("user", ""))
                await st.set_labels(e2.id, [], entity_labels=["Interaction Lab"])
                lab = st._pages[Path(d2) / "topics" / "interaction-lab.md"]
                assert lab.frontmatter.get("aliases") == ["Interaction Lab"], lab.frontmatter
                reopened = FilePageStore(Path(d2))
                await reopened.open()  # heal is idempotent + repairs any prior contamination
                assert reopened._pages[Path(d2) / _DIRECTIVES].frontmatter["title"] == "Directives"
                assert reopened._pages[Path(d2) / "topics" / "interaction-lab.md"].frontmatter["aliases"] == ["Interaction Lab"]
            print("file_store.py self-check OK")

    async def _demo_vec():
        # Hermetic proof of the vector path (embed -> vector_search -> id-map ->
        # rrf fuse -> salience rank -> filter), with a deterministic mock embedder.
        import numpy as np

        def _v(text: str) -> np.ndarray:
            v = np.zeros(64, dtype=np.float32)
            for tok in re.findall(r"\w+", text.lower()):
                v[hash(tok) % 64] += 1.0
            n = float(np.linalg.norm(v))
            return v / n if n else v

        class _Store:
            def __init__(self):
                self.items: dict[int, tuple] = {}
                self.nid = 0

            async def get_indexed_hashes(self, source):
                return {sid: (rid, str(hash(c))) for rid, (s, sid, _vec, _m, c) in self.items.items() if s == source}

            async def get_by_id(self, rid):
                it = self.items.get(rid)
                return type("I", (), {"metadata": it[3]}) if it else None

            async def vector_search(self, emb_bytes, sources=None, limit=10):
                q = np.frombuffer(emb_bytes, dtype=np.float32)
                out = [(rid, float(np.dot(q, vec))) for rid, (s, _sid, vec, _m, _c) in self.items.items()
                       if not sources or s in sources]
                out.sort(key=lambda t: t[1], reverse=True)
                return out[:limit]

        class _Emb:
            @staticmethod
            async def embed_one(t):
                return _v(t)

        class _Index:
            def __init__(self):
                self.embedder = _Emb()
                self.store = _Store()

            async def upsert(self, source, source_id, title, content, metadata):
                st = self.store
                for rid, (s, sid, *_r) in list(st.items.items()):
                    if s == source and sid == source_id:
                        del st.items[rid]
                st.nid += 1
                st.items[st.nid] = (source, source_id, _v(f"{title}\n{content}"), metadata, content)

            async def delete(self, source, source_id):
                st = self.store
                for rid, (s, sid, *_r) in list(st.items.items()):
                    if s == source and sid == source_id:
                        del st.items[rid]

        with tempfile.TemporaryDirectory() as d:
            store = FilePageStore(Path(d), search_index=_Index())
            await store.open()
            await store.add("Tim commutes on a gravel bicycle every day.", kind="fact", source_ref=SourceRef("user", ""))
            await store.add("The capital of France is Paris.", kind="fact", source_ref=SourceRef("user", ""))
            await store.add("Tim's wife is named Lena.", kind="fact", source_ref=SourceRef("user", ""))
            # query shares tokens with the bicycle line via the mock vector + lexical legs
            await asyncio.sleep(0)  # let fire-and-forget _index upserts run
            hits = await store.search("which bicycle does Tim commute on", limit=2, scopes=None)
            assert hits and "bicycle" in hits[0].text.lower(), hits
            # vectors exist + get cleaned on delete
            assert len(await store._search_index.store.get_indexed_hashes(_MEMORY_LINE_SOURCE)) == 3
            await store.delete(hits[0].id)
            await asyncio.sleep(0)  # let fire-and-forget _unindex run
            assert len(await store._search_index.store.get_indexed_hashes(_MEMORY_LINE_SOURCE)) == 2
            print("file_store.py vector-path self-check OK")

    asyncio.run(_demo())
    asyncio.run(_demo_vec())
