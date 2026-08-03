import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

import zstandard

from arden.trajectory.export import export_cold_bundle, verify_cold_bundle


def _source_database(path: Path) -> None:
    raw_result = "result " * 4_000
    raw = raw_result.encode()
    content_sha256 = hashlib.sha256(raw).hexdigest()
    blob_path = path.parent / "blobs" / f"{content_sha256}.txt.gz"
    blob_path.parent.mkdir()
    blob_path.write_bytes(gzip.compress(raw, mtime=0))
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            metadata TEXT,
            chat_model TEXT
        );
        CREATE TABLE session_messages (
            session_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            role TEXT NOT NULL,
            created_at TEXT NOT NULL,
            message_json TEXT NOT NULL
        );
        CREATE TABLE tool_results (
            session_id TEXT NOT NULL,
            tool_call_id TEXT,
            blob_ref TEXT NOT NULL,
            blob_path TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            content_bytes INTEGER NOT NULL,
            compression TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?)",
        ("session-1", "2026-08-03T10:00:00Z", json.dumps({"cwd": "/repo"}), "gpt-test"),
    )
    messages = [
        {"role": "system", "content": "Follow instructions."},
        {"role": "user", "content": "Keep this prose exactly."},
        {
            "role": "assistant",
            "content": "I will inspect it.",
            "reasoning_content": "brief reasoning",
            "tool_calls": [
                {
                    "id": "call-1",
                    "function": {"name": "read", "arguments": json.dumps({"path": "x" * 5_000})},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": raw_result, "ok": True},
        {"role": "assistant", "content": "Done."},
    ]
    for seq, message in enumerate(messages):
        connection.execute(
            "INSERT INTO session_messages VALUES (?, ?, ?, ?, ?)",
            ("session-1", seq, message["role"], f"2026-08-03T10:00:0{seq}Z", json.dumps(message)),
        )
    connection.execute(
        "INSERT INTO tool_results VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "session-1",
            "call-1",
            f"sha256:{content_sha256}",
            str(blob_path),
            content_sha256,
            len(raw),
            "gzip",
            "2026-08-03T10:00:03Z",
        ),
    )
    connection.commit()
    connection.close()


def test_cold_bundle_is_schema_valid_deterministic_and_space_benchmarked(tmp_path: Path):
    database = tmp_path / "sessions.db"
    _source_database(database)
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    first = export_cold_bundle(database, "session-1", first_dir)
    second = export_cold_bundle(database, "session-1", second_dir)
    verify_cold_bundle(first_dir, first)

    assert first["source"]["sha256"] == second["source"]["sha256"]
    assert first["outputs"] == second["outputs"]
    assert first["outputs"]["atif-summary"]["json_bytes"] < first["outputs"]["atif-full"]["json_bytes"]
    assert first["outputs"]["letta-summary"]["json_bytes"] < first["outputs"]["letta-full"]["json_bytes"]
    assert first["blob_refs"][0]["ref"].startswith("sha256:")
    assert first["outputs"]["atif-summary"]["stats"]["clipped_tool_arguments"] == 1
    assert first["outputs"]["letta-summary"]["stats"]["clipped_tool_results"] == 1

    compressed = (first_dir / "session-1.letta.summary.json.zst").read_bytes()
    document = json.loads(zstandard.ZstdDecompressor().decompress(compressed))
    prose = [row["content"] for row in document if row["role"] in {"user", "assistant"} and row["content"]]
    assert prose == ["Keep this prose exactly.", "I will inspect it.", "Done."]
    tool_result = next(row for row in document if row["role"] == "tool")
    assert f"[raw result: {first['blob_refs'][0]['ref']}]" in tool_result["content"]
