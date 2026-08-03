import gzip
import hashlib
import json
import math
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import jsonschema
import zstandard
from atif import Trajectory

LETTA_SCHEMA_COMMIT = "59c0db52cc1521efc7fb5d8c7cccf48ee4afcf32"
ATIF_SCHEMA_VERSION = "ATIF-v1.7"
LETTA_SCHEMA_VERSION = "trajectory-v1"
SUMMARY_ARGUMENT_CHARS = 2_000
SUMMARY_RESULT_CHARS = 2_500


@dataclass(frozen=True)
class TranscriptRow:
    seq: int
    role: str
    created_at: str
    message: dict[str, Any]
    message_json: str


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _timestamp(value: Any, fallback: str) -> str:
    raw = str(value or fallback)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.fromisoformat(fallback.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _clip(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    head = limit * 3 // 5
    tail = limit - head
    removed = len(value) - limit
    return f"{value[:head]}\n… [{removed} chars omitted] …\n{value[-tail:]}", True


def _tool_calls(message: dict[str, Any], profile: Literal["full", "summary"]) -> tuple[list[dict], int]:
    normalized: list[dict] = []
    clipped = 0
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list):
        return normalized, clipped
    for index, call in enumerate(raw_calls):
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        call_id = str(call.get("id") or call.get("tool_call_id") or f"tool-call-{index + 1}")
        name = str(function.get("name") or call.get("name") or "unknown")
        args = function.get("arguments", call.get("arguments", call.get("args", {})))
        if isinstance(args, str):
            args_string = args
            try:
                args_object = json.loads(args)
            except json.JSONDecodeError:
                args_object = {"_raw": args}
        else:
            args_object = args if isinstance(args, dict) else {"value": args}
            args_string = json.dumps(args_object, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if profile == "summary":
            args_string, was_clipped = _clip(args_string, SUMMARY_ARGUMENT_CHARS)
            if was_clipped:
                args_object = {"_summary": args_string}
                clipped += 1
        normalized.append({"id": call_id, "name": name, "args": args_string, "args_object": args_object})
    return normalized, clipped


def _load_letta_schema() -> dict[str, Any]:
    path = Path(__file__).with_name("schemas") / "trajectory-v1.schema.json"
    return json.loads(path.read_text())


def _read_source(
    db_path: Path, session_id: str
) -> tuple[dict, list[TranscriptRow], dict[str, str], list[dict[str, Any]]]:
    uri = f"file:{quote(str(db_path.resolve()))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        session = connection.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if session is None:
            raise ValueError(f"session not found: {session_id}")
        rows = connection.execute(
            """
            SELECT seq, role, created_at, message_json
            FROM session_messages
            WHERE session_id = ?
            ORDER BY seq ASC
            """,
            (session_id,),
        ).fetchall()
        columns = {row[1] for row in connection.execute("PRAGMA table_info(tool_results)").fetchall()}
        raw_refs: dict[str, str] = {}
        blob_inventory: list[dict[str, Any]] = []
        if {
            "session_id",
            "tool_call_id",
            "blob_ref",
            "blob_path",
            "content_sha256",
            "content_bytes",
            "compression",
        } <= columns:
            for row in connection.execute(
                """
                SELECT tool_call_id, blob_ref, blob_path, content_sha256, content_bytes, compression
                FROM tool_results
                WHERE session_id = ? AND tool_call_id IS NOT NULL
                ORDER BY created_at ASC
                """,
                (session_id,),
            ).fetchall():
                raw_refs[str(row["tool_call_id"])] = str(row["blob_ref"])
                blob_inventory.append(
                    {
                        "ref": str(row["blob_ref"]),
                        "path": str(row["blob_path"]),
                        "sha256": str(row["content_sha256"]),
                        "bytes": int(row["content_bytes"]),
                        "compression": str(row["compression"]),
                    }
                )
        elif {"session_id", "tool_call_id", "storage_ref"} <= columns:
            for row in connection.execute(
                "SELECT tool_call_id, storage_ref FROM tool_results WHERE session_id = ?",
                (session_id,),
            ).fetchall():
                raw_refs[str(row["tool_call_id"])] = str(row["storage_ref"])
    finally:
        connection.close()
    transcript: list[TranscriptRow] = []
    for row in rows:
        try:
            message = json.loads(row["message_json"])
        except (TypeError, json.JSONDecodeError):
            message = {"role": row["role"], "content": str(row["message_json"])}
        transcript.append(
            TranscriptRow(
                seq=int(row["seq"]),
                role=str(row["role"]),
                created_at=str(row["created_at"]),
                message=message,
                message_json=str(row["message_json"]),
            )
        )
    return dict(session), transcript, raw_refs, blob_inventory


def _atif_document(
    session: dict,
    rows: list[TranscriptRow],
    raw_refs: dict[str, str],
    profile: Literal["full", "summary"],
) -> tuple[dict, dict]:
    started_at = str(session["started_at"])
    steps: list[dict] = []
    stats: Counter[str] = Counter()
    pending_observations: dict[str, list[dict]] = {}
    for row in rows:
        message = row.message
        role = str(message.get("role") or row.role)
        timestamp = _timestamp(message.get("created_at") or row.created_at, started_at)
        content = _text(message.get("content"))
        if role in {"system", "user"}:
            steps.append({"step_id": len(steps) + 1, "timestamp": timestamp, "source": role, "message": content})
            continue
        if role == "assistant":
            calls, clipped_args = _tool_calls(message, profile)
            stats["clipped_tool_arguments"] += clipped_args
            step: dict[str, Any] = {
                "step_id": len(steps) + 1,
                "timestamp": timestamp,
                "source": "agent",
                "message": content,
            }
            reasoning = _text(message.get("reasoning_content") or message.get("reasoning"))
            if reasoning:
                step["reasoning_content"] = reasoning
            if calls:
                step["tool_calls"] = [
                    {
                        "tool_call_id": call["id"],
                        "function_name": call["name"],
                        "arguments": call["args_object"],
                    }
                    for call in calls
                ]
                for call in calls:
                    pending_observations[call["id"]] = []
                step["observation"] = {"results": []}
            steps.append(step)
            continue
        if role == "tool":
            call_id = str(message.get("tool_call_id") or "unknown")
            raw_ref = raw_refs.get(call_id) or message.get("raw_ref")
            if profile == "summary":
                content, was_clipped = _clip(content, SUMMARY_RESULT_CHARS)
                stats["clipped_tool_results"] += int(was_clipped)
            if raw_ref:
                marker = f"[raw result: {raw_ref}]"
                if marker not in content:
                    content = f"{content}\n{marker}" if content else marker
            result: dict[str, Any] = {"source_call_id": call_id, "content": content}
            if raw_ref:
                result["extra"] = {"raw_ref": str(raw_ref)}
                stats["raw_blob_refs"] += 1
            matched = False
            for step in reversed(steps):
                call_ids = {call["tool_call_id"] for call in step.get("tool_calls", [])}
                if call_id in call_ids:
                    step["observation"]["results"].append(result)
                    matched = True
                    break
            if not matched:
                steps.append(
                    {
                        "step_id": len(steps) + 1,
                        "timestamp": timestamp,
                        "source": "agent",
                        "message": "",
                        "observation": {"results": [result]},
                        "llm_call_count": 0,
                    }
                )
                stats["orphan_tool_results"] += 1
            continue
        stats[f"omitted_role_{role or 'empty'}"] += 1
    metadata = json.loads(session.get("metadata") or "{}") if session.get("metadata") else {}
    document = {
        "schema_version": ATIF_SCHEMA_VERSION,
        "session_id": session["session_id"],
        "trajectory_id": f"arden:{session['session_id']}:{profile}",
        "agent": {
            "name": "arden",
            "version": "1",
            "model_name": session.get("chat_model"),
            "extra": {"profile": profile},
        },
        "steps": steps,
        "extra": {
            "source": "arden-session-messages",
            "cwd": metadata.get("cwd"),
            "cold_storage_profile": profile,
        },
    }
    validated = Trajectory.model_validate(document)
    return validated.model_dump(mode="json", exclude_none=True), dict(stats)


def _letta_document(
    session: dict,
    rows: list[TranscriptRow],
    raw_refs: dict[str, str],
    profile: Literal["full", "summary"],
) -> tuple[list[dict], dict]:
    started_at = str(session["started_at"])
    metadata = json.loads(session.get("metadata") or "{}") if session.get("metadata") else {}
    result: list[dict] = [{"role": "meta", "source": "arden"}]
    if metadata.get("cwd"):
        result[0]["cwd"] = str(metadata["cwd"])
    if session.get("chat_model"):
        result[0]["model"] = str(session["chat_model"])
    stats: Counter[str] = Counter()
    for row in rows:
        message = row.message
        role = str(message.get("role") or row.role)
        timestamp = _timestamp(message.get("created_at") or row.created_at, started_at)
        content = _text(message.get("content"))
        if role == "user":
            result.append({"role": "user", "content": content, "timestamp": timestamp})
        elif role == "assistant":
            reasoning = _text(message.get("reasoning_content") or message.get("reasoning"))
            if reasoning:
                result.append({"role": "reasoning", "content": reasoning, "timestamp": timestamp})
            calls, clipped_args = _tool_calls(message, profile)
            stats["clipped_tool_arguments"] += clipped_args
            if content:
                result.append({"role": "assistant", "content": content, "timestamp": timestamp})
            if calls:
                result.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "timestamp": timestamp,
                        "tool_calls": [
                            {"id": call["id"], "name": call["name"], "args": call["args"]} for call in calls
                        ],
                    }
                )
            elif not content:
                stats["omitted_empty_assistant"] += 1
        elif role == "tool":
            call_id = str(message.get("tool_call_id") or "unknown")
            raw_ref = raw_refs.get(call_id) or message.get("raw_ref")
            if profile == "summary":
                content, was_clipped = _clip(content, SUMMARY_RESULT_CHARS)
                stats["clipped_tool_results"] += int(was_clipped)
            if raw_ref:
                marker = f"[raw result: {raw_ref}]"
                if marker not in content:
                    content = f"{content}\n{marker}" if content else marker
                stats["raw_blob_refs"] += 1
            record = {"role": "tool", "tool_call_id": call_id, "content": content, "timestamp": timestamp}
            if isinstance(message.get("ok"), bool):
                record["ok"] = message["ok"]
            result.append(record)
        else:
            stats[f"omitted_role_{role or 'empty'}"] += 1
    jsonschema.Draft202012Validator(_load_letta_schema()).validate(result)
    return result, dict(stats)


def _prose_digest(rows: list[TranscriptRow]) -> str:
    prose = [
        [str(row.message.get("role") or row.role), _text(row.message.get("content"))]
        for row in rows
        if str(row.message.get("role") or row.role) in {"user", "assistant"} and _text(row.message.get("content"))
    ]
    return _sha256(_canonical_json(prose))


def _output_prose_digest(format_name: str, document: Any) -> str:
    if format_name == "atif":
        prose = [
            ["assistant" if step["source"] == "agent" else "user", _text(step.get("message"))]
            for step in document["steps"]
            if step["source"] in {"user", "agent"} and _text(step.get("message"))
        ]
    else:
        prose = [
            [record["role"], _text(record.get("content"))]
            for record in document
            if record["role"] in {"user", "assistant"} and _text(record.get("content"))
        ]
    return _sha256(_canonical_json(prose))


def export_cold_bundle(db_path: Path, session_id: str, output_dir: Path) -> dict[str, Any]:
    """Create deterministic compressed sidecars without mutating the source DB."""
    session, rows, raw_refs, blob_inventory = _read_source(db_path, session_id)
    if not rows:
        raise ValueError(f"session has no durable transcript rows: {session_id}")
    output_dir.mkdir(parents=True, exist_ok=True)
    source_material = _canonical_json(
        [[row.seq, row.role, row.created_at, json.loads(row.message_json)] for row in rows]
    )
    compressor = zstandard.ZstdCompressor(level=10, write_checksum=True, write_content_size=True)
    manifest: dict[str, Any] = {
        "format_version": "arden-cold-bundle-v1",
        "session_id": session_id,
        "source": {
            "database": str(db_path.resolve()),
            "message_count": len(rows),
            "max_seq": max(row.seq for row in rows),
            "sha256": _sha256(source_material),
            "canonical_bytes": sum(len(row.message_json.encode()) for row in rows),
            "user_assistant_prose_sha256": _prose_digest(rows),
        },
        "schemas": {
            "atif": {"version": ATIF_SCHEMA_VERSION, "validator": "atif==1.7.0a0"},
            "letta": {"version": LETTA_SCHEMA_VERSION, "commit": LETTA_SCHEMA_COMMIT},
        },
        "blob_refs": sorted({blob["ref"]: blob for blob in blob_inventory}.values(), key=lambda blob: blob["ref"]),
        "outputs": {},
    }
    for format_name, builder in (("atif", _atif_document), ("letta", _letta_document)):
        for profile in ("full", "summary"):
            document, stats = builder(session, rows, raw_refs, profile)
            output_prose_digest = _output_prose_digest(format_name, document)
            if output_prose_digest != manifest["source"]["user_assistant_prose_sha256"]:
                raise ValueError(f"user/assistant prose or message order changed in {format_name}-{profile}")
            payload = _canonical_json(document)
            compressed = compressor.compress(payload)
            filename = f"{session_id}.{format_name}.{profile}.json.zst"
            path = output_dir / filename
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_bytes(compressed)
            temporary.replace(path)
            key = f"{format_name}-{profile}"
            manifest["outputs"][key] = {
                "file": filename,
                "sha256": _sha256(compressed),
                "json_sha256": _sha256(payload),
                "json_bytes": len(payload),
                "compressed_bytes": len(compressed),
                "estimated_tokens": math.ceil(len(payload.decode()) / 4),
                "searchable_chars": sum(
                    len(_text(row.message.get("content")))
                    for row in rows
                    if str(row.message.get("role") or row.role) in {"user", "assistant"}
                ),
                "compression_ratio": round(len(compressed) / len(payload), 4) if payload else 0,
                "stats": stats,
                "user_assistant_prose_sha256": output_prose_digest,
            }
    manifest_bytes = _canonical_json(manifest) + b"\n"
    manifest_path = output_dir / f"{session_id}.manifest.json"
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_bytes(manifest_bytes)
    temporary.replace(manifest_path)
    return manifest


def verify_cold_bundle(output_dir: Path, manifest: dict[str, Any]) -> None:
    decompressor = zstandard.ZstdDecompressor()
    for blob in manifest.get("blob_refs", []):
        raw = Path(blob["path"]).read_bytes()
        if blob["compression"] == "gzip":
            raw = gzip.decompress(raw)
        if len(raw) != blob["bytes"] or _sha256(raw) != blob["sha256"]:
            raise ValueError(f"raw blob integrity mismatch: {blob['ref']}")
    for key, output in manifest["outputs"].items():
        compressed = (output_dir / output["file"]).read_bytes()
        if _sha256(compressed) != output["sha256"]:
            raise ValueError(f"compressed hash mismatch: {key}")
        payload = decompressor.decompress(compressed)
        if _sha256(payload) != output["json_sha256"]:
            raise ValueError(f"JSON hash mismatch: {key}")
        document = json.loads(payload)
        if key.startswith("atif-"):
            Trajectory.model_validate(document)
        else:
            jsonschema.Draft202012Validator(_load_letta_schema()).validate(document)
