"""Fail-closed migration and validation for schema-v2 memory vaults."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ntrp.memory.journal import VaultJournal
from ntrp.memory.ledger import LedgerEntry, parse_ledger_entry, render_ledger_entry
from ntrp.memory.models import Kind
from ntrp.memory.pages import SENTINEL, Page, parse_page, render_page

_HEADER = "<!-- ntrp:records schema=2 page={page} -->"
_HEADER_RE = re.compile(r"^<!-- ntrp:records schema=(?P<version>\d+)(?: [^>]*)? -->$")
_READABLE_ID_RE = re.compile(r"^- \S+ \^(?P<id>[\w-]+) ")
_VALID_SCOPES = frozenset({"global", "user", "area", "project", "entity"})
_MIGRATION_META = Path(".ntrp/migration-v2.json")


@dataclass(frozen=True)
class MigrationReport:
    migrated: bool
    backup_path: str | None
    migrated_pages: int = 0
    migrated_records: int = 0
    collapsed_duplicates: int = 0
    reassigned_duplicates: int = 0


@dataclass(frozen=True)
class VaultHealth:
    schema_version: int | None
    last_migration: str | None
    backup_path: str | None
    duplicate_ids: tuple[str, ...] = ()
    invalid_relationship_targets: tuple[str, ...] = ()
    malformed_metadata: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    invalid_scope: tuple[str, ...] = ()
    timestamp_precision_violations: tuple[str, ...] = ()
    interrupted_journals: tuple[str, ...] = ()

    @property
    def healthy(self) -> bool:
        return self.schema_version != 1 and not any(
            (
                self.duplicate_ids,
                self.invalid_relationship_targets,
                self.malformed_metadata,
                self.invalid_scope,
                self.timestamp_precision_violations,
                self.interrupted_journals,
            )
        )

    @property
    def first_error(self) -> str | None:
        if self.malformed_metadata:
            return self.malformed_metadata[0]
        if self.duplicate_ids:
            return f"duplicate record id: {self.duplicate_ids[0]}"
        groups = (
            self.invalid_relationship_targets,
            self.invalid_scope,
            self.timestamp_precision_violations,
            self.interrupted_journals,
        )
        return next((str(item) for group in groups for item in group), None)


class VaultMigrationError(RuntimeError):
    def __init__(self, path: Path, reason: str):
        self.path = Path(path)
        self.reason = reason
        super().__init__(f"{self.path.as_posix()}: {reason}")


@dataclass
class _LegacyPage:
    rel: Path
    visible: str
    page: Page
    rows: list[tuple[str, LedgerEntry]]


def _split_frontmatter(text: str) -> tuple[str, str]:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            return text[: end + 5], text[end + 5 :]
    return "", text


def _legacy_rows(text: str, rel: Path) -> list[tuple[str, LedgerEntry]]:
    _, body = _split_frontmatter(text)
    rows: list[tuple[str, LedgerEntry]] = []
    for number, raw in enumerate(body.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            rows.append((raw, parse_ledger_entry(raw)))
        except ValueError as exc:
            if raw.startswith("- unknown "):
                try:
                    placeholder = parse_ledger_entry(raw.replace("- unknown ", "- 1970-01-01 ", 1))
                except ValueError:
                    pass
                else:
                    sources = tuple(
                        replace(source, occurred_at=None, time_precision="unknown")
                        for source in placeholder.meta.sources
                    )
                    rows.append(
                        (
                            raw,
                            replace(
                                placeholder,
                                occurred_at=None,
                                meta=replace(placeholder.meta, time_precision="unknown", sources=sources),
                            ),
                        )
                    )
                    continue
            raise VaultMigrationError(rel, f"line {number}: {exc}") from exc
    return rows


def _discover_legacy(root: Path) -> list[_LegacyPage]:
    found: list[_LegacyPage] = []
    consumed: set[Path] = set()
    raw_root = root / "raw"
    if raw_root.is_dir():
        for raw_path in sorted(raw_root.rglob("*.md")):
            rel = raw_path.relative_to(raw_root)
            raw_text = raw_path.read_text(encoding="utf-8")
            _, body = _split_frontmatter(raw_text)
            first = next((line for line in body.splitlines() if line.strip()), "")
            if _HEADER_RE.fullmatch(first):
                continue
            visible_path = root / rel
            visible = visible_path.read_text(encoding="utf-8") if visible_path.exists() else ""
            page = parse_page(visible)
            found.append(_LegacyPage(rel, visible, page, _legacy_rows(raw_text, Path("raw") / rel)))
            consumed.add(rel)
    for visible_path in sorted(root.rglob("*.md")):
        rel = visible_path.relative_to(root)
        if rel.parts[0] in {"raw", ".ntrp", ".index"} or rel in consumed:
            continue
        visible = visible_path.read_text(encoding="utf-8")
        if SENTINEL not in visible:
            continue
        prose, timeline = visible.split(SENTINEL, 1)
        page = parse_page(prose)
        found.append(_LegacyPage(rel, visible, page, _legacy_rows(timeline, rel)))
    return found


def _scope(page: _LegacyPage, kind: Kind) -> tuple[str, str | None]:
    key = page.page.frontmatter.get("scope_key")
    if key:
        return "area", str(key)
    if kind in (Kind.DIRECTIVE, Kind.LESSON):
        return "global", None
    return "user", None


def _new_id(old_id: str, rel: Path, raw: str) -> str:
    digest = hashlib.sha256(rel.as_posix().encode() + b"\0" + raw.encode()).hexdigest()[:12]
    return f"{old_id}-{digest}"


def _render_pages(pages: list[_LegacyPage]) -> tuple[dict[Path, bytes], int, int, int]:
    seen: dict[str, str] = {}
    rendered: dict[Path, bytes] = {}
    collapsed = 0
    reassigned = 0
    sequence = 0
    used_ids: set[str] = set()
    for legacy in pages:
        entries: list[LedgerEntry] = []
        local_remap: dict[str, str] = {}
        for raw, parsed in legacy.rows:
            previous = seen.get(parsed.id)
            if previous == raw:
                collapsed += 1
                continue
            record_id = parsed.id
            if previous is not None:
                record_id = _new_id(record_id, legacy.rel, raw)
                while record_id in used_ids:
                    record_id += "x"
                local_remap[parsed.id] = record_id
                reassigned += 1
            seen.setdefault(parsed.id, raw)
            used_ids.add(record_id)
            sequence += 1
            scope_kind, scope_key = _scope(legacy, parsed.kind)
            precision = "day" if parsed.occurred_at else "unknown"
            recorded_at = f"{parsed.occurred_at}T00:00:00Z" if parsed.occurred_at else datetime.now(UTC).isoformat()
            entries.append(
                replace(
                    parsed,
                    id=record_id,
                    meta=replace(
                        parsed.meta,
                        recorded_at=recorded_at,
                        sequence=sequence,
                        time_precision=precision,
                        scope_kind=scope_kind,
                        scope_key=scope_key,
                    ),
                )
            )
        if local_remap:
            entries = [
                replace(
                    entry,
                    meta=replace(
                        entry.meta,
                        supersedes=tuple(local_remap.get(target, target) for target in entry.meta.supersedes),
                        successor_id=local_remap.get(entry.meta.successor_id, entry.meta.successor_id),
                    ),
                )
                for entry in entries
            ]
        visible_page = Page(frontmatter=legacy.page.frontmatter, prose=legacy.page.prose)
        rendered[legacy.rel] = render_page(visible_page).encode()
        body = "\n".join(render_ledger_entry(entry) for entry in entries)
        raw = _HEADER.format(page=legacy.rel.as_posix()) + "\n"
        if body:
            raw += body + "\n"
        rendered[Path("raw") / legacy.rel] = raw.encode()
    return rendered, sequence, collapsed, reassigned


def _copy_backup(root: Path, backup: Path) -> None:
    backup.mkdir(parents=True)
    for source in sorted(root.rglob("*")):
        rel = source.relative_to(root)
        if rel.parts[:2] == (".ntrp", "backups") or rel.parts[:2] == (".ntrp", "maintenance"):
            continue
        if source.is_symlink():
            raise VaultMigrationError(rel, "backup source is a symlink")
        if source.is_dir():
            (backup / rel).mkdir(parents=True, exist_ok=True)
        elif source.is_file():
            target = backup / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _migration_meta(root: Path) -> dict[str, str]:
    path = root / _MIGRATION_META
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {key: str(value) for key, value in data.items() if value is not None}


def _write_migration_meta(root: Path, *, migrated_at: str, backup_path: Path) -> None:
    path = root / _MIGRATION_META
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps({"schema_version": 2, "last_migration": migrated_at, "backup_path": str(backup_path)}, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def _stage_current_vault(root: Path, stage: Path) -> None:
    """Copy canonical markdown so validation sees migrated and unchanged pages together."""
    for source in sorted(root.rglob("*.md")):
        rel = source.relative_to(root)
        if rel.parts[0] in {".ntrp", ".index"}:
            continue
        if source.is_symlink():
            raise VaultMigrationError(rel, "staging source is a symlink")
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def migrate_vault_to_v2(root: Path) -> MigrationReport:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    maintenance = root / ".ntrp" / "maintenance" / "migration-v2"
    if maintenance.exists():
        if maintenance.is_symlink():
            raise VaultMigrationError(Path(".ntrp/maintenance/migration-v2"), "staging root is a symlink")
        shutil.rmtree(maintenance)
    pages = _discover_legacy(root)
    if not pages:
        meta = _migration_meta(root)
        return MigrationReport(False, meta.get("backup_path"))
    files, record_count, collapsed, reassigned = _render_pages(pages)
    run_id = uuid4().hex
    stage = maintenance / run_id
    _stage_current_vault(root, stage)
    for rel, content in files.items():
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    staged_health = validate_vault(stage)
    if not staged_health.healthy:
        raise VaultMigrationError(Path(".ntrp/maintenance/migration-v2") / run_id, staged_health.first_error or "validation failed")
    migrated_at = datetime.now(UTC).isoformat()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = root / ".ntrp" / "backups" / stamp
    _copy_backup(root, backup)
    try:
        VaultJournal(root).commit(files)
    except Exception as exc:
        try:
            VaultJournal(root).recover(prefer_rollback=True)
        except Exception as recovery_exc:
            raise VaultMigrationError(Path(".ntrp/journal"), f"journal commit: {exc}; rollback: {recovery_exc}") from exc
        raise VaultMigrationError(Path(".ntrp/journal"), f"journal commit: {exc}") from exc
    _write_migration_meta(root, migrated_at=migrated_at, backup_path=backup)
    shutil.rmtree(maintenance, ignore_errors=True)
    return MigrationReport(True, str(backup), len(pages), record_count, collapsed, reassigned)


def _v2_entries(path: Path, root: Path, malformed: list[str]) -> list[LedgerEntry]:
    rel = path.relative_to(root).as_posix()
    _, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    rows = body.splitlines()
    first = next((index for index, row in enumerate(rows) if row.strip()), None)
    if first is None or not _HEADER_RE.fullmatch(rows[first]):
        return []
    entries: list[LedgerEntry] = []
    index = first + 1
    while index < len(rows):
        if not rows[index].strip():
            index += 1
            continue
        readable = rows[index]
        record_match = _READABLE_ID_RE.match(readable)
        record_id = record_match["id"] if record_match else f"line {index + 1}"
        if index + 1 >= len(rows) or not rows[index + 1].lstrip().startswith("<!-- ntrp:meta "):
            malformed.append(f"{rel}: record {record_id}: schema-v2 record is missing its metadata comment")
            index += 1
            continue
        try:
            entries.append(parse_ledger_entry(f"{readable}\n{rows[index + 1]}"))
        except ValueError as exc:
            malformed.append(f"{rel}: record {record_id}: {exc}")
        index += 2
    return entries


def validate_vault(root: Path) -> VaultHealth:
    root = Path(root)
    malformed: list[str] = []
    entries: list[tuple[str, LedgerEntry]] = []
    versions: set[int] = set()
    raw_root = root / "raw"
    if raw_root.is_dir():
        for path in sorted(raw_root.rglob("*.md")):
            _, body = _split_frontmatter(path.read_text(encoding="utf-8"))
            first = next((line for line in body.splitlines() if line.strip()), "")
            header = _HEADER_RE.fullmatch(first)
            if header:
                versions.add(int(header["version"]))
                entries.extend((path.relative_to(root).as_posix(), entry) for entry in _v2_entries(path, root, malformed))
            elif first:
                versions.add(1)
    ids: dict[str, list[str]] = {}
    for rel, entry in entries:
        ids.setdefault(entry.id, []).append(rel)
    duplicate_ids = tuple(sorted(record_id for record_id, rels in ids.items() if len(rels) > 1))
    all_ids = set(ids)
    relationships: list[str] = []
    missing_evidence: list[str] = []
    invalid_scope: list[str] = []
    precision: list[str] = []
    for rel, entry in entries:
        for target in (*entry.meta.supersedes, *((entry.meta.successor_id,) if entry.meta.successor_id else ())):
            if target not in all_ids:
                relationships.append(f"{rel}: {entry.id} -> {target}")
        if not entry.meta.sources:
            missing_evidence.append(f"{rel}: {entry.id}")
        if entry.meta.scope_kind not in _VALID_SCOPES:
            invalid_scope.append(f"{rel}: {entry.id}: {entry.meta.scope_kind}")
        if entry.occurred_at is None and entry.meta.time_precision != "unknown":
            precision.append(f"{rel}: {entry.id}: absent occurred_at requires unknown precision")
        elif entry.occurred_at is not None:
            date_only = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry.occurred_at))
            if entry.meta.time_precision == "day" and not date_only:
                precision.append(f"{rel}: {entry.id}: day precision requires a date-only occurred_at")
            elif date_only and entry.meta.time_precision != "day":
                precision.append(f"{rel}: {entry.id}: date-only occurred_at requires day precision")
    interrupted: list[str] = []
    for rel in (Path(".ntrp/journal"), Path(".ntrp/maintenance/migration-v2")):
        path = root / rel
        if path.is_dir():
            interrupted.extend((rel / child.name).as_posix() for child in sorted(path.iterdir()))
    meta = _migration_meta(root)
    schema_version = next(iter(versions)) if len(versions) == 1 else (None if not versions else min(versions))
    return VaultHealth(
        schema_version=schema_version,
        last_migration=meta.get("last_migration"),
        backup_path=meta.get("backup_path"),
        duplicate_ids=duplicate_ids,
        invalid_relationship_targets=tuple(sorted(relationships)),
        malformed_metadata=tuple(sorted(malformed)),
        missing_evidence=tuple(sorted(missing_evidence)),
        invalid_scope=tuple(sorted(invalid_scope)),
        timestamp_precision_violations=tuple(sorted(precision)),
        interrupted_journals=tuple(interrupted),
    )
