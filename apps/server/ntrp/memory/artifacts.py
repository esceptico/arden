"""Read-only access over the file-canonical memory vault, for the desktop UI and
the memory_read/memory_tree tools.

The markdown pages under the memory artifacts directory ARE canonical (written by
FilePageStore). This module no longer projects records into markdown — it only:
  - list_artifacts / read_artifact: safe, symlink-hardened reads + frontmatter/timeline
    parsing for the UI and tools.
  - append_event: the audit changelog (changelog/<year>/<month>.md + rollups), the one
    thing still written here, with operational-id redaction.
"""

from __future__ import annotations

import errno
import logging
import os
import re
import sqlite3
import stat
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ntrp.memory.frontmatter import QuotedStr, dump_frontmatter, parse_frontmatter, strip_frontmatter
from ntrp.memory.page_events import page_revision
from ntrp.memory.pages import parse_line as _parse_line

_logger = logging.getLogger(__name__)

ROOT_ARTIFACTS: dict[str, tuple[str, str]] = {
    "me.md": ("topic", "Profile"),
    "active-work.md": ("topic", "Active work"),
    "README.md": ("source", "Memory artifacts"),
    "tooling.md": ("source", "Agent memory tooling"),
    "directives.md": ("directive", "Directives"),
    "lessons.md": ("directive", "Playbook (learned)"),
    "references.md": ("source", "References"),
    "index.md": ("topic", "Index"),
    "AGENTS.md": ("source", "Memory conventions"),
    "health.md": ("topic", "Health & gaps"),
}
ARTIFACT_DIR_KINDS: dict[str, str] = {
    "facts": "fact",
    "context": "topic",
    "topics": "topic",  # unified subject folder (people/products/projects/topics)
    "entities": "topic",  # legacy — folded into topics/ (kept so leftovers still read)
    "projects": "topic",  # legacy — folded into topics/
    "references": "source",
    "feeds": "source",  # automation-owned briefings (feeds/<slug>.md) — whole-page rewritten per run
    "insights": "topic",  # cross-domain dream outputs (OKF insights/)
    "daily": "source",  # dated activity logs (daily/<date>.md) — browsable history, prose-only
    "changelog": "changelog",
}
ARTIFACT_DIR_ORDER = {name: i for i, name in enumerate(ARTIFACT_DIR_KINDS)}
_OPEN_RESOURCE_SUFFIXES = {".md", ".txt"}
_ENGINE_RESOURCE_DIRS = {"raw", ".ntrp", ".index", ".maintenance"}
_PAGE_EDIT_INTERNAL_PREFIXES = (
    Path("raw/events"),
    Path(".ntrp/maintenance/page-edit-previews"),
)
_SYNTHESIS_MAINTENANCE_PREFIXES = (
    Path(".ntrp/maintenance/synthesis-bases"),
    Path(".ntrp/maintenance/synthesis-candidates"),
)
_DAILY_MAINTENANCE_PREFIXES = (
    Path(".ntrp/maintenance/daily-bases"),
    Path(".ntrp/maintenance/daily-candidates"),
)

MAX_LOG_CHARS = 500
MAX_DOSSIER_SNIPPET_CHARS = 280
# Pages that are intentionally NEVER prose-synthesized (synthesize._SKIP_NAMES / _SKIP_DIRS):
# their value IS the timeline records (verbatim rules/lessons/pointers, or dream insights),
# so render those instead of a "synthesis pending" placeholder that never resolves.
_RECORD_LIST_PAGES = {"directives.md", "lessons.md", "references.md"}
_RECORD_LIST_DIRS = {"insights"}  # insights/<month>.md — dream insights are the records


def _is_record_list_page(rel: str) -> bool:
    parts = Path(rel).parts
    return rel in _RECORD_LIST_PAGES or (len(parts) > 1 and parts[0] in _RECORD_LIST_DIRS)

_CHANGELOG_HEADER_TEMPLATE = (
    "# Changelog {month}\n\n"
    "Atomic monthly memory mutation log for {month}. "
    "Markdown is generated from DB mutations and append events; do not edit it as canonical memory.\n"
)
_LEGACY_CHANGELOG_RE = re.compile(r"^-\s+(\d{4})-(\d{2})")
_CHANGELOG_MONTH_RE = re.compile(r"^changelog/(\d{4})/(\d{4}-\d{2})\.md$")
_CHANGELOG_YEAR_RE = re.compile(r"^changelog/(\d{4})\.md$")
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")
_BECAUSE_OF_RE = re.compile(r"\s*\(because of [^)]*\)")
_WIKILINK_RE = re.compile(r"\[\[([^\]#|]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")
_DEBUG_KEYS = (
    "scope",
    "scope_key",
    "scope_kind",
    "source",
    "source_ref",
    "source_kind",
    "ref",
    "captured_at",
    "project",
    "project_id",
    "session",
    "session_id",
    "run_id",
    "span_id",
    "tool_id",
    "trigger_key",
    "record_id",
    "hash",
)
_DEBUG_KEY_PATTERN = "|".join(re.escape(k).replace("_", r"[_-]?") for k in _DEBUG_KEYS)
_SOURCE_REF_RE = re.compile(r"\bSourceRef\([^)]*\)", flags=re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s,;)]+")
_DEBUG_KV_RE = re.compile(
    r"(?:(?<=^)|(?<=[\s,;({\[]))"
    r"[\"']?(?:" + _DEBUG_KEY_PATTERN + r")[\"']?"
    r"\s*[:=]\s*"
    r"(?:\{[^}\r\n]*\}|\[[^\]\r\n]*\]|[A-Za-z_][\w.]*\([^)]*\)|\([^)]*\)|\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;)\]}]+)",
    flags=re.IGNORECASE,
)
_LOCAL_PATH_RE = re.compile(
    r"(?<!\w)(?:~|/Users|/home|/private/var/folders|/private/tmp|/tmp|/var/folders)(?:/[^\s,;:)]*)*"
    r"|\b[A-Za-z]:(?:\\|/)[^\s,;)]*",
)
_REPO_PATH_RE = re.compile(
    r"(?<!\w)(?:apps|packages|src|tests|docs|backup|session_logs|preserved_tmp_[A-Za-z0-9_-]*|pipeline-v2|trigger|scripts)(?:/[^\s,;:)]+)+"
    r"|(?<!\w)/api(?:/[^\s,;:)]+)+"
    r"|(?<!\w)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^\s,;:)]+)*(?:\.(?:py|ts|tsx|rs|md|jsonl?|ya?ml|toml|log|sh|sql|html|css))",
)
_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_LONG_HEX_RE = re.compile(r"(?i)(?=[0-9a-f]*[a-f])\b[0-9a-f]{16,}\b")
_SHORT_COMMIT_RE = re.compile(r"(?i)(?=.*[a-f])\b[0-9a-f]{7,15}\b")
# Redact opaque operational identifiers, not meaningful technical names.
# Human-readable experiment/metric tokens like `stage25_sentence_bridge_300`,
# `exact_match`, `avg_loss`, `no_act`, and dashed slugs must remain readable.
_DASHED_ID_RE = re.compile(r"\b[A-Z0-9]{5,}(?:-[A-Z0-9]{4,})+\b")
_PROJECT_ID_RE = re.compile(r"\bproject:proj_[A-Za-z0-9_-]+\b|\bproj_[A-Za-z0-9_-]{8,}\b")
_RECORD_ID_RE = re.compile(r"\brec_[A-Za-z0-9_-]{6,}\b")
_OPERATIONAL_ID_RE = re.compile(
    r"\b(?:prod|run|span|tool|toolu|call|trigger|task|thread|msg)_[A-Za-z0-9-]{8,}\b"
    r"|\btr_dev_[A-Za-z0-9-]{8,}\b"
    r"|\bchatcmpl-[A-Za-z0-9_-]{6,}\b"
    r"|\bC[A-Z0-9]{8,}/p\d{10,}\b"
    r"|\b[CDG][A-Z0-9]{8,}\b"
    r"|\bp\d{10,}\b"
)
_LEGACY_UNQUOTED_TECH_PLACEHOLDER_RE = re.compile(r"(?<!`)\[technical id\](?!`)")
_FILE_TOKEN_RE = re.compile(
    r"(?<!\w)[A-Za-z0-9_.@+-]+\.(?:py|ts|tsx|rs|md|jsonl?|ya?ml|toml|log|sh|sql|html|css|tar|pdf|docx?)(?!\w)"
)
_HASH_WORD_RE = re.compile(r"\b(?:sha256|sha-?256|hash(?:es|ed|ing)?|checksum(?:s)?)\b", flags=re.IGNORECASE)
_PROVENANCE_PHRASE_RE = re.compile(
    r"\b(?:trace link|source inspection|supporting evidence|run artifacts?|exact reports?|files touched|raw html/api error|known-issue pointers?)\b",
    flags=re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")


def _slug(text: str | None, *, fallback: str) -> str:
    """Filename slug; an opaque project id collapses to the fallback. Shared by the
    read path (project-artifact scope resolution) and tools/memory path resolution."""
    raw = str(text or "").strip()
    base_source = fallback if _PROJECT_ID_RE.fullmatch(raw) else raw
    base = _SLUG_RE.sub("-", base_source).strip(".-_").lower()
    return base[:60].strip(".-_") or fallback


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _trim_at_word_boundary(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    suffix = "…"
    limit = max(max_chars - len(suffix), 0)
    cut = text[:limit].rstrip()
    boundary = cut.rfind(" ")
    if boundary >= max_chars // 2:
        cut = cut[:boundary].rstrip()
    cut = cut.rstrip(" ,;:-")
    return f"{cut}{suffix}" if cut else suffix[:max_chars]


def _sanitize_visible_text(text: str, *, max_chars: int) -> str:
    """Collapse whitespace and trim to length — NO content redaction. Records are
    written clean by the curator (LLM judgment keeps meaningful names and omits
    secrets/opaque ids), so subject pages render REAL paths/names/links instead of
    useless [path]/[id] placeholders. Redaction here was a regex blacklist that
    made memory less useful, not more."""
    visible = _WHITESPACE_RE.sub(" ", str(text or "")).strip()
    return _trim_at_word_boundary(visible, max_chars)


def _redact_changelog(text: str, *, max_chars: int) -> str:
    """The changelog is an auto-generated AUDIT log of mutations, not user content;
    its events can carry raw SourceRef reprs / opaque ids, so this one path still
    strips them. (Subject pages use the faithful _sanitize_visible_text above.)"""
    visible = re.sub(r"[\r\n]+", " ", str(text or ""))
    visible = _SOURCE_REF_RE.sub(" ", visible)
    visible = _DEBUG_KV_RE.sub(" ", visible)
    visible = _URL_RE.sub("[link]", visible)
    visible = _LEGACY_UNQUOTED_TECH_PLACEHOLDER_RE.sub("technical identifier", visible)
    visible = _LOCAL_PATH_RE.sub("[path]", visible)
    visible = _REPO_PATH_RE.sub("[path]", visible)
    visible = _UUID_RE.sub("[id]", visible)
    visible = _LONG_HEX_RE.sub("[id]", visible)
    visible = _SHORT_COMMIT_RE.sub("[id]", visible)
    visible = _DASHED_ID_RE.sub("[id]", visible)
    visible = _PROJECT_ID_RE.sub("[id]", visible)
    visible = _OPERATIONAL_ID_RE.sub("[id]", visible)
    visible = _HASH_WORD_RE.sub("provenance marker", visible)
    visible = _PROVENANCE_PHRASE_RE.sub("diagnostic detail", visible)
    visible = visible.replace("provenance marker provenance marker", "provenance marker")
    visible = _WHITESPACE_RE.sub(" ", visible).strip()
    return _trim_at_word_boundary(visible, max_chars)


def _safe_log(event: str) -> str:
    return _redact_changelog(event, max_chars=MAX_LOG_CHARS)


def summarize_changelog_text(items: list[str], *, max_items: int = 3) -> str:
    snippets = [
        _redact_changelog(item, max_chars=180)
        for item in items
        if _redact_changelog(item, max_chars=180)
    ][:max_items]
    if not snippets:
        return "details redacted"
    suffix = "" if len(items) <= max_items else f"; +{len(items) - max_items} more"
    return "; ".join(snippets) + suffix


def _artifact_record_count(kind: str, content: str) -> int:
    if kind in {"directive", "fact"}:
        return len(re.findall(r"^- ", content, flags=re.MULTILINE))
    return 0


def _record_count_for_artifact(rel: str, kind: str, content: str, timeline: tuple | None = None) -> int | None:
    if rel in {"README.md", "tooling.md"} or rel.startswith("changelog/"):
        return None
    if rel.endswith("/index.md"):
        return None
    if timeline is not None:  # canonical page: count active timeline atoms (raw/ sidecar)
        return sum(1 for ln in timeline if not ln.superseded)
    fm, body = parse_frontmatter(content)
    if "record_count" in fm:
        value = fm["record_count"]
        return int(value) if value is not None else None
    if kind == "topic":
        match = re.search(r"^- Records?: (\d+)\s*$", body, flags=re.MULTILINE)
        return int(match.group(1)) if match else 0
    return _artifact_record_count(kind, body)


def _artifact_directory(rel: str) -> str:
    parts = Path(rel).parts
    return parts[0] if len(parts) > 1 else "memory"


# Reports regenerated on load — everything else in the vault is a canonical page.
_GENERATED_RELS = {"AGENTS.md", "health.md", "index.md", "facts/index.md"}


def _artifact_generated(rel: str, content: str) -> bool:
    if rel in _GENERATED_RELS or rel.startswith("changelog/"):
        return True
    if (
        Path(rel).name == "README.md"
        and "<!-- ntrp:index:start -->" in content
        and "<!-- ntrp:index:end -->" in content
    ):
        return True
    fm, _ = parse_frontmatter(content)
    if "generated" in fm:
        return bool(fm["generated"])
    return False


def artifact_is_editable(rel: str, content: str) -> bool:
    if rel.startswith("changelog/"):
        return False
    fm, _ = parse_frontmatter(content)
    if "editable" in fm:
        return bool(fm["editable"])
    return not _artifact_generated(rel, content)


def _readonly_reason(rel: str, kind: str, generated: bool, editable: bool) -> str | None:
    if rel == "AGENTS.md":
        return "Conventions doc — regenerated on load."
    if rel == "health.md":
        return "Self-audit — regenerated on load from the current pages."
    if rel == "index.md":
        return "Navigational index — regenerated from the pages."
    if rel == "facts/index.md":
        return "Generated from DB records; use recall/record tools for facts."
    if rel.startswith("changelog/"):
        return "Generated audit log; append events through memory tools."
    if generated and not editable:
        return "Generated report — edits are overwritten; change the underlying records."
    return None


def _artifact_snippet(content: str, query: str | None = None) -> str | None:
    content = strip_frontmatter(content)
    lines = [_sanitize_visible_text(line, max_chars=MAX_DOSSIER_SNIPPET_CHARS) for line in content.splitlines()]
    lines = [
        line
        for line in lines
        if line and not line.startswith("#") and not line.startswith("_Generated read-only dossier")
    ]
    if query:
        q = query.lower()
        for line in lines:
            if q in line.lower():
                return line
    for line in lines:
        if not line.startswith("---"):
            return line
    return None


def _artifact_summary(content: str) -> str | None:
    frontmatter, _ = parse_frontmatter(content)
    summary = frontmatter.get("summary")
    if isinstance(summary, str) and summary.strip():
        return _sanitize_visible_text(summary, max_chars=MAX_DOSSIER_SNIPPET_CHARS)
    return _artifact_snippet(content)


def _decode_artifact_text(content: bytes) -> str:
    """Match text-mode rendering while keeping the original bytes for revision/editing."""
    return content.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _title_from_content(content: str, fallback: str) -> str:
    content = strip_frontmatter(content)
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or fallback
    return fallback


_CHANGELOG_LINE_DATE_RE = re.compile(r"^-\s+\d{4}-\d{2}-(\d{2})T(\d{2}:\d{2})\S*\s*$")


def _compact_changelog_prefix(prefix: str) -> str:
    """Trim a legacy full-ISO line prefix to day + time — the year-month is
    already the file name, and microseconds/offset are noise. No-op if already
    compact or not a dated prefix."""
    m = _CHANGELOG_LINE_DATE_RE.match(prefix)
    return f"- {m.group(1)} {m.group(2)}" if m else prefix


# Going-forward events carry real change text ("Learned: …" / "Remembered: …").
# Legacy lines are normalized on render: maintenance + contentless generics are
# dropped; the old "curator updated N record(s) … transcript:" wrapper is folded
# into a plain "Learned: …".
_CHANGELOG_CURATOR_PREFIX_RE = re.compile(
    r"^curator updated \d+ memory record\(s\) from chat transcript:\s*", flags=re.IGNORECASE
)
_CHANGELOG_DROP_RE = re.compile(
    r"^(?:"
    r"(?:manual\s+)?memory filesystem\b"  # maintenance: rebuild / patch / vN
    r"|curator updated [^:]*\(\d+\s*change\(s\)\)\s*$"  # count-only, no content
    r"|(?:remembered|forgot|added|pinned|unpinned)\b[^:]*$"  # generic verb, no content
    r")",
    flags=re.IGNORECASE,
)


def _normalize_changelog_text(text: str) -> str | None:
    if _CHANGELOG_DROP_RE.match(text):
        return None
    return _CHANGELOG_CURATOR_PREFIX_RE.sub("Learned: ", text).strip() or None
_EVENT_FLAT_RE = re.compile(r"^-\s+(\d{2})\s+(\d{2}:\d{2})\s+—\s+(.*)$")
_EVENT_GROUPED_RE = re.compile(r"^-\s+(\d{2}:\d{2})\s+—\s+(.*)$")
_EVENT_LEGACY_FULL_RE = re.compile(r"^-\s+\d{4}-\d{2}-(\d{2})T(\d{2}:\d{2})\S*\s+—\s+(.*)$")


def _render_changelog_month(content: str, *, year: int, month_num: int, month_label: str) -> str:
    """Group a monthly changelog into readable day sections (## Weekday, Mon D),
    dropping legacy contentless noise. Robust to flat `- DD HH:MM — …`,
    already-grouped `## day` + `- HH:MM — …`, and legacy full-ISO lines."""
    events: list[tuple[str, str, str]] = []  # (day, time, text), file order
    current_day: str | None = None
    for line in content.splitlines():
        if line.startswith("## "):
            m = re.search(r"(\d{1,2})\s*$", line)
            if m:
                current_day = f"{int(m.group(1)):02d}"
            continue
        if not (line.startswith("- ") and " — " in line):
            continue
        flat, legacy, grouped = (
            _EVENT_FLAT_RE.match(line),
            _EVENT_LEGACY_FULL_RE.match(line),
            _EVENT_GROUPED_RE.match(line),
        )
        if flat:
            day, time, text = flat.group(1), flat.group(2), flat.group(3)
        elif legacy:
            day, time, text = legacy.group(1), legacy.group(2), legacy.group(3)
        elif grouped and current_day:
            day, time, text = current_day, grouped.group(1), grouped.group(2)
        else:
            continue
        text = (_safe_log(text) or "").strip()
        text = _normalize_changelog_text(text) if text else None
        if not text:
            continue
        events.append((day, time, text))

    header = _CHANGELOG_HEADER_TEMPLATE.format(month=month_label).rstrip("\n")
    if not events:
        return header + "\n"
    out = [header]
    last_day: str | None = None
    for day, time, text in events:
        if day != last_day:
            try:
                label = datetime(year, month_num, int(day)).strftime("%A, %b ") + str(int(day))
            except ValueError:
                label = f"Day {day}"
            out.extend(["", f"## {label}"])
            last_day = day
        out.append(f"- {time} — {text}")
    return "\n".join(out) + "\n"


def _sanitize_changelog_content(content: str, *, compact_dates: bool = False) -> str:
    # compact_dates only when the content is already routed into a monthly file
    # (the year-month is the filename) — NOT during legacy migration, where the
    # full line date is still needed to route each line to the right month.
    if not content:
        return content
    out: list[str] = []
    for line in content.splitlines():
        if not line.strip():
            out.append("")
            continue
        if line.startswith("- "):
            if " — " in line:
                prefix, payload = line.split(" — ", 1)
                if compact_dates:
                    prefix = _compact_changelog_prefix(prefix)
                out.append(f"{prefix} — {_safe_log(payload) or '[redacted]'}")
            else:
                out.append(f"- {_safe_log(line[2:]) or '[redacted]'}")
        else:
            out.append(_sanitize_visible_text(line, max_chars=MAX_LOG_CHARS))
    sanitized = "\n".join(out) + ("\n" if content.endswith("\n") else "")
    sanitized = sanitized.replace(
        "Markdown is generated from DB mutations and append events; do not edit it as canonical memory.\n- ",
        "Markdown is generated from DB mutations and append events; do not edit it as canonical memory.\n\n- ",
    )
    return sanitized


@dataclass(frozen=True)
class ArtifactMeta:
    labels: tuple[str, ...] = ()
    source: str | None = None


@dataclass(frozen=True)
class MemoryArtifact:
    path: str
    title: str
    kind: str
    scope_kind: str
    scope_key: str | None
    content: str
    summary: str | None
    revision: str
    record_count: int | None
    updated_at: str | None
    type: str = "file"
    directory: str = "memory"
    generated: bool = True
    editable: bool = False
    readonly_reason: str | None = None
    snippet: str | None = None
    labels: tuple[str, ...] = ()
    source: str | None = None
    timeline: tuple = ()  # parsed timeline atoms (read_artifact only; () in list view)
    frontmatter: dict = field(default_factory=dict)  # raw YAML properties (read_artifact only)
    raw_content: str | None = None  # exact decoded file bytes (read_artifact only)


class ArtifactMemoryStore:
    def __init__(self, root: Path, *, project_names: dict[str, str] | None = None):
        self.root = root
        self.project_names = project_names or self._load_project_names()
        self._scope_keys_by_rel: dict[str, str] = {}

    def vault_health(self):
        """Return the exact ledger safety report used by the startup gate."""
        from ntrp.memory.migrate_ledger_v2 import validate_vault

        return validate_vault(self.root)

    def _load_project_names(self) -> dict[str, str]:
        db_path = self.root.parent / "sessions.db"
        if not db_path.exists() or db_path.is_symlink():
            return {}
        try:
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT area_id, name, knowledge_scope FROM areas WHERE archived_at IS NULL"
            ).fetchall()
        except sqlite3.Error:
            return {}
        finally:
            try:
                conn.close()
            except Exception:
                pass
        names: dict[str, str] = {}
        for project_id, name, knowledge_scope in rows:
            if not project_id or not name:
                continue
            display = str(name).strip()
            if not display:
                continue
            names[str(project_id)] = display
            names[f"project:{project_id}"] = display
            if knowledge_scope:
                names[str(knowledge_scope)] = display
        return names

    def ensure_dirs(self) -> None:
        self._assert_root_safe()
        self.root.mkdir(parents=True, exist_ok=True)
        self._assert_root_safe()

    def append_event(self, event: str) -> None:
        self.ensure_dirs()
        self._migrate_legacy_changelog()
        now = datetime.now(UTC)
        rel = self._changelog_month_rel(now)
        path = self._safe_path(rel)
        self._ensure_month_changelog(path, now.strftime("%Y-%m"))
        # Year-month is already the file name (changelog/YYYY/YYYY-MM.md), so the
        # line only needs day + time — no redundant full ISO / microseconds / offset.
        self._append_text_no_symlink(path, f"- {now.strftime('%d %H:%M')} — {_safe_log(event) or '[redacted]'}\n")
        self._write_changelog_rollups()

    def _changelog_month_rel(self, at: datetime) -> str:
        return f"changelog/{at:%Y}/{at:%Y-%m}.md"

    def _ensure_month_changelog(self, path: Path, month: str) -> None:
        if path.exists():
            st = path.lstat()
            if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
                raise FileNotFoundError(path.name)
            self._sanitize_changelog_file(path, ensure_trailing_newline=True)
            return
        self._write_text_no_symlink(path, _CHANGELOG_HEADER_TEMPLATE.format(month=month))

    def _migrate_legacy_changelog(self) -> None:
        path = self.root / "changelog.md"
        if not path.exists() and not path.is_symlink():
            return
        try:
            st = path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise FileNotFoundError("changelog.md")
        content = self._read_text_no_symlink(path)
        sanitized = _sanitize_changelog_content(content)
        fallback = datetime.now(UTC)
        for line in sanitized.splitlines():
            if not line.startswith("- "):
                continue
            match = _LEGACY_CHANGELOG_RE.match(line)
            year = match.group(1) if match else fallback.strftime("%Y")
            month = f"{year}-{match.group(2)}" if match else fallback.strftime("%Y-%m")
            target = self._safe_path(f"changelog/{year}/{month}.md")
            self._ensure_month_changelog(target, month)
            existing = self._read_text_no_symlink(target)
            if line not in existing.splitlines():
                if existing and not existing.endswith("\n"):
                    self._append_text_no_symlink(target, "\n")
                self._append_text_no_symlink(target, f"{line}\n")
        path.unlink()

    def _monthly_changelog_paths(self) -> list[Path]:
        changelog_dir = self.root / "changelog"
        if not changelog_dir.exists() or changelog_dir.is_symlink() or not changelog_dir.is_dir():
            return []
        out: list[Path] = []
        for path in self._iter_artifact_files(changelog_only=True):
            rel = path.relative_to(self.root).as_posix()
            if _CHANGELOG_MONTH_RE.match(rel):
                out.append(path)
        return sorted(out, key=lambda p: p.relative_to(self.root).as_posix())

    def _write_changelog_rollups(self) -> None:
        years: defaultdict[str, dict[str, list[str]]] = defaultdict(dict)
        for path in self._monthly_changelog_paths():
            rel = path.relative_to(self.root).as_posix()
            match = _CHANGELOG_MONTH_RE.match(rel)
            if not match:
                continue
            year, month = match.groups()
            self._sanitize_changelog_file(path, ensure_trailing_newline=True)
            lines = [line for line in self._read_text_no_symlink(path).splitlines() if line.startswith("- ")]
            years[year][month] = lines

        index = [
            "# Changelog",
            "",
            "Memory mutation history.",
            "Monthly files are atomic append logs; yearly and root files are compact rollups only.",
            "",
            "## Years",
            "",
        ]
        if years:
            for year in sorted(years, reverse=True):
                year_lines = [line for month in years[year].values() for line in month]
                event_count = len(year_lines)
                month_count = len(years[year])
                index.append(
                    f"- `changelog/{year}.md` — {event_count} events across "
                    f"{month_count} month{'s' if month_count != 1 else ''}."
                )
                self._write_year_changelog(year, years[year])
        else:
            index.append("_No changelog events yet._")
        self._write("changelog/index.md", "Changelog", "changelog", "global", None, "\n".join(index).rstrip() + "\n", 0)

    def _write_year_changelog(self, year: str, months: dict[str, list[str]]) -> None:
        body = [
            f"# Changelog {year}",
            "",
            "Yearly rollup. Atomic timestamped events live in monthly files.",
            "",
            "## Months",
            "",
        ]
        for month in sorted(months, reverse=True):
            count = len(months[month])
            body.append(f"- `changelog/{year}/{month}.md` — {count} event{'s' if count != 1 else ''}.")
        self._write(
            f"changelog/{year}.md", f"Changelog {year}", "changelog", "global", None, "\n".join(body).rstrip() + "\n", 0
        )

    def _write(
        self,
        rel: str,
        title: str,
        kind: str,
        scope_kind: str,
        scope_key: str | None,
        content: str,
        count: int | None,
        *,
        meta: ArtifactMeta = ArtifactMeta(),
    ) -> MemoryArtifact:
        if not self._allowed_artifact_rel(rel):
            raise FileNotFoundError(rel)
        self.ensure_dirs()
        path = self._safe_path(rel)
        if path.is_symlink():
            raise FileNotFoundError(rel)
        body = strip_frontmatter(content)
        generated = _artifact_generated(rel, body)
        editable = artifact_is_editable(rel, body)
        frontmatter = dump_frontmatter({
            "kind": kind,
            "title": title,
            "scope": {"kind": scope_kind, "key": scope_key},
            "labels": list(meta.labels),
            "source": meta.source,
            "record_count": count,
            "generated": generated,
            "editable": editable,
            "updated": QuotedStr(datetime.now(UTC).isoformat()),
        })
        self._write_text_no_symlink(path, frontmatter + body)
        if scope_key:
            self._scope_keys_by_rel[rel] = scope_key
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode):
            raise FileNotFoundError(rel)
        exact = self.read_resource_bytes(rel)
        exact_text = exact.decode("utf-8")
        return MemoryArtifact(
            path=rel,
            title=title,
            kind=kind,
            scope_kind=scope_kind,
            scope_key=scope_key,
            content=body,
            record_count=count,
            updated_at=datetime.fromtimestamp(st.st_mtime, UTC).isoformat(),
            type="file",
            directory=_artifact_directory(rel),
            generated=generated,
            editable=editable,
            readonly_reason=_readonly_reason(rel, kind, generated, editable),
            snippet=_artifact_snippet(body),
            summary=_artifact_summary(exact_text),
            revision=page_revision(exact),
            labels=meta.labels,
            source=meta.source,
            raw_content=exact_text,
        )

    def list_artifacts(self, *, kind: str | None = None, q: str | None = None) -> list[MemoryArtifact]:
        self.ensure_dirs()
        query = (q or "").strip().lower()
        artifacts: list[MemoryArtifact] = []
        for path in self._iter_artifact_files():
            rel = path.relative_to(self.root).as_posix()
            try:
                st = path.lstat()
                exact = self.read_resource_bytes(rel)
                content = _decode_artifact_text(exact)
            except FileNotFoundError:
                continue
            fm, _ = parse_frontmatter(content)
            artifact_kind, title, scope_kind, scope_key = self._artifact_meta(rel, content)
            if kind and artifact_kind != kind:
                continue
            snippet = _artifact_snippet(content, query if query else None)
            haystack = " ".join([rel, title, artifact_kind, _artifact_directory(rel), snippet or "", content]).lower()
            if query and query not in haystack:
                continue
            generated = _artifact_generated(rel, content)
            editable = artifact_is_editable(rel, content)
            artifacts.append(
                MemoryArtifact(
                    path=rel,
                    title=title,
                    kind=artifact_kind,
                    scope_kind=scope_kind,
                    scope_key=scope_key,
                    content="",
                    record_count=_record_count_for_artifact(rel, artifact_kind, content, self._load_timeline(rel)),
                    updated_at=datetime.fromtimestamp(st.st_mtime, UTC).isoformat(),
                    type="file",
                    directory=_artifact_directory(rel),
                    generated=generated,
                    editable=editable,
                    readonly_reason=_readonly_reason(rel, artifact_kind, generated, editable),
                    snippet=snippet,
                    summary=_artifact_summary(content),
                    revision=page_revision(exact),
                    labels=tuple(fm.get("labels") or ()),
                    source=fm.get("source"),
                )
            )
        return sorted(artifacts, key=lambda a: self._artifact_sort_key(a.path))

    def read_artifact(self, rel: str) -> MemoryArtifact:
        safe = Path(rel)
        if safe.is_absolute() or ".." in safe.parts:
            raise FileNotFoundError(rel)
        rel_posix = safe.as_posix()
        if not self._allowed_artifact_rel(rel_posix):
            raise FileNotFoundError(rel)
        path = self._safe_path(rel_posix)
        try:
            st = path.lstat()
        except FileNotFoundError as exc:
            raise FileNotFoundError(rel) from exc
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise FileNotFoundError(rel)
        if _CHANGELOG_MONTH_RE.match(rel_posix):
            self._sanitize_changelog_file(path)
            st = path.lstat()
        exact = self.read_resource_bytes(rel_posix)
        raw_content = exact.decode("utf-8")
        content = _decode_artifact_text(exact)
        fm, body = parse_frontmatter(content)
        # Canonical pages: the file IS the prose (wiki view); the timeline atoms come
        # from the raw/ sidecar and are surfaced separately so the client can show
        # them as secondary/collapsed evidence.
        timeline = self._load_timeline(rel_posix)
        if timeline is not None:
            prose = body.strip()
            if not prose:
                if _is_record_list_page(rel_posix):
                    # Intentionally never synthesized — these are verbatim rules/lessons/
                    # pointers (paraphrasing would distort them). Show the records, not a
                    # "synthesis pending" note that will never resolve. Dream insights
                    # carry a machine cite tail `(because of ^id1, ^id2)` — provenance
                    # for the engine, noise for the reader; strip it from the view.
                    active = [ln for ln in timeline if not ln.superseded]
                    prose = "\n".join(f"- {_BECAUSE_OF_RE.sub('', ln.text).rstrip()}" for ln in active) or "_No entries yet._"
                else:
                    prose = "_No synthesized summary yet — synthesis pass pending._"
            body = prose
            # Drop the synthesizer's own leading `# Title` h1 (the chrome shows the title) — never a `## Section`.
            body = re.sub(r"^\s*#[^#][^\n]*\n+", "", body, count=1).lstrip("\n")
        timeline = timeline or ()
        kind, title, scope_kind, scope_key = self._artifact_meta(rel_posix, content)
        generated = _artifact_generated(rel_posix, content)
        editable = artifact_is_editable(rel_posix, content)
        return MemoryArtifact(
            path=rel_posix,
            title=title,
            kind=kind,
            scope_kind=scope_kind,
            scope_key=scope_key,
            content=body,
            record_count=_record_count_for_artifact(rel_posix, kind, content, timeline or None),
            updated_at=datetime.fromtimestamp(st.st_mtime, UTC).isoformat(),
            type="file",
            directory=_artifact_directory(rel_posix),
            generated=generated,
            editable=editable,
            readonly_reason=_readonly_reason(rel_posix, kind, generated, editable),
            snippet=_artifact_snippet(content),
            summary=_artifact_summary(content),
            revision=page_revision(exact),
            labels=tuple(fm.get("labels") or ()),
            source=fm.get("source"),
            timeline=timeline,
            frontmatter=dict(fm),
            raw_content=raw_content,
        )

    def read_resource_bytes(self, rel: str, *, page_edit_internal: bool = False) -> bytes:
        """Read an exact open-vault resource without following symlinks."""
        safe = Path(rel)
        rel_posix = safe.as_posix()
        if page_edit_internal:
            if not self._allowed_page_edit_internal(safe):
                raise FileNotFoundError(rel)
        elif not self._allowed_artifact_rel(rel_posix):
            raise FileNotFoundError(rel)
        fd = self._open_anchored_regular(safe, os.O_RDONLY, create_parents=False)
        with os.fdopen(fd, "rb") as stream:
            return stream.read()

    def write_page_edit_preview(self, rel: str, content: bytes) -> None:
        safe = Path(rel)
        if not self._allowed_page_edit_internal(safe) or safe.suffix != ".json":
            raise FileNotFoundError(rel)
        fd = self._open_anchored_regular(
            safe,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            create_parents=True,
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        parent_fd = -1
        try:
            parent_fd, _ = self._open_anchored_parent(safe, create_parents=False)
            os.fsync(parent_fd)
        finally:
            if parent_fd >= 0:
                os.close(parent_fd)

    def read_synthesis_maintenance(self, rel: Path) -> bytes:
        if not self._allowed_synthesis_maintenance(rel):
            raise FileNotFoundError(rel.as_posix())
        fd = self._open_anchored_regular(rel, os.O_RDONLY, create_parents=False)
        with os.fdopen(fd, "rb") as stream:
            return stream.read()

    def write_synthesis_maintenance(self, rel: Path, content: bytes) -> None:
        if not self._allowed_synthesis_maintenance(rel):
            raise FileNotFoundError(rel.as_posix())
        fd = self._open_anchored_regular(
            rel,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            create_parents=True,
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        parent_fd, _ = self._open_anchored_parent(rel, create_parents=False)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)

    def read_daily_maintenance(self, rel: Path) -> bytes:
        if not self._allowed_daily_maintenance(rel):
            raise FileNotFoundError(rel.as_posix())
        fd = self._open_anchored_regular(rel, os.O_RDONLY, create_parents=False)
        with os.fdopen(fd, "rb") as stream:
            return stream.read()

    def write_daily_maintenance(self, rel: Path, content: bytes) -> None:
        if not self._allowed_daily_maintenance(rel):
            raise FileNotFoundError(rel.as_posix())
        fd = self._open_anchored_regular(
            rel,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            create_parents=True,
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        parent_fd, _ = self._open_anchored_parent(rel, create_parents=False)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)

    def delete_page_edit_preview(self, rel: str) -> None:
        safe = Path(rel)
        if not self._allowed_page_edit_internal(safe) or safe.suffix != ".json":
            raise FileNotFoundError(rel)
        parent_fd = -1
        try:
            parent_fd, name = self._open_anchored_parent(safe, create_parents=False)
            target_st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if stat.S_ISLNK(target_st.st_mode) or not stat.S_ISREG(target_st.st_mode):
                raise FileNotFoundError(rel)
            os.unlink(name, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileNotFoundError:
            return
        finally:
            if parent_fd >= 0:
                os.close(parent_fd)

    def page_edit_event_resources(self) -> tuple[str, ...]:
        directory = self.root / "raw" / "events"
        try:
            directory_st = directory.lstat()
            children = sorted(directory.iterdir(), key=lambda path: path.name)
        except OSError:
            return ()
        if stat.S_ISLNK(directory_st.st_mode) or not stat.S_ISDIR(directory_st.st_mode):
            return ()
        resources: list[str] = []
        for path in children:
            try:
                path_st = path.lstat()
            except OSError:
                continue
            if stat.S_ISREG(path_st.st_mode) and not stat.S_ISLNK(path_st.st_mode) and path.suffix == ".md":
                resources.append(path.relative_to(self.root).as_posix())
        return tuple(resources)

    @staticmethod
    def _allowed_page_edit_internal(path: Path) -> bool:
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            return False
        return any(path.is_relative_to(prefix) for prefix in _PAGE_EDIT_INTERNAL_PREFIXES)

    @staticmethod
    def _allowed_synthesis_maintenance(path: Path) -> bool:
        if path.is_absolute() or path.suffix != ".md" or any(part in {"", ".", ".."} for part in path.parts):
            return False
        return any(path.is_relative_to(prefix) for prefix in _SYNTHESIS_MAINTENANCE_PREFIXES)

    @staticmethod
    def _allowed_daily_maintenance(path: Path) -> bool:
        if path.is_absolute() or path.suffix != ".md" or any(part in {"", ".", ".."} for part in path.parts):
            return False
        return any(path.is_relative_to(prefix) for prefix in _DAILY_MAINTENANCE_PREFIXES)

    def _open_anchored_regular(self, rel: Path, flags: int, *, create_parents: bool) -> int:
        parent_fd = -1
        descriptor = -1
        try:
            parent_fd, name = self._open_anchored_parent(rel, create_parents=create_parents)
            descriptor = os.open(
                name,
                flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                0o600,
                dir_fd=parent_fd,
            )
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise FileNotFoundError(rel.as_posix())
            result = descriptor
            descriptor = -1
            return result
        except OSError as exc:
            raise FileNotFoundError(rel.as_posix()) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if parent_fd >= 0:
                os.close(parent_fd)

    def open_anchored_regular(self, rel: Path, flags: int, *, create_parents: bool = False) -> int:
        """Open one vault-relative regular file without following swapped parents."""
        return self._open_anchored_regular(rel, flags, create_parents=create_parents)

    def _open_anchored_parent(self, rel: Path, *, create_parents: bool) -> tuple[int, str]:
        if rel.is_absolute() or not rel.parts or any(part in {"", ".", ".."} for part in rel.parts):
            raise FileNotFoundError(rel.as_posix())
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        current = os.open(self.root, directory_flags)
        try:
            if not stat.S_ISDIR(os.fstat(current).st_mode):
                raise FileNotFoundError(str(self.root))
            for part in rel.parts[:-1]:
                try:
                    child = os.open(part, directory_flags, dir_fd=current)
                except FileNotFoundError:
                    if not create_parents:
                        raise
                    os.mkdir(part, mode=0o700, dir_fd=current)
                    os.fsync(current)
                    child = os.open(part, directory_flags, dir_fd=current)
                if not stat.S_ISDIR(os.fstat(child).st_mode):
                    os.close(child)
                    raise FileNotFoundError(rel.as_posix())
                os.close(current)
                current = child
            result = current
            current = -1
            return result, rel.parts[-1]
        except OSError as exc:
            raise FileNotFoundError(rel.as_posix()) from exc
        finally:
            if current >= 0:
                os.close(current)

    def open_anchored_parent(self, rel: Path, *, create_parents: bool = False) -> tuple[int, str]:
        """Open the anchored parent directory for one vault-relative leaf."""
        return self._open_anchored_parent(rel, create_parents=create_parents)

    def _artifact_meta(self, rel: str, content: str) -> tuple[str, str, str, str | None]:
        fm, _ = parse_frontmatter(content)
        if fm.get("kind") and fm.get("title") and isinstance(fm.get("scope"), dict):
            scope = fm["scope"]
            scope_key = scope.get("key")
            if scope_key is not None:
                self._scope_keys_by_rel[rel] = scope_key
            return str(fm["kind"]), str(fm["title"]), str(scope.get("kind") or "global"), scope_key
        if rel in ROOT_ARTIFACTS:
            kind, title = ROOT_ARTIFACTS[rel]
            return kind, _title_from_content(content, title), "global", None
        parts = Path(rel).parts
        if not parts:
            raise FileNotFoundError(rel)
        dirname = parts[0]
        kind = ARTIFACT_DIR_KINDS.get(dirname)
        if kind is None:
            fallback = Path(rel).stem.replace("-", " ").replace("_", " ").title()
            return "source", _title_from_content(content, fallback), "global", None
        fallback = Path(rel).stem.replace("-", " ").replace("_", " ").title()
        title = _title_from_content(content, fallback)
        name = Path(rel).name
        # topics/ unifies entities + projects: a page is a project (scope "project") when
        # its frontmatter carries a scope_key, otherwise an emergent subject (scope "entity").
        if dirname == "topics" and name not in {"index.md", "needs-triage.md"}:
            fm, _ = parse_frontmatter(content)
            sk = fm.get("scope_key")
            return (kind, title, "project", str(sk)) if sk else (kind, title, "entity", Path(rel).stem)
        if dirname == "entities" and name not in {"index.md", "needs-triage.md"}:
            return kind, title, "entity", Path(rel).stem
        if dirname == "projects" and name not in {"index.md", "inbox.md"}:
            return kind, title, "project", self._scope_key_for_project_artifact(rel, title)
        return kind, title, "global", None

    def _scope_key_for_project_artifact(self, rel: str, title: str) -> str | None:
        if rel in self._scope_keys_by_rel:
            return self._scope_keys_by_rel[rel]
        stem = Path(rel).stem
        matches = [
            key for key, name in self.project_names.items() if _slug(name, fallback="project") == stem or name == title
        ]
        if matches:
            matches.sort(key=lambda key: (0 if key.startswith("project:") else 1, len(key), key))
            return matches[0]
        return stem

    def _load_timeline(self, rel: str) -> tuple | None:
        """Parse the raw/ sidecar timeline for a page; None when the page has no
        sidecar (prose-only pages, generated reports, changelog)."""
        path = self.root / "raw" / rel
        try:
            st = path.lstat()
        except (FileNotFoundError, OSError):
            return None
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            return None
        try:
            text = self._read_text_no_symlink(path)
        except (FileNotFoundError, OSError):
            return None
        return tuple(ln for ln in (_parse_line(r) for r in strip_frontmatter(text).splitlines()) if ln is not None)

    def _artifact_sort_key(self, rel: str) -> tuple[int, int, str]:
        if rel in ROOT_ARTIFACTS:
            return (0, list(ROOT_ARTIFACTS).index(rel), rel)
        parts = Path(rel).parts
        dirname = parts[0] if parts else ""
        return (1, ARTIFACT_DIR_ORDER.get(dirname, 99), rel.casefold())

    def _allowed_artifact_rel(self, rel: str) -> bool:
        safe = Path(rel)
        if safe.is_absolute() or ".." in safe.parts:
            return False
        parts = safe.parts
        if not parts or any(part in ("", ".") for part in parts):
            return False
        if any(part in _ENGINE_RESOURCE_DIRS for part in parts):
            return False
        if safe.suffix.casefold() not in _OPEN_RESOURCE_SUFFIXES:
            return False
        return not (parts[0] == "raw" or parts[0] == ".ntrp")

    def _iter_artifact_files(self, *, changelog_only: bool = False) -> list[Path]:
        if changelog_only:
            out = self._walk_resource_files(self.root / "changelog")
        else:
            out = self._walk_resource_files(self.root)
        return sorted(out, key=lambda p: p.relative_to(self.root).as_posix())

    def _walk_resource_files(self, directory: Path) -> list[Path]:
        try:
            st = directory.lstat()
        except FileNotFoundError:
            return []
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            return []
        out: list[Path] = []
        try:
            children = sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError:
            return []
        for child in children:
            try:
                child_st = child.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(child_st.st_mode):
                continue
            if stat.S_ISDIR(child_st.st_mode):
                if child.name not in _ENGINE_RESOURCE_DIRS:
                    out.extend(self._walk_resource_files(child))
            elif stat.S_ISREG(child_st.st_mode) and child.suffix.casefold() in _OPEN_RESOURCE_SUFFIXES:
                rel = child.relative_to(self.root).as_posix()
                if self._allowed_artifact_rel(rel):
                    out.append(child)
        return out

    def _unlink_regular_artifact(self, rel: str) -> None:
        try:
            path = self._safe_path(rel)
            st = path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISREG(st.st_mode) and not stat.S_ISLNK(st.st_mode):
            path.unlink(missing_ok=True)

    def _remove_markdown_tree(self, directory: Path) -> None:
        try:
            st = directory.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            return
        try:
            children = sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for child in children:
            try:
                child_st = child.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(child_st.st_mode):
                continue
            if stat.S_ISDIR(child_st.st_mode):
                self._remove_markdown_tree(child)
                try:
                    child.rmdir()
                except OSError:
                    pass
            elif stat.S_ISREG(child_st.st_mode) and child.suffix == ".md" and self._within_root(child):
                child.unlink(missing_ok=True)

    def _assert_root_safe(self) -> None:
        try:
            st = self.root.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(st.st_mode):
            raise FileNotFoundError(str(self.root))
        if not stat.S_ISDIR(st.st_mode):
            raise NotADirectoryError(str(self.root))

    def _safe_path(self, rel: str) -> Path:
        self._assert_root_safe()
        safe = Path(rel)
        if safe.is_absolute() or ".." in safe.parts:
            raise FileNotFoundError(rel)
        rel_posix = safe.as_posix()
        if rel_posix in ("", "."):
            raise FileNotFoundError(rel)
        path = self.root / safe
        if not self._within_root(path):
            raise FileNotFoundError(rel)
        if not self._within_root(path.parent):
            raise FileNotFoundError(rel)
        return path

    def _absolute_root(self) -> Path:
        return Path(os.path.abspath(self.root))

    def _within_root(self, path: Path) -> bool:
        root = self._absolute_root()
        candidate = Path(os.path.abspath(path))
        try:
            return os.path.commonpath([str(root), str(candidate)]) == str(root)
        except ValueError:
            return False

    def _ensure_parent_dir(self, path: Path) -> None:
        self.ensure_dirs()
        try:
            rel_parent = path.parent.relative_to(self.root)
        except ValueError as exc:
            raise FileNotFoundError(path.name) from exc
        current = self.root
        for part in rel_parent.parts:
            current = current / part
            try:
                st = current.lstat()
            except FileNotFoundError:
                current.mkdir(mode=0o700)
                st = current.lstat()
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise FileNotFoundError(current.name)

    def _assert_parent_safe(self, path: Path) -> None:
        try:
            rel_parent = path.parent.relative_to(self.root)
        except ValueError as exc:
            raise FileNotFoundError(path.name) from exc
        current = self.root
        for part in rel_parent.parts:
            current = current / part
            try:
                st = current.lstat()
            except FileNotFoundError as exc:
                raise FileNotFoundError(path.name) from exc
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise FileNotFoundError(current.name)

    def _open_no_follow(self, path: Path, flags: int, mode: int = 0o600) -> int:
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if nofollow:
            flags |= nofollow
        nonblock = getattr(os, "O_NONBLOCK", 0)
        if nonblock:
            flags |= nonblock
        try:
            return os.open(path, flags, mode)
        except OSError as exc:
            suspicious = path.is_symlink() or exc.errno in {
                errno.ELOOP,
                getattr(errno, "EMLINK", errno.ELOOP),
                getattr(errno, "ENXIO", errno.ELOOP),
            }
            if suspicious or (exc.errno == errno.ENOENT and path.is_symlink()):
                raise FileNotFoundError(path.name) from exc
            raise

    def _lstat_regular(self, path: Path, *, allow_missing: bool = False) -> os.stat_result | None:
        self._assert_parent_safe(path)
        try:
            st = path.lstat()
        except FileNotFoundError:
            if allow_missing:
                return None
            raise FileNotFoundError(path.name)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise FileNotFoundError(path.name)
        return st

    def _open_regular_no_follow(self, path: Path, flags: int, mode: int = 0o600, *, allow_missing: bool) -> int:
        if allow_missing:
            self._ensure_parent_dir(path)
        self._lstat_regular(path, allow_missing=allow_missing)
        fd = self._open_no_follow(path, flags, mode)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise FileNotFoundError(path.name)
            return fd
        except Exception:
            os.close(fd)
            raise

    def _write_text_no_symlink(self, path: Path, content: str) -> None:
        fd = self._open_regular_no_follow(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600, allow_missing=True)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)

    def _append_text_no_symlink(self, path: Path, content: str) -> None:
        fd = self._open_regular_no_follow(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600, allow_missing=True)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(content)

    def _read_text_no_symlink(self, path: Path) -> str:
        fd = self._open_regular_no_follow(path, os.O_RDONLY, allow_missing=False)
        with os.fdopen(fd, "r", encoding="utf-8") as f:
            return f.read()

    def _sanitize_changelog_file(self, path: Path, *, ensure_trailing_newline: bool = False) -> None:
        self._lstat_regular(path, allow_missing=False)
        content = self._read_text_no_symlink(path)
        rel = path.relative_to(self.root).as_posix()
        month_match = _CHANGELOG_MONTH_RE.match(rel)
        if month_match:
            label = month_match.group(2)  # YYYY-MM
            sanitized = _render_changelog_month(
                content, year=int(month_match.group(1)), month_num=int(label.split("-")[1]), month_label=label
            )
        else:
            sanitized = _sanitize_changelog_content(content, compact_dates=True)
        if ensure_trailing_newline and sanitized and not sanitized.endswith("\n"):
            sanitized += "\n"
        if sanitized != content:
            self._write_text_no_symlink(path, sanitized)
