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
from ntrp.memory.pages import SENTINEL, Page, parse_page, parse_raw, render_page, render_raw

_HEADER = "<!-- ntrp:records schema=2 page={page} -->"
_HEADER_RE = re.compile(r"^<!-- ntrp:records schema=(?P<version>\d+)(?: [^>]*)? -->$")
_READABLE_ID_RE = re.compile(r"^- \S+ \^(?P<id>[\w-]+) ")
_VALID_SCOPES = frozenset({"global", "user", "area"})
_INTERNAL_SOURCE_KINDS = frozenset({"record", "memory_record", "derived_record"})
_MIGRATION_META = Path(".ntrp/maintenance/migration-v2.json")


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
    schema_versions: tuple[int, ...] = ()
    duplicate_ids: tuple[str, ...] = ()
    invalid_relationship_targets: tuple[str, ...] = ()
    malformed_metadata: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    invalid_scope: tuple[str, ...] = ()
    timestamp_precision_violations: tuple[str, ...] = ()
    interrupted_journals: tuple[str, ...] = ()

    @property
    def healthy(self) -> bool:
        return self.schema_versions in ((), (2,)) and not any(
            (
                self.duplicate_ids,
                self.invalid_relationship_targets,
                self.malformed_metadata,
                self.missing_evidence,
                self.invalid_scope,
                self.timestamp_precision_violations,
                self.interrupted_journals,
            )
        )

    @property
    def first_error(self) -> str | None:
        if self.schema_versions not in ((), (2,)):
            return f"unsupported schema versions: {list(self.schema_versions)}"
        if self.malformed_metadata:
            return self.malformed_metadata[0]
        if self.duplicate_ids:
            return f"duplicate record id: {self.duplicate_ids[0]}"
        if self.missing_evidence:
            return self.missing_evidence[0]
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
    raw_fm: dict
    existing: list[LedgerEntry]


@dataclass
class _Occurrence:
    page: _LegacyPage
    ordinal: int
    old_id: str
    new_id: str
    scope: tuple[str, str | None]
    entry: LedgerEntry
    identity: str
    is_legacy: bool


def _split_frontmatter(text: str) -> tuple[str, str]:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            return text[: end + 5], text[end + 5 :]
    return "", text


def _read_text(path: Path, root: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise VaultMigrationError(path.relative_to(root), str(exc)) from exc


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
    found: dict[Path, _LegacyPage] = {}
    has_legacy = False
    consumed: set[Path] = set()
    raw_root = root / "raw"
    if raw_root.is_dir():
        for raw_path in sorted(raw_root.rglob("*.md")):
            rel = raw_path.relative_to(raw_root)
            if rel.parts[0] == "events":
                continue
            raw_text = _read_text(raw_path, root)
            _, body = _split_frontmatter(raw_text)
            first = next((line for line in body.splitlines() if line.strip()), "")
            visible_path = root / rel
            visible = _read_text(visible_path, root) if visible_path.exists() else ""
            try:
                page = parse_page(visible)
            except Exception as exc:
                raise VaultMigrationError(rel, f"visible parse: {exc}") from exc
            try:
                raw_fm, parsed = parse_raw(raw_text)
            except Exception as exc:
                raise VaultMigrationError(Path("raw") / rel, f"raw parse: {exc}") from exc
            existing = [item for item in parsed if isinstance(item, LedgerEntry)]
            rows = [] if _HEADER_RE.fullmatch(first) else _legacy_rows(raw_text, Path("raw") / rel)
            if SENTINEL in visible:
                prose, timeline = visible.split(SENTINEL, 1)
                try:
                    page = parse_page(prose)
                except Exception as exc:
                    raise VaultMigrationError(rel, f"visible parse: {exc}") from exc
                rows.extend(_legacy_rows(timeline, rel))
            has_legacy = has_legacy or bool(rows)
            found[rel] = _LegacyPage(rel, visible, page, rows, raw_fm, existing)
            consumed.add(rel)
    for visible_path in sorted(root.rglob("*.md")):
        rel = visible_path.relative_to(root)
        if rel.parts[0] in {"raw", ".ntrp", ".index"} or rel in consumed:
            continue
        visible = _read_text(visible_path, root)
        if SENTINEL not in visible:
            continue
        prose, timeline = visible.split(SENTINEL, 1)
        try:
            page = parse_page(prose)
        except Exception as exc:
            raise VaultMigrationError(rel, f"visible parse: {exc}") from exc
        found[rel] = _LegacyPage(rel, visible, page, _legacy_rows(timeline, rel), {}, [])
        has_legacy = True
    return list(found.values()) if has_legacy else []


def _scope(page: _LegacyPage, kind: Kind) -> tuple[str, str | None]:
    key = page.raw_fm.get("scope_key")
    raw_kind = page.raw_fm.get("scope_kind")
    if key:
        return "area", str(key)
    if raw_kind in {"global", "user"}:
        return str(raw_kind), None
    if kind in (Kind.DIRECTIVE, Kind.LESSON):
        return "global", None
    return "user", None


def _new_id(old_id: str, rel: Path, raw: str) -> str:
    digest = hashlib.sha256(rel.as_posix().encode() + b"\0" + raw.encode()).hexdigest()[:12]
    return f"{old_id}-{digest}"


_EXPLICIT_REF_RE = re.compile(r"^(?P<path>[^#]+)#\^?(?P<id>[\w-]+)$")


def _normal_page_path(raw: str) -> str:
    value = raw.removeprefix("raw/")
    return value.removesuffix(".md") + ".md"


def _resolve_reference(
    raw: str,
    *,
    referring: _Occurrence | None,
    page: _LegacyPage,
    by_old_id: dict[str, list[_Occurrence]],
) -> str:
    explicit = _EXPLICIT_REF_RE.fullmatch(raw)
    old_id = explicit["id"] if explicit else raw.removeprefix("^")
    candidates = by_old_id.get(old_id, [])
    if explicit:
        target_path = _normal_page_path(explicit["path"])
        candidates = [item for item in candidates if item.page.rel.as_posix() == target_path]
        if len(candidates) != 1:
            raise VaultMigrationError(page.rel, f"reference {raw!r} resolves to {len(candidates)} targets")
        return candidates[0].new_id
    if not candidates:
        return raw
    if referring is not None and old_id == referring.old_id:
        return referring.new_id
    same_page = [item for item in candidates if item.page.rel == page.rel]
    if len(same_page) == 1:
        return same_page[0].new_id
    if referring is not None:
        same_scope = [item for item in candidates if item.scope == referring.scope]
        if len(same_scope) == 1:
            return same_scope[0].new_id
    return candidates[0].new_id


def _render_pages(pages: list[_LegacyPage], migration_time: str) -> tuple[dict[Path, bytes], int, int, int]:
    rendered: dict[Path, bytes] = {}
    collapsed = 0
    reassigned = 0
    migrated_count = 0
    sequence = max((e.meta.sequence for page in pages for e in page.existing), default=0)
    reserved = {e.id for page in pages for e in page.existing} | {e.id for page in pages for _, e in page.rows}
    used_ids: set[str] = set(reserved)
    raw_occurrences = []
    for page in sorted(pages, key=lambda item: item.rel.as_posix()):
        raw_occurrences.extend((page, render_ledger_entry(entry), entry, False) for entry in page.existing)
        raw_occurrences.extend((page, raw, entry, True) for raw, entry in page.rows)
    seen: dict[str, str] = {}
    assigned: list[_Occurrence] = []
    for ordinal, (page, identity, entry, is_legacy) in enumerate(raw_occurrences):
        previous = seen.get(entry.id)
        if previous == identity:
            collapsed += 1
            continue
        new_id = entry.id
        if previous is not None:
            new_id = _new_id(entry.id, page.rel, identity)
            while new_id in used_ids:
                new_id += "x"
            used_ids.add(new_id)
            reassigned += 1
        seen.setdefault(entry.id, identity)
        scope = _scope(page, entry.kind) if is_legacy else (entry.meta.scope_kind, entry.meta.scope_key)
        assigned.append(_Occurrence(page, ordinal, entry.id, new_id, scope, entry, identity, is_legacy))
    by_old_id: dict[str, list[_Occurrence]] = {}
    for occurrence in assigned:
        by_old_id.setdefault(occurrence.old_id, []).append(occurrence)
    by_page: dict[Path, list[LedgerEntry]] = {page.rel: [] for page in pages}
    for occurrence in assigned:
        page, entry = occurrence.page, replace(occurrence.entry, id=occurrence.new_id)
        if occurrence.is_legacy:
            migrated_count += 1
            sequence += 1
            scope_kind, scope_key = occurrence.scope
            entry = replace(
                entry,
                meta=replace(
                    entry.meta,
                    recorded_at=migration_time,
                    sequence=sequence,
                    time_precision="day" if entry.occurred_at else "unknown",
                    scope_kind=scope_kind,
                    scope_key=scope_key,
                ),
            )
        sources = []
        for source in entry.meta.sources:
            ref = source.ref
            owned_kind = source.kind.lower().replace("-", "_")
            if owned_kind in _INTERNAL_SOURCE_KINDS:
                ref = _resolve_reference(ref, referring=occurrence, page=page, by_old_id=by_old_id)
            sources.append(replace(source, ref=ref))
        entry = replace(
            entry,
            meta=replace(
                entry.meta,
                sources=tuple(sources),
                supersedes=tuple(
                    _resolve_reference(target, referring=occurrence, page=page, by_old_id=by_old_id)
                    for target in entry.meta.supersedes
                ),
                successor_id=(
                    _resolve_reference(entry.meta.successor_id, referring=occurrence, page=page, by_old_id=by_old_id)
                    if entry.meta.successor_id else None
                ),
            ),
        )
        by_page[page.rel].append(entry)
    for legacy in pages:
        entries = by_page[legacy.rel]
        frontmatter = {**legacy.page.frontmatter, **legacy.raw_fm}
        if "prose_cites" in frontmatter:
            frontmatter["prose_cites"] = [
                _resolve_reference(str(cite), referring=None, page=legacy, by_old_id=by_old_id)
                for cite in frontmatter.get("prose_cites", [])
            ]
        visible_page = Page(frontmatter=frontmatter, prose=legacy.page.prose)
        rendered[legacy.rel] = render_page(visible_page).encode()
        visible_page.lines = entries
        visible_page.records_header = _HEADER.format(page=legacy.rel.as_posix())
        rendered[Path("raw") / legacy.rel] = render_raw(visible_page).encode()
    return rendered, migrated_count, collapsed, reassigned


def _copy_backup(root: Path, backup: Path) -> None:
    backup.mkdir(parents=True, exist_ok=True)
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
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            except OSError as exc:
                raise VaultMigrationError(rel, f"backup copy: {exc}") from exc


def _migration_meta(root: Path) -> dict[str, str]:
    path = root / _MIGRATION_META
    if not path.exists():
        return {}
    try:
        data = json.loads(_read_text(path, root))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise VaultMigrationError(_MIGRATION_META, f"invalid migration status JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise VaultMigrationError(_MIGRATION_META, "migration status must be a JSON object")
    return {key: str(value) for key, value in data.items() if value is not None}


def _migration_meta_bytes(*, migrated_at: str, backup_path: Path | None) -> bytes:
    data: dict[str, object] = {"schema_version": 2, "last_migration": migrated_at}
    if backup_path is not None:
        data["backup_path"] = str(backup_path)
    return (json.dumps(data, sort_keys=True) + "\n").encode()


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


def _migrate_vault_to_v2(root: Path) -> MigrationReport:
    # VaultJournal canonicalizes its root. Derive every migration-internal path
    # from the same canonical path so aliases such as macOS /tmp -> /private/tmp
    # cannot fall outside the journal's containment checks.
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    journal = VaultJournal(root)
    maintenance = root / ".ntrp" / "maintenance" / "migration-v2"
    journal._assert_safe_parents(maintenance / "run")
    if maintenance.exists():
        if maintenance.is_symlink():
            raise VaultMigrationError(Path(".ntrp/maintenance/migration-v2"), "staging root is a symlink")
        shutil.rmtree(maintenance)
    pages = _discover_legacy(root)
    if not pages:
        meta = _migration_meta(root)
        if meta.get("schema_version") != "2":
            migrated_at = datetime.now(UTC).isoformat()
            journal.commit_migration(
                {_MIGRATION_META: _migration_meta_bytes(migrated_at=migrated_at, backup_path=None)}
            )
        return MigrationReport(False, meta.get("backup_path"))
    migrated_at = datetime.now(UTC).isoformat()
    files, record_count, collapsed, reassigned = _render_pages(pages, migrated_at)
    run_id = uuid4().hex
    stage = maintenance / run_id
    _stage_current_vault(root, stage)
    for rel, content in files.items():
        target = stage / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        except OSError as exc:
            raise VaultMigrationError(rel, f"stage write: {exc}") from exc
    staged_health = validate_vault(stage)
    if not staged_health.healthy:
        raise VaultMigrationError(Path(".ntrp/maintenance/migration-v2") / run_id, staged_health.first_error or "validation failed")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = root / ".ntrp" / "backups" / stamp
    journal._mkdir_durable(backup)
    _copy_backup(root, backup)
    files[_MIGRATION_META] = _migration_meta_bytes(migrated_at=migrated_at, backup_path=backup)
    try:
        journal.commit_migration(files)
    except Exception as exc:
        try:
            VaultJournal(root).recover(prefer_rollback=True)
        except Exception as recovery_exc:
            raise VaultMigrationError(Path(".ntrp/journal"), f"journal commit: {exc}; rollback: {recovery_exc}") from exc
        raise VaultMigrationError(Path(".ntrp/journal"), f"journal commit: {exc}") from exc
    shutil.rmtree(maintenance, ignore_errors=True)
    return MigrationReport(True, str(backup), len(pages), record_count, collapsed, reassigned)


def migrate_vault_to_v2(root: Path) -> MigrationReport:
    root = Path(root).resolve()
    try:
        return _migrate_vault_to_v2(root)
    except VaultMigrationError:
        raise
    except Exception as exc:
        filename = getattr(exc, "filename", None)
        path = Path(filename) if filename else root
        try:
            path = path.relative_to(root)
        except ValueError:
            path = Path(".")
        raise VaultMigrationError(path, str(exc)) from exc


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


def _precision_error(value: str | None, precision: str) -> str | None:
    if precision == "unknown":
        return None if value is None else "unknown precision requires absent occurred_at"
    if value is None:
        return "absent occurred_at requires unknown precision"
    if precision == "day":
        return None if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else "day precision requires a date-only occurred_at"
    timestamp = re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:(?P<seconds>\d{2})(?P<fraction>\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
        value,
    )
    if timestamp is None:
        return f"{precision} precision requires an RFC 3339 timestamp"
    fraction = timestamp["fraction"]
    if precision == "minute" and (timestamp["seconds"] != "00" or fraction):
        return "minute precision requires zero seconds and no fraction"
    if precision == "second" and fraction:
        return "second precision forbids fractional seconds"
    if precision == "millisecond" and (fraction is None or len(fraction) != 4):
        return "millisecond precision requires exactly three fractional digits"
    return None


def validate_vault(root: Path) -> VaultHealth:
    root = Path(root)
    malformed: list[str] = []
    entries: list[tuple[str, LedgerEntry]] = []
    versions: set[int] = set()
    raw_root = root / "raw"
    if raw_root.is_symlink():
        malformed.append("raw: vault raw root is a symlink")
        raw_root = root / "__invalid_raw__"
    for internal in (root / ".ntrp", root / ".ntrp/backups", root / ".ntrp/maintenance"):
        if internal.is_symlink():
            malformed.append(f"{internal.relative_to(root).as_posix()}: internal root is a symlink")
    if raw_root.is_dir():
        for path in sorted(raw_root.rglob("*.md")):
            if path.relative_to(raw_root).parts[0] == "events":
                continue
            try:
                _, body = _split_frontmatter(_read_text(path, root))
            except VaultMigrationError as exc:
                malformed.append(str(exc))
                continue
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
        elif entry.meta.scope_kind == "area" and not entry.meta.scope_key:
            invalid_scope.append(f"{rel}: {entry.id}: area requires a scope key")
        elif entry.meta.scope_kind in {"user", "global"} and entry.meta.scope_key is not None:
            invalid_scope.append(f"{rel}: {entry.id}: {entry.meta.scope_kind} forbids a scope key")
        if error := _precision_error(entry.occurred_at, entry.meta.time_precision):
            precision.append(f"{rel}: {entry.id}: {error}")
        for index, source in enumerate(entry.meta.sources):
            source_label = f"{rel}: {entry.id}: source {index}"
            if error := _precision_error(source.occurred_at, source.time_precision):
                precision.append(f"{source_label}: {error}")
    interrupted: list[str] = []
    for rel in (Path(".ntrp/journal"), Path(".ntrp/maintenance/migration-v2")):
        path = root / rel
        if path.is_dir():
            interrupted.extend((rel / child.name).as_posix() for child in sorted(path.iterdir()))
    try:
        meta = _migration_meta(root)
    except VaultMigrationError as exc:
        malformed.append(str(exc))
        meta = {}
    schema_version = next(iter(versions)) if len(versions) == 1 else (None if not versions else min(versions))
    return VaultHealth(
        schema_version=schema_version,
        last_migration=meta.get("last_migration"),
        backup_path=meta.get("backup_path"),
        schema_versions=tuple(sorted(versions)),
        duplicate_ids=duplicate_ids,
        invalid_relationship_targets=tuple(sorted(relationships)),
        malformed_metadata=tuple(sorted(malformed)),
        missing_evidence=tuple(sorted(missing_evidence)),
        invalid_scope=tuple(sorted(invalid_scope)),
        timestamp_precision_violations=tuple(sorted(precision)),
        interrupted_journals=tuple(interrupted),
    )
