import fcntl
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from arden.constants import RAW_TOOL_RESULT_INLINE_MAX_BYTES, SESSION_EVENT_DURABLE_RETENTION
from arden.core.raw_tool_results import preview_text
from arden.outbox.store import OUTBOX_TABLE, compact_legacy_completed

_FTS_TRIGGERS = """
CREATE TRIGGER session_messages_ai AFTER INSERT ON session_messages BEGIN
    INSERT INTO session_messages_fts(rowid, search_text) VALUES (new.rowid, new.search_text);
END;
CREATE TRIGGER session_messages_ad AFTER DELETE ON session_messages BEGIN
    INSERT INTO session_messages_fts(session_messages_fts, rowid, search_text)
    VALUES ('delete', old.rowid, old.search_text);
END;
CREATE TRIGGER session_messages_au AFTER UPDATE ON session_messages BEGIN
    INSERT INTO session_messages_fts(session_messages_fts, rowid, search_text)
    VALUES ('delete', old.rowid, old.search_text);
    INSERT INTO session_messages_fts(rowid, search_text) VALUES (new.rowid, new.search_text);
END;
"""

_TOOL_RESULTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_results (
    tool_result_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    run_id TEXT,
    tool_call_id TEXT NOT NULL,
    tool_name TEXT,
    content_sha256 TEXT NOT NULL,
    content_bytes INTEGER NOT NULL,
    stored_bytes INTEGER NOT NULL,
    compression TEXT NOT NULL,
    blob_ref TEXT NOT NULL,
    blob_path TEXT NOT NULL,
    preview TEXT,
    retention_class TEXT NOT NULL DEFAULT 'session',
    expires_at TEXT,
    source_event_seq INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, tool_call_id, content_sha256)
);
CREATE INDEX IF NOT EXISTS idx_tool_results_session_created ON tool_results(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tool_results_tool_call ON tool_results(tool_call_id);
CREATE INDEX IF NOT EXISTS idx_tool_results_expires ON tool_results(expires_at) WHERE expires_at IS NOT NULL;
"""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    result: dict[str, int] = {}
    for table in (
        "sessions",
        "session_messages",
        "session_events",
        "tool_calls",
        "tool_results",
        OUTBOX_TABLE,
    ):
        if _table_exists(connection, table):
            result[table] = int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    return result


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item if isinstance(item, str) else str(item.get("text") or "")
            for item in content
            if isinstance(item, (str, dict))
        )
    return "" if content is None else str(content)


def _flatten_message(message: dict[str, Any]) -> str:
    return _text(message.get("content")).strip()


def _prose_digest(connection: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    if not _table_exists(connection, "session_messages"):
        return digest.hexdigest()
    for row in connection.execute(
        """
        SELECT session_id, seq, role, message_json
        FROM session_messages
        WHERE role IN ('user', 'assistant')
        ORDER BY session_id, seq
        """
    ):
        try:
            message = json.loads(row["message_json"])
        except (TypeError, json.JSONDecodeError):
            message = {"content": str(row["message_json"])}
        value = [row["session_id"], row["seq"], row["role"], _text(message.get("content"))]
        digest.update(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
    return digest.hexdigest()


@contextmanager
def _offline_lock(database: Path):
    wal = Path(f"{database}-wal")
    if wal.exists() and wal.stat().st_size:
        raise RuntimeError("database has uncheckpointed WAL state; stop Arden and checkpoint it before maintenance")
    lock_path = database.with_name(f".{database.name}.offline-maintenance.lock")
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another offline maintenance process holds the lock") from exc
        yield


def _copy_database(source: Path, destination: Path) -> None:
    uri = f"file:{quote(str(source.resolve()))}?mode=ro&immutable=1"
    source_connection = sqlite3.connect(uri, uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def _ensure_tool_results_schema(connection: sqlite3.Connection) -> None:
    required = {
        "tool_result_id",
        "session_id",
        "tool_call_id",
        "content_sha256",
        "blob_ref",
        "blob_path",
    }
    if _table_exists(connection, "tool_results") and not required <= _columns(connection, "tool_results"):
        suffix = 0
        legacy = "tool_results_legacy_offline"
        while _table_exists(connection, legacy):
            suffix += 1
            legacy = f"tool_results_legacy_offline_{suffix}"
        connection.execute(f'ALTER TABLE tool_results RENAME TO "{legacy}"')
    connection.executescript(_TOOL_RESULTS_SCHEMA)
    if _table_exists(connection, "tool_calls") and "result_ref" not in _columns(connection, "tool_calls"):
        connection.execute("ALTER TABLE tool_calls ADD COLUMN result_ref TEXT")


def _write_blob(blob_root: Path, content: str) -> tuple[str, str, int, int]:
    raw = content.encode()
    content_sha256 = hashlib.sha256(raw).hexdigest()
    path = blob_root / content_sha256[:2] / f"{content_sha256}.txt.gz"
    compressed = gzip.compress(raw, compresslevel=6, mtime=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = gzip.decompress(path.read_bytes())
        if hashlib.sha256(existing).hexdigest() != content_sha256:
            raise RuntimeError(f"content-addressed blob collision: {path}")
    else:
        temporary = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(compressed)
        temporary.replace(path)
    return content_sha256, str(path.resolve()), len(raw), path.stat().st_size


def _offload(
    connection: sqlite3.Connection,
    *,
    blob_root: Path,
    session_id: str,
    seq: int,
    run_id: str | None,
    tool_call_id: str,
    tool_name: str | None,
    content: str,
    created_at: str,
    source_event_seq: int | None,
) -> tuple[str, str, bool, dict[str, Any]]:
    content_sha256, blob_path, content_bytes, stored_bytes = _write_blob(blob_root, content)
    existing = connection.execute(
        """
        SELECT tool_result_id FROM tool_results
        WHERE session_id = ? AND tool_call_id = ? AND content_sha256 = ?
        """,
        (session_id, tool_call_id, content_sha256),
    ).fetchone()
    seed = f"{session_id}\0{seq}\0{tool_call_id}\0{content_sha256}".encode()
    tool_result_id = str(existing[0]) if existing else f"tr_{hashlib.sha256(seed).hexdigest()[:24]}"
    preview = preview_text(content)
    if existing is None:
        connection.execute(
            """
            INSERT INTO tool_results (
                tool_result_id, session_id, run_id, tool_call_id, tool_name,
                content_sha256, content_bytes, stored_bytes, compression,
                blob_ref, blob_path, preview, retention_class, expires_at,
                source_event_seq, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'gzip', ?, ?, ?, 'session', NULL, ?, ?)
            """,
            (
                tool_result_id,
                session_id,
                run_id,
                tool_call_id,
                tool_name,
                content_sha256,
                content_bytes,
                stored_bytes,
                f"sha256:{content_sha256}",
                blob_path,
                preview,
                source_event_seq,
                created_at,
            ),
        )
    if _table_exists(connection, "tool_calls"):
        call_columns = _columns(connection, "tool_calls")
        if {"session_id", "tool_call_id", "result_ref"} <= call_columns:
            connection.execute(
                "UPDATE tool_calls SET result_ref = ? WHERE session_id = ? AND tool_call_id = ?",
                (tool_result_id, session_id, tool_call_id),
            )
    pointer = f"{preview}\n\n[Full raw tool result stored as {tool_result_id}; {content_bytes} bytes.]"
    blob = {
        "tool_result_id": tool_result_id,
        "blob_ref": f"sha256:{content_sha256}",
        "blob_path": blob_path,
        "content_sha256": content_sha256,
        "content_bytes": content_bytes,
        "stored_bytes": stored_bytes,
    }
    return tool_result_id, pointer, existing is None, blob


def _migrate_inline_results(
    connection: sqlite3.Connection, blob_root: Path, inline_limit: int
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    stats = {"event_bodies": 0, "message_bodies": 0, "raw_bytes": 0, "new_manifests": 0}
    blobs: dict[str, dict[str, Any]] = {}
    if _table_exists(connection, "session_events"):
        rows = connection.execute(
            """
            SELECT rowid, session_id, seq, run_id, created_at, event_json
            FROM session_events WHERE event_type = 'TOOL_CALL_RESULT'
            ORDER BY session_id, seq
            """
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["event_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            content = payload.get("content")
            call_id = payload.get("tool_call_id")
            if not isinstance(content, str) or len(content.encode()) <= inline_limit or not isinstance(call_id, str):
                continue
            tool_result_id, pointer, created, blob = _offload(
                connection,
                blob_root=blob_root,
                session_id=str(row["session_id"]),
                seq=int(row["seq"]),
                run_id=str(row["run_id"]) if row["run_id"] else None,
                tool_call_id=call_id,
                tool_name=str(payload.get("name")) if payload.get("name") else None,
                content=content,
                created_at=str(row["created_at"]),
                source_event_seq=int(row["seq"]),
            )
            payload["content"] = pointer
            payload["preview"] = preview_text(content)
            payload["raw_ref"] = tool_result_id
            payload["content_bytes"] = len(content.encode())
            payload["retention_class"] = "session"
            if isinstance(payload.get("data"), dict):
                payload["data"].pop("_raw_tool_result", None)
                if not payload["data"]:
                    payload.pop("data")
            connection.execute(
                "UPDATE session_events SET event_json = ? WHERE rowid = ?",
                (json.dumps(payload, ensure_ascii=False, separators=(",", ":")), row["rowid"]),
            )
            stats["event_bodies"] += 1
            stats["raw_bytes"] += blob["content_bytes"]
            stats["new_manifests"] += int(created)
            blobs[blob["blob_ref"]] = blob
    if _table_exists(connection, "session_messages"):
        rows = connection.execute(
            """
            SELECT rowid, session_id, seq, created_at, message_json
            FROM session_messages WHERE role = 'tool'
            ORDER BY session_id, seq
            """
        ).fetchall()
        for row in rows:
            try:
                message = json.loads(row["message_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            content = message.get("content")
            call_id = message.get("tool_call_id")
            if not isinstance(content, str) or len(content.encode()) <= inline_limit or not isinstance(call_id, str):
                continue
            tool_result_id, pointer, created, blob = _offload(
                connection,
                blob_root=blob_root,
                session_id=str(row["session_id"]),
                seq=int(row["seq"]),
                run_id=None,
                tool_call_id=call_id,
                tool_name=None,
                content=content,
                created_at=str(row["created_at"]),
                source_event_seq=None,
            )
            message["content"] = pointer
            message["raw_ref"] = tool_result_id
            message["content_bytes"] = len(content.encode())
            connection.execute(
                "UPDATE session_messages SET message_json = ?, search_text = ? WHERE rowid = ?",
                (
                    json.dumps(message, ensure_ascii=False, separators=(",", ":")),
                    pointer,
                    row["rowid"],
                ),
            )
            stats["message_bodies"] += 1
            stats["raw_bytes"] += blob["content_bytes"]
            stats["new_manifests"] += int(created)
            blobs[blob["blob_ref"]] = blob
    return stats, sorted(blobs.values(), key=lambda blob: blob["blob_ref"])


def _rebuild_transcript_fts(connection: sqlite3.Connection) -> bool:
    if not _table_exists(connection, "session_messages"):
        return False
    columns = _columns(connection, "session_messages")
    removed_legacy_column = "file_search_text" in columns
    connection.executescript(
        """
        DROP TRIGGER IF EXISTS session_messages_ai;
        DROP TRIGGER IF EXISTS session_messages_ad;
        DROP TRIGGER IF EXISTS session_messages_au;
        DROP TABLE IF EXISTS session_messages_fts;
        """
    )
    if "search_text" not in columns:
        connection.execute("ALTER TABLE session_messages ADD COLUMN search_text TEXT")
    if removed_legacy_column:
        connection.execute("ALTER TABLE session_messages DROP COLUMN file_search_text")
    for row in connection.execute("SELECT rowid, message_json FROM session_messages").fetchall():
        try:
            message = json.loads(row["message_json"])
        except (TypeError, json.JSONDecodeError):
            message = {}
        connection.execute(
            "UPDATE session_messages SET search_text = ? WHERE rowid = ?",
            (_flatten_message(message), row["rowid"]),
        )
    connection.execute(
        """
        CREATE VIRTUAL TABLE session_messages_fts USING fts5(
            search_text, content='session_messages', content_rowid='rowid'
        )
        """
    )
    connection.executescript(_FTS_TRIGGERS)
    connection.execute("INSERT INTO session_messages_fts(session_messages_fts) VALUES('rebuild')")
    return removed_legacy_column


def _prune_events(connection: sqlite3.Connection, keep: int, archived_keep: int) -> int:
    if not _table_exists(connection, "session_events"):
        return 0
    before = int(connection.execute("SELECT COUNT(*) FROM session_events").fetchone()[0])
    has_archive_state = _table_exists(connection, "sessions") and "archived_at" in _columns(connection, "sessions")
    if has_archive_state:
        connection.execute(
            """
            DELETE FROM session_events
            WHERE rowid IN (
                SELECT rowid FROM (
                    SELECT
                        e.rowid,
                        ROW_NUMBER() OVER (PARTITION BY e.session_id ORDER BY e.seq DESC) AS rank,
                        CASE WHEN s.archived_at IS NOT NULL THEN ? ELSE ? END AS keep_limit
                    FROM session_events e
                    LEFT JOIN sessions s ON s.session_id = e.session_id
                ) WHERE rank > keep_limit
            )
            """,
            (archived_keep, keep),
        )
    else:
        connection.execute(
            """
        DELETE FROM session_events
        WHERE rowid IN (
            SELECT rowid FROM (
                SELECT rowid, ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY seq DESC) AS rank
                FROM session_events
            ) WHERE rank > ?
        )
        """,
        (keep,),
        )
    if _table_exists(connection, "session_event_retention_state"):
        connection.execute("UPDATE session_event_retention_state SET writes_since_prune = 0")
    after = int(connection.execute("SELECT COUNT(*) FROM session_events").fetchone()[0])
    return before - after


def _estimated_reclaim_bytes(database: Path, event_keep: int, archived_event_keep: int) -> int:
    uri = f"file:{quote(str(database.resolve()))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        reclaim = 0
        if _table_exists(connection, "session_events"):
            if _table_exists(connection, "sessions") and "archived_at" in _columns(connection, "sessions"):
                reclaim += int(
                    connection.execute(
                        """
                        SELECT COALESCE(SUM(bytes), 0) FROM (
                            SELECT
                                length(e.event_json) AS bytes,
                                ROW_NUMBER() OVER (PARTITION BY e.session_id ORDER BY e.seq DESC) AS rank,
                                CASE WHEN s.archived_at IS NOT NULL THEN ? ELSE ? END AS keep_limit
                            FROM session_events e
                            LEFT JOIN sessions s ON s.session_id = e.session_id
                        ) WHERE rank > keep_limit
                        """,
                        (archived_event_keep, event_keep),
                    ).fetchone()[0]
                )
            else:
                reclaim += int(
                    connection.execute(
                        """
                        SELECT COALESCE(SUM(bytes), 0) FROM (
                            SELECT
                                length(event_json) AS bytes,
                                ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY seq DESC) AS rank
                            FROM session_events
                        ) WHERE rank > ?
                        """,
                        (event_keep,),
                    ).fetchone()[0]
                )
        if _table_exists(connection, OUTBOX_TABLE):
            reclaim += int(
                connection.execute(
                    f"SELECT COALESCE(SUM(length(payload)), 0) FROM {OUTBOX_TABLE} WHERE status = 'completed'"
                ).fetchone()[0]
            )
        if _table_exists(connection, "session_messages") and "file_search_text" in _columns(
            connection, "session_messages"
        ):
            reclaim += int(
                connection.execute("SELECT COALESCE(SUM(length(file_search_text)), 0) FROM session_messages").fetchone()[0]
            )
        return reclaim
    finally:
        connection.close()


def _missing_blob_refs(connection: sqlite3.Connection) -> set[str]:
    if not _table_exists(connection, "tool_results"):
        return set()
    return {
        str(row["blob_ref"])
        for row in connection.execute("SELECT blob_ref, blob_path FROM tool_results").fetchall()
        if not Path(row["blob_path"]).is_file()
    }


def _verify_blobs(connection: sqlite3.Connection, *, allow_missing: bool) -> tuple[int, set[str]]:
    if not _table_exists(connection, "tool_results"):
        return 0, set()
    verified = 0
    missing: set[str] = set()
    for row in connection.execute(
        "SELECT blob_ref, content_sha256, content_bytes, compression, blob_path FROM tool_results"
    ).fetchall():
        path = Path(row["blob_path"])
        if not path.is_file():
            missing.add(str(row["blob_ref"]))
            continue
        raw = path.read_bytes()
        if row["compression"] == "gzip":
            raw = gzip.decompress(raw)
        if len(raw) != row["content_bytes"] or hashlib.sha256(raw).hexdigest() != row["content_sha256"]:
            raise RuntimeError(f"blob integrity mismatch: {path}")
        verified += 1
    if missing and not allow_missing:
        raise RuntimeError(f"{len(missing)} tool-result blobs are already missing; rerun with explicit allowance")
    return verified, missing


def compact_legacy_database(
    database: Path,
    *,
    blob_root: Path,
    output: Path | None = None,
    apply: bool = False,
    event_keep: int = SESSION_EVENT_DURABLE_RETENTION,
    archived_event_keep: int | None = None,
    inline_limit: int = RAW_TOOL_RESULT_INLINE_MAX_BYTES,
    outbox_retention_days: int = 1,
    allow_existing_missing_blobs: bool = False,
) -> dict[str, Any]:
    """Build and verify a compacted DB; optionally atomically swap it in."""
    database = database.resolve()
    blob_root = blob_root.resolve()
    if not database.is_file():
        raise FileNotFoundError(database)
    if apply and output is not None:
        raise ValueError("output and apply are mutually exclusive")
    if not apply and output is None:
        raise ValueError("output is required unless apply=True")
    archived_event_keep = event_keep if archived_event_keep is None else archived_event_keep
    if event_keep < 1 or archived_event_keep < 0 or inline_limit < 1 or outbox_retention_days < 1:
        raise ValueError("retention and inline limits are invalid")
    estimated_reclaim = _estimated_reclaim_bytes(database, event_keep, archived_event_keep)
    source_bytes = database.stat().st_size
    estimated_candidate = max(64 * 1024 * 1024, source_bytes - int(estimated_reclaim * 0.75))
    required_free = source_bytes + estimated_candidate + 64 * 1024 * 1024
    if shutil.disk_usage(database.parent).free < required_free:
        raise RuntimeError(f"insufficient free space; need at least {required_free} bytes")
    if output is not None and output.exists():
        raise FileExistsError(output)

    with _offline_lock(database), tempfile.TemporaryDirectory(dir=database.parent) as temporary_dir:
        temporary_root = Path(temporary_dir)
        working = temporary_root / "working.db"
        candidate = (
            database.with_name(f".{database.name}.compacted-{uuid.uuid4().hex}.db")
            if apply
            else output.resolve()
        )
        assert candidate is not None
        _copy_database(database, working)
        connection = sqlite3.connect(working)
        connection.row_factory = sqlite3.Row
        try:
            source_counts = _counts(connection)
            source_prose = _prose_digest(connection)
            _ensure_tool_results_schema(connection)
            existing_missing_blobs = _missing_blob_refs(connection)
            fts_removed = _rebuild_transcript_fts(connection)
            migration_stats, blobs = _migrate_inline_results(connection, blob_root, inline_limit)
            events_pruned = _prune_events(connection, event_keep, archived_event_keep)
            outbox_stats = compact_legacy_completed(connection, outbox_retention_days)
            connection.commit()
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("working database failed integrity_check")
            verified_blobs, missing_blobs = _verify_blobs(
                connection, allow_missing=allow_existing_missing_blobs
            )
            if not missing_blobs <= existing_missing_blobs:
                raise RuntimeError("migration introduced new missing blob references")
            final_counts = _counts(connection)
            final_prose = _prose_digest(connection)
            for table in ("sessions", "session_messages", "tool_calls"):
                if source_counts.get(table) != final_counts.get(table):
                    raise RuntimeError(f"row-count parity failed for {table}")
            if source_prose != final_prose:
                raise RuntimeError("user/assistant prose parity failed")
            if candidate.exists():
                raise FileExistsError(candidate)
            escaped = str(candidate).replace("'", "''")
            connection.execute(f"VACUUM INTO '{escaped}'")
        finally:
            connection.close()
        compacted = sqlite3.connect(candidate)
        try:
            if compacted.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("compacted database failed integrity_check")
        finally:
            compacted.close()
        os.chmod(candidate, database.stat().st_mode & 0o777)

        rollback: Path | None = None
        if apply:
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            rollback = database.with_name(f"{database.name}.precompact-{timestamp}")
            if rollback.exists():
                raise FileExistsError(rollback)
            os.replace(database, rollback)
            try:
                os.replace(candidate, database)
            except BaseException:
                os.replace(rollback, database)
                raise
            final_path = database
        else:
            final_path = candidate

        manifest = {
            "format_version": "arden-session-compaction-v1",
            "created_at": datetime.now(UTC).isoformat(),
            "source": {"path": str(database), "sha256": _sha256_file(rollback or database), "counts": source_counts},
            "output": {
                "path": str(final_path),
                "sha256": _sha256_file(final_path),
                "bytes": final_path.stat().st_size,
                "counts": final_counts,
            },
            "rollback_path": str(rollback) if rollback else None,
            "event_keep": event_keep,
            "archived_event_keep": archived_event_keep,
            "preflight": {
                "estimated_reclaim_bytes": estimated_reclaim,
                "required_free_bytes": required_free,
            },
            "inline_limit": inline_limit,
            "outbox_retention_days": outbox_retention_days,
            "events_pruned": events_pruned,
            "outbox": outbox_stats,
            "legacy_file_search_text_removed": fts_removed,
            "migration": migration_stats,
            "verified_blob_manifests": verified_blobs,
            "preexisting_missing_blob_refs": sorted(missing_blobs),
            "new_blobs": blobs,
            "checks": {"integrity": "ok", "row_counts": "ok", "user_assistant_prose": "ok"},
        }
        manifest_path = final_path.with_name(f"{final_path.name}.compaction-manifest.json")
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        manifest["manifest_path"] = str(manifest_path)
        return manifest
