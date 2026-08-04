import gzip
import json
import sqlite3
from pathlib import Path

from arden.maintenance.session_compaction import compact_legacy_database


def _legacy_database(path: Path) -> str:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, archived_at TEXT);
        CREATE TABLE session_messages (
            session_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            role TEXT NOT NULL,
            message_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            search_text TEXT,
            file_search_text TEXT
        );
        CREATE TABLE session_events (
            session_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            event_json TEXT NOT NULL,
            run_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE tool_calls (
            run_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            tool_call_id TEXT NOT NULL,
            result_ref TEXT
        );
        CREATE TABLE session_event_retention_state (
            session_id TEXT PRIMARY KEY,
            writes_since_prune INTEGER NOT NULL
        );
        CREATE TABLE outbox_events (
            id INTEGER PRIMARY KEY,
            payload TEXT NOT NULL,
            status TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    connection.execute("INSERT INTO sessions VALUES ('session-1', '2026-08-03T10:00:00Z', '2026-08-03T11:00:00Z')")
    large = "large legacy result\n" * 2_000
    messages = [
        {"role": "user", "content": "Keep the user prose."},
        {"role": "assistant", "content": "Keep the assistant prose."},
        {"role": "tool", "tool_call_id": "call-1", "content": large},
    ]
    for seq, message in enumerate(messages):
        connection.execute(
            "INSERT INTO session_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "session-1",
                f"message-{seq}",
                seq,
                message["role"],
                json.dumps(message),
                f"2026-08-03T10:00:0{seq}Z",
                message["content"],
                "legacy",
            ),
        )
    for seq in range(1, 6):
        payload = {
            "type": "TOOL_CALL_RESULT" if seq == 5 else "STATUS",
            "tool_call_id": "call-1",
            "name": "bash",
            "content": large if seq == 5 else f"status-{seq}",
        }
        connection.execute(
            "INSERT INTO session_events VALUES (?, ?, ?, ?, ?, ?)",
            ("session-1", seq, payload["type"], json.dumps(payload), "run-1", f"2026-08-03T10:01:0{seq}Z"),
        )
    connection.execute("INSERT INTO tool_calls VALUES ('run-1', 'session-1', 'call-1', NULL)")
    connection.execute("INSERT INTO session_event_retention_state VALUES ('session-1', 999)")
    connection.execute("INSERT INTO outbox_events VALUES (1, ?, 'completed', '2020-01-01T00:00:00Z')", ("x" * 10_000,))
    connection.execute("INSERT INTO outbox_events VALUES (2, ?, 'completed', '2999-01-01T00:00:00Z')", ("y" * 10_000,))
    connection.execute("INSERT INTO outbox_events VALUES (3, ?, 'dead', '2020-01-01T00:00:00Z')", ("dead-payload",))
    connection.commit()
    connection.close()
    return large


def test_compactor_builds_verified_copy_without_mutating_source(tmp_path: Path):
    source = tmp_path / "sessions.db"
    large = _legacy_database(source)
    output = tmp_path / "sessions.compacted.db"
    blob_root = tmp_path / "blobs"

    manifest = compact_legacy_database(
        source,
        blob_root=blob_root,
        output=output,
        event_keep=2,
        archived_event_keep=1,
        inline_limit=100,
    )

    source_connection = sqlite3.connect(source)
    assert source_connection.execute("SELECT COUNT(*) FROM session_events").fetchone()[0] == 5
    assert "file_search_text" in {row[1] for row in source_connection.execute("PRAGMA table_info(session_messages)")}
    source_connection.close()

    compacted = sqlite3.connect(output)
    compacted.row_factory = sqlite3.Row
    assert compacted.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert compacted.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
    assert compacted.execute("SELECT COUNT(*) FROM session_events").fetchone()[0] == 1
    assert "file_search_text" not in {row[1] for row in compacted.execute("PRAGMA table_info(session_messages)")}
    tool_message = json.loads(
        compacted.execute("SELECT message_json FROM session_messages WHERE role = 'tool'").fetchone()[0]
    )
    assert tool_message["raw_ref"].startswith("tr_")
    assert large not in tool_message["content"]
    tool_result = compacted.execute("SELECT * FROM tool_results").fetchone()
    assert gzip.decompress(Path(tool_result["blob_path"]).read_bytes()).decode() == large
    assert compacted.execute("SELECT result_ref FROM tool_calls").fetchone()[0] == tool_result["tool_result_id"]
    assert compacted.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0] == 2
    receipt = json.loads(compacted.execute("SELECT payload FROM outbox_events WHERE id = 2").fetchone()[0])
    assert receipt["payload_bytes"] == 10_000
    assert compacted.execute("SELECT payload FROM outbox_events WHERE id = 3").fetchone()[0] == "dead-payload"
    compacted.close()

    assert manifest["events_pruned"] == 4
    assert manifest["legacy_file_search_text_removed"] is True
    assert manifest["outbox"]["payloads_compacted"] == 2
    assert manifest["outbox"]["payload_bytes_removed"] > 19_000
    assert manifest["outbox"]["receipts_pruned"] == 1
    assert manifest["checks"] == {
        "integrity": "ok",
        "row_counts": "ok",
        "user_assistant_prose": "ok",
        "auto_vacuum": "incremental",
    }
    assert Path(manifest["manifest_path"]).is_file()


def test_compactor_apply_keeps_rollback_artifact(tmp_path: Path):
    source = tmp_path / "sessions.db"
    _legacy_database(source)

    manifest = compact_legacy_database(
        source,
        blob_root=tmp_path / "blobs",
        apply=True,
        event_keep=2,
        inline_limit=100,
    )

    rollback = Path(manifest["rollback_path"])
    assert rollback.is_file()
    original = sqlite3.connect(rollback)
    compacted = sqlite3.connect(source)
    assert original.execute("SELECT COUNT(*) FROM session_events").fetchone()[0] == 5
    assert compacted.execute("SELECT COUNT(*) FROM session_events").fetchone()[0] == 2
    original.close()
    compacted.close()
