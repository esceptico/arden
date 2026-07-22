"""Initialization and validation for schema-v2 memory vaults."""

from __future__ import annotations

import re
import stat
from dataclasses import dataclass
from pathlib import Path

from ntrp.memory.ledger import LedgerEntry, parse_ledger_entry

_HEADER_RE = re.compile(r"^<!-- ntrp:records schema=(?P<version>\d+)(?: [^>]*)? -->$")
_READABLE_ID_RE = re.compile(r"^- \S+ \^(?P<id>[\w-]+) ")
_VALID_SCOPES = frozenset({"global", "user", "area"})


@dataclass(frozen=True)
class VaultHealth:
    schema_version: int | None
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
        return next((item for group in groups for item in group), None)


def initialize_empty_vault(root: Path) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "raw").mkdir(exist_ok=True)
    (root / ".ntrp").mkdir(exist_ok=True)


def _body(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            return text[end + 5 :]
    return text


def _entries(path: Path, root: Path, malformed: list[str]) -> list[LedgerEntry]:
    rel = path.relative_to(root).as_posix()
    rows = _body(path.read_text(encoding="utf-8")).splitlines()
    first = next((index for index, row in enumerate(rows) if row.strip()), None)
    if first is None or _HEADER_RE.fullmatch(rows[first]) is None:
        return []
    entries: list[LedgerEntry] = []
    index = first + 1
    while index < len(rows):
        if not rows[index].strip():
            index += 1
            continue
        match = _READABLE_ID_RE.match(rows[index])
        record_id = match["id"] if match else f"line {index + 1}"
        if index + 1 >= len(rows) or not rows[index + 1].lstrip().startswith("<!-- ntrp:meta "):
            malformed.append(f"{rel}: record {record_id}: schema-v2 record is missing its metadata comment")
            index += 1
            continue
        try:
            entries.append(parse_ledger_entry(f"{rows[index]}\n{rows[index + 1]}"))
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
    located: list[tuple[str, LedgerEntry]] = []
    versions: set[int] = set()
    raw_root = root / "raw"
    if raw_root.is_symlink():
        malformed.append("raw: vault raw root is a symlink")
    elif raw_root.is_dir():
        for path in sorted(raw_root.rglob("*.md")):
            if path.relative_to(raw_root).parts[0] == "events":
                continue
            try:
                if stat.S_ISLNK(path.lstat().st_mode):
                    malformed.append(f"{path.relative_to(root).as_posix()}: file is a symlink")
                    continue
                body = _body(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError) as exc:
                malformed.append(f"{path.relative_to(root).as_posix()}: {exc}")
                continue
            first = next((line for line in body.splitlines() if line.strip()), "")
            header = _HEADER_RE.fullmatch(first)
            if header is None:
                if first:
                    versions.add(1)
                continue
            versions.add(int(header["version"]))
            located.extend((path.relative_to(root).as_posix(), entry) for entry in _entries(path, root, malformed))
    ids: dict[str, list[str]] = {}
    for rel, entry in located:
        ids.setdefault(entry.id, []).append(rel)
    all_ids = set(ids)
    relationships: list[str] = []
    evidence: list[str] = []
    scopes: list[str] = []
    precision: list[str] = []
    for rel, entry in located:
        for target in (*entry.meta.supersedes, *((entry.meta.successor_id,) if entry.meta.successor_id else ())):
            if target not in all_ids:
                relationships.append(f"{rel}: {entry.id} -> {target}")
        if not entry.meta.sources:
            evidence.append(f"{rel}: {entry.id}")
        if entry.meta.scope_kind not in _VALID_SCOPES:
            scopes.append(f"{rel}: {entry.id}: {entry.meta.scope_kind}")
        elif entry.meta.scope_kind == "area" and not entry.meta.scope_key:
            scopes.append(f"{rel}: {entry.id}: area requires a scope key")
        elif entry.meta.scope_kind in {"user", "global"} and entry.meta.scope_key is not None:
            scopes.append(f"{rel}: {entry.id}: {entry.meta.scope_kind} forbids a scope key")
        if error := _precision_error(entry.occurred_at, entry.meta.time_precision):
            precision.append(f"{rel}: {entry.id}: {error}")
        for index, source in enumerate(entry.meta.sources):
            if error := _precision_error(source.occurred_at, source.time_precision):
                precision.append(f"{rel}: {entry.id}: source {index}: {error}")
    journal = root / ".ntrp/journal"
    interrupted = tuple(
        (Path(".ntrp/journal") / child.name).as_posix()
        for child in sorted(journal.iterdir())
    ) if journal.is_dir() else ()
    schema_version = next(iter(versions)) if len(versions) == 1 else (min(versions) if versions else None)
    return VaultHealth(
        schema_version=schema_version,
        schema_versions=tuple(sorted(versions)),
        duplicate_ids=tuple(sorted(record_id for record_id, rels in ids.items() if len(rels) > 1)),
        invalid_relationship_targets=tuple(sorted(relationships)),
        malformed_metadata=tuple(sorted(malformed)),
        missing_evidence=tuple(sorted(evidence)),
        invalid_scope=tuple(sorted(scopes)),
        timestamp_precision_violations=tuple(sorted(precision)),
        interrupted_journals=interrupted,
    )
