"""Deterministic root and folder README navigation over one wiki snapshot."""

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from ..models import GeneratedPageTarget, WikiPageRecord, WikiSnapshot
from ..service import WikiService
from .store import WikiNavigationStore

WIKI_NAVIGATION_ACTOR = "Wiki Navigation"
WIKI_NAVIGATION_ORIGIN = "wiki.navigation"
WIKI_NAVIGATION_REASON = "project deterministic README navigation"
_ROOT_TITLE = "Home"


class WikiNavigationError(RuntimeError):
    """A deterministic README projection cannot safely converge."""


@dataclass(frozen=True, slots=True)
class WikiNavigationResult:
    observed_head: str | None
    checkpoint: str | None
    published: bool
    advanced: bool
    readme_count: int


@dataclass(frozen=True, slots=True)
class _ReadmeTarget:
    directory: str
    page_id: str
    path: str
    title: str
    aliases: tuple[str, ...]
    metadata: Mapping[str, object]


class WikiNavigationProjection:
    """Projects one canonical README per managed active Markdown directory."""

    def __init__(self, wiki: WikiService, store: WikiNavigationStore) -> None:
        self._wiki = wiki
        self._store = store

    async def run(self) -> WikiNavigationResult:
        watermark = await self._store.get()
        expected = None if watermark is None else watermark.revision
        snapshot = await asyncio.to_thread(self._wiki.snapshot)
        if watermark is not None and snapshot.head == expected:
            return WikiNavigationResult(snapshot.head, snapshot.head, False, False, 0)
        await asyncio.to_thread(self._validate_watermark, snapshot, expected)
        targets = await asyncio.to_thread(self._targets, snapshot)
        source_revision = _source_revision(snapshot)
        commit = await asyncio.to_thread(
            self._wiki.publish_generated,
            targets,
            source_revision=source_revision,
            base_head=snapshot.head,
            actor=WIKI_NAVIGATION_ACTOR,
            origin=WIKI_NAVIGATION_ORIGIN,
            reason=WIKI_NAVIGATION_REASON,
        )
        checkpoint = snapshot.head if commit is None else commit.commit_id
        if checkpoint is None:
            raise WikiNavigationError("README projection did not publish the required root README")
        await self._store.advance(expected_revision=expected, revision=checkpoint)
        return WikiNavigationResult(snapshot.head, checkpoint, commit is not None, True, len(targets))

    def _validate_watermark(self, snapshot: WikiSnapshot, watermark: str | None) -> None:
        if watermark is None:
            return
        if snapshot.head is None:
            raise WikiNavigationError("wiki navigation watermark is not reachable from an empty wiki")
        try:
            self._wiki.repository.history(start=snapshot.head, stop_before=watermark)
        except KeyError as exc:
            raise WikiNavigationError("wiki navigation watermark is not reachable from the pinned wiki head") from exc

    def _targets(self, snapshot: WikiSnapshot) -> tuple[GeneratedPageTarget, ...]:
        records = tuple(
            record
            for record in snapshot.pages
            if record.page.lifecycle == "active" and record.resource.path.endswith(".md")
        )
        directories = _directories(records)
        existing = {record.resource.path: record for record in snapshot.pages if _is_readme(record.resource.path)}
        targets: list[_ReadmeTarget] = []
        target_directories = directories | {_readme_directory(path) for path in existing}
        for directory in target_directories:
            path = _readme_path(directory)
            record = existing.get(path)
            if record is not None:
                if record.page.lifecycle != "active":
                    raise WikiNavigationError(f"README is not active: {path}")
                if "fact_citations" in record.page.metadata:
                    raise WikiNavigationError(f"README cannot be fact-backed: {path}")
                metadata = {
                    key: value for key, value in record.page.metadata.items() if key != "generated_from_revision"
                }
                targets.append(
                    _ReadmeTarget(
                        directory,
                        record.page.page_id,
                        path,
                        record.page.title,
                        record.page.aliases,
                        metadata,
                    )
                )
            else:
                targets.append(_ReadmeTarget(directory, _readme_id(directory), path, _readme_title(directory), (), {}))

        generated: list[GeneratedPageTarget] = []
        by_directory = {target.directory: target for target in targets if target.directory in directories}
        for target in targets:
            generated.append(
                GeneratedPageTarget(
                    page_id=target.page_id,
                    path=target.path,
                    title=target.title,
                    aliases=target.aliases,
                    generated=_render_directory(target.directory, records, by_directory),
                    metadata=target.metadata,
                )
            )
        return tuple(sorted(generated, key=lambda target: target.path))


def _directories(records: tuple[WikiPageRecord, ...]) -> set[str]:
    directories = {""}
    for record in records:
        if _is_readme(record.resource.path):
            continue
        parent = PurePosixPath(record.resource.path).parent
        while str(parent) not in {"", "."}:
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _is_readme(path: str) -> bool:
    return path == "README.md" or path.endswith("/README.md")


def _readme_path(directory: str) -> str:
    return "README.md" if not directory else f"{directory}/README.md"


def _readme_directory(path: str) -> str:
    return "" if path == "README.md" else str(PurePosixPath(path).parent)


def _readme_id(directory: str) -> str:
    digest = hashlib.sha256(f"wiki.navigation.readme\0{directory}".encode()).hexdigest()
    return f"readme-{digest[:16]}"


def _readme_title(directory: str) -> str:
    return _ROOT_TITLE if not directory else f"Folder: {directory}"


def _render_directory(
    directory: str,
    records: tuple[WikiPageRecord, ...],
    targets: dict[str, _ReadmeTarget],
) -> bytes:
    lines = ["## Navigation", ""]
    children = sorted(
        (target for child, target in targets.items() if child != directory and _parent(child) == directory),
        key=lambda target: target.path,
    )
    pages = sorted(
        (
            record
            for record in records
            if _parent(record.resource.path) == directory and not _is_readme(record.resource.path)
        ),
        key=lambda record: record.resource.path,
    )
    for child in children:
        lines.append(f"- {_wikilink(_link_target(child.path), child.directory)}")
    for record in pages:
        lines.append(f"- {_wikilink(_link_target(record.resource.path), record.page.title)}")
    if not children and not pages:
        lines.append("- _No managed pages._")
    return ("\n".join(lines) + "\n").encode()


def _parent(path: str) -> str:
    parent = PurePosixPath(path).parent
    return "" if str(parent) == "." else parent.as_posix()


def _link_target(path: str) -> str:
    if not path.endswith(".md"):
        raise WikiNavigationError(f"navigation target is not Markdown: {path}")
    return path[:-3]


def _wikilink(target: str, label: str) -> str:
    if any(character in "[]|#\\" or ord(character) < 32 for character in target):
        raise WikiNavigationError(f"navigation target cannot be represented as a wikilink: {target!r}")
    if any(character in "[]\\" or ord(character) < 32 for character in label):
        raise WikiNavigationError(f"navigation label cannot be represented as a wikilink: {label!r}")
    return f"[[{target}|{label}]]"


def _source_revision(snapshot: WikiSnapshot) -> str:
    sources = [
        (record.resource.path, record.page.page_id, record.page.title)
        for record in snapshot.pages
        if record.page.lifecycle == "active"
        and record.resource.path.endswith(".md")
        and not _is_readme(record.resource.path)
    ]
    encoded = json.dumps(sorted(sources), ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(b"wiki.navigation.v1\0" + encoded).hexdigest()
