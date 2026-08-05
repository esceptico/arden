import json

import aiosqlite


def chat_run_payload(row: aiosqlite.Row) -> dict:
    columns = set(row.keys())
    return {
        "run_id": row["run_id"],
        "session_id": row["session_id"],
        "status": row["status"],
        "stop_reason": row["stop_reason"],
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
        "ended_at": row["ended_at"],
        "last_seq": row["last_seq"],
        "metadata": json.loads(row["metadata_json"] or "{}"),
        "error_code": row["error_code"] if "error_code" in columns else None,
        "error_message": row["error_message"] if "error_message" in columns else None,
        "client_id": row["client_id"] if "client_id" in columns else None,
    }


def chat_idempotency_payload(row: aiosqlite.Row) -> dict:
    return {
        "session_id": row["session_id"],
        "client_id": row["client_id"],
        "request_hash": row["request_hash"],
        "run_id": row["run_id"],
        "message_id": row["message_id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "expires_at": row["expires_at"],
    }


def background_agent_payload(row: aiosqlite.Row) -> dict:
    return {
        "task_id": row["task_id"],
        "agent_ref": row["agent_ref"],
        "child_run_id": row["task_id"],
        "child_session_id": row["child_session_id"],
        "session_id": row["session_id"],
        "parent_run_id": row["parent_run_id"],
        "parent_tool_call_id": row["parent_tool_call_id"],
        "suspension_id": row["suspension_id"],
        "agent_type": row["agent_type"] or "background_research",
        "wait": bool(row["wait"]),
        "status": row["status"],
        "command": row["command"],
        "detail": row["detail"],
        "result_ref": row["result_ref"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
        "ended_at": row["ended_at"],
        "cancel_requested_at": row["cancel_requested_at"],
        "cancel_actor": row["cancel_actor"],
        "terminal_cause": row["terminal_cause"],
        "cancel_generation": int(row["cancel_generation"]),
        "cancel_idempotency_key": row["cancel_idempotency_key"],
        "notified_at": row["notified_at"],
        "completion_id": dict(row).get("completion_id"),
    }


def background_agent_event_payload(row: aiosqlite.Row) -> dict:
    return {
        "session_id": row["session_id"],
        "seq": row["seq"],
        "task_id": row["task_id"],
        "status": row["status"],
        "detail": row["detail"],
        "result_ref": row["result_ref"],
        "terminal": bool(row["terminal"]),
        "created_at": row["created_at"],
        "event_id": dict(row).get("event_id"),
        "delivered_at": dict(row).get("delivered_at"),
    }


def tool_call_payload(row: aiosqlite.Row) -> dict:
    columns = set(row.keys())
    return {
        "run_id": row["run_id"],
        "session_id": row["session_id"],
        "tool_call_id": row["tool_call_id"],
        "tool_name": row["tool_name"],
        "action": row["action"],
        "scope": row["scope"],
        "args_hash": row["args_hash"],
        "status": row["status"],
        "result_preview": row["result_preview"],
        "result_ref": row["result_ref"],
        "outcome": json.loads(row["outcome_json"])
        if "outcome_json" in columns and row["outcome_json"]
        else None,
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
    }


def tool_result_payload(row: aiosqlite.Row, *, content: str | None = None) -> dict:
    return {
        "tool_result_id": row["tool_result_id"],
        "session_id": row["session_id"],
        "run_id": row["run_id"],
        "tool_call_id": row["tool_call_id"],
        "tool_name": row["tool_name"],
        "content_sha256": row["content_sha256"],
        "content_bytes": row["content_bytes"],
        "stored_bytes": row["stored_bytes"],
        "compression": row["compression"],
        "blob_ref": row["blob_ref"],
        "blob_path": row["blob_path"],
        "preview": row["preview"],
        "retention_class": row["retention_class"],
        "expires_at": row["expires_at"],
        "source_event_seq": row["source_event_seq"],
        "created_at": row["created_at"],
        "content": content,
    }


def tool_approval_payload(row: aiosqlite.Row) -> dict:
    columns = set(row.keys())
    payload = json.loads(row["payload_json"] or "{}") if "payload_json" in columns else {}
    resolution = json.loads(row["resolution_json"]) if "resolution_json" in columns and row["resolution_json"] else None
    return {
        "run_id": row["run_id"],
        "session_id": row["session_id"],
        "suspension_id": row["tool_call_id"],
        "kind": row["kind"] if "kind" in columns else "tool_approval",
        "payload": payload,
        "resolution": resolution,
        "tool_call_id": row["tool_call_id"],
        "tool_name": row["tool_name"],
        "action": row["action"],
        "scope": row["scope"],
        "preview": row["preview"],
        "diff": row["diff"],
        "status": row["status"],
        "requested_at": row["requested_at"],
        "resolved_at": row["resolved_at"],
        "expires_at": row["expires_at"],
        "result_feedback": row["result_feedback"],
    }
