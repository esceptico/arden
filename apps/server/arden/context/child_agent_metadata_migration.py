"""One-way rewrite of persisted child-agent receipts to public references."""

import json
from collections.abc import Callable
from dataclasses import dataclass

import aiosqlite
from pydantic import ValidationError

from arden.context.errors import SessionDataCorruptionError
from arden.core.tool_result_data import PersistedChildAgent

StrictProjection = Callable[[str, object], list[dict]]

_INTERNAL_FIELDS = {
    "child_run_id",
    "child_session_id",
    "parent_tool_call_id",
    "agent_type",
    "wait",
    "status",
    "tool_call_ids",
}
_REQUIRED_INTERNAL_FIELDS = {"child_run_id", "agent_type", "wait", "status"}


@dataclass(frozen=True, slots=True)
class _SessionIdentity:
    public_ref: str
    parent_session_id: str | None
    parent_tool_call_id: str | None


@dataclass(frozen=True, slots=True)
class _RunIdentity:
    agent_ref: str
    child_session_id: str | None
    parent_tool_call_id: str | None


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


def _read_object(raw: object, *, location: str) -> dict:
    try:
        value = json.loads(raw, parse_constant=_reject_nonfinite)
    except (TypeError, ValueError) as exc:
        raise SessionDataCorruptionError(f"{location} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise SessionDataCorruptionError(f"{location} must be an object")
    return value


def _nonempty_string(value: object, *, field: str, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise SessionDataCorruptionError(f"{location} has an invalid {field}")
    return value


def _optional_string(value: object, *, field: str, location: str) -> str | None:
    if value is None:
        return None
    return _nonempty_string(value, field=field, location=location)


def _validate_internal_receipt(child: dict, *, location: str) -> None:
    missing = _REQUIRED_INTERNAL_FIELDS - set(child)
    unknown = set(child) - _INTERNAL_FIELDS
    if missing:
        raise SessionDataCorruptionError(f"{location} is missing fields: {sorted(missing)}")
    if unknown:
        raise SessionDataCorruptionError(f"{location} has unknown fields: {sorted(unknown)}")
    _nonempty_string(child["child_run_id"], field="child_run_id", location=location)
    _optional_string(child.get("child_session_id"), field="child_session_id", location=location)
    _optional_string(child.get("parent_tool_call_id"), field="parent_tool_call_id", location=location)
    _nonempty_string(child["agent_type"], field="agent_type", location=location)
    if not isinstance(child["wait"], bool):
        raise SessionDataCorruptionError(f"{location} has an invalid wait value")
    _nonempty_string(child["status"], field="status", location=location)
    tool_call_ids = child.get("tool_call_ids")
    if tool_call_ids is not None and (
        not isinstance(tool_call_ids, list)
        or any(not isinstance(tool_call_id, str) or not tool_call_id for tool_call_id in tool_call_ids)
    ):
        raise SessionDataCorruptionError(f"{location} has invalid tool_call_ids")


def _validated_public_receipt(value: object, *, location: str) -> dict:
    try:
        receipt = PersistedChildAgent.model_validate(value)
    except ValidationError as exc:
        raise SessionDataCorruptionError(f"{location} is invalid: {exc}") from exc
    return receipt.model_dump(mode="json", exclude_none=True)


def _rewrite_receipt(
    child: object,
    *,
    owner_session_id: str,
    sessions: dict[str, _SessionIdentity],
    runs: dict[tuple[str, str], _RunIdentity],
    location: str,
) -> dict:
    if not isinstance(child, dict):
        raise SessionDataCorruptionError(f"{location} must be an object")
    if "agent_ref" in child:
        return _validated_public_receipt(child, location=location)

    _validate_internal_receipt(child, location=location)
    run_id = child["child_run_id"]
    receipt_session_id = child.get("child_session_id")
    receipt_call_id = child.get("parent_tool_call_id")
    run = runs.get((owner_session_id, run_id))

    if run is not None and receipt_call_id is not None and run.parent_tool_call_id != receipt_call_id:
        raise SessionDataCorruptionError(f"{location} conflicts with its background run")
    if run is not None and receipt_session_id is not None and run.child_session_id != receipt_session_id:
        raise SessionDataCorruptionError(f"{location} conflicts with its background run child session")

    child_session_id = receipt_session_id or (run.child_session_id if run is not None else None)
    child_session = sessions.get(child_session_id) if child_session_id is not None else None
    if child_session_id is not None and child_session is None:
        raise SessionDataCorruptionError(f"{location} owns a missing child session")
    if child_session is not None:
        if child_session.parent_session_id != owner_session_id:
            raise SessionDataCorruptionError(f"{location} child session belongs to another parent")
        if receipt_call_id is not None and child_session.parent_tool_call_id != receipt_call_id:
            raise SessionDataCorruptionError(f"{location} conflicts with its child session")

    session_ref = child_session.public_ref if child_session is not None else None
    agent_ref = run.agent_ref if run is not None else session_ref
    if agent_ref is None:
        raise SessionDataCorruptionError(f"{location} cannot be resolved to a public agent reference")
    if session_ref is not None and session_ref != agent_ref:
        raise SessionDataCorruptionError(f"{location} has conflicting public agent references")

    return _validated_public_receipt(
        {
            "agent_ref": agent_ref,
            "session_ref": session_ref,
            "agent_type": child["agent_type"],
            "wait": child["wait"],
            "status": child["status"],
        },
        location=location,
    )


def _rewrite_payload(
    payload: dict,
    *,
    owner_session_id: str,
    sessions: dict[str, _SessionIdentity],
    runs: dict[tuple[str, str], _RunIdentity],
    location: str,
) -> bool:
    data = payload.get("data")
    if not isinstance(data, dict) or "child_agent" not in data:
        return False
    rewritten = _rewrite_receipt(
        data["child_agent"],
        owner_session_id=owner_session_id,
        sessions=sessions,
        runs=runs,
        location=f"{location} child_agent",
    )
    changed = data["child_agent"] != rewritten
    data["child_agent"] = rewritten
    return changed


async def rewrite_child_agent_metadata(
    conn: aiosqlite.Connection,
    *,
    strict_active_projection: StrictProjection,
) -> None:
    """Replace persisted runtime IDs in every durable history surface."""

    session_identities: dict[str, _SessionIdentity] = {}
    session_rows = await conn.execute_fetchall(
        "SELECT session_id, public_ref, parent_session_id, parent_tool_call_id FROM sessions"
    )
    for row in session_rows:
        session_id = _nonempty_string(row["session_id"], field="session_id", location="sessions row")
        session_identities[session_id] = _SessionIdentity(
            public_ref=_nonempty_string(row["public_ref"], field="public_ref", location=f"session {session_id}"),
            parent_session_id=_optional_string(
                row["parent_session_id"], field="parent_session_id", location=f"session {session_id}"
            ),
            parent_tool_call_id=_optional_string(
                row["parent_tool_call_id"], field="parent_tool_call_id", location=f"session {session_id}"
            ),
        )

    run_identities: dict[tuple[str, str], _RunIdentity] = {}
    run_rows = await conn.execute_fetchall(
        "SELECT session_id, task_id, agent_ref, child_session_id, parent_tool_call_id FROM background_agent_runs"
    )
    for row in run_rows:
        session_id = _nonempty_string(row["session_id"], field="session_id", location="background agent row")
        task_id = _nonempty_string(row["task_id"], field="task_id", location=f"session {session_id} background agent")
        location = f"session {session_id} background agent {task_id}"
        run_identities[(session_id, task_id)] = _RunIdentity(
            agent_ref=_nonempty_string(row["agent_ref"], field="agent_ref", location=location),
            child_session_id=_optional_string(row["child_session_id"], field="child_session_id", location=location),
            parent_tool_call_id=_optional_string(
                row["parent_tool_call_id"], field="parent_tool_call_id", location=location
            ),
        )

    projection_rows = await conn.execute_fetchall(
        "SELECT session_id, messages FROM sessions WHERE instr(messages, '\"child_agent\"') > 0"
    )
    for row in projection_rows:
        session_id = str(row["session_id"])
        messages = strict_active_projection(session_id, row["messages"])
        changed = False
        for index, message in enumerate(messages):
            changed |= _rewrite_payload(
                message,
                owner_session_id=session_id,
                sessions=session_identities,
                runs=run_identities,
                location=f"session {session_id} messages[{index}]",
            )
        if changed:
            await conn.execute(
                "UPDATE sessions SET messages = ? WHERE session_id = ?",
                (json.dumps(messages, ensure_ascii=False, separators=(",", ":")), session_id),
            )

    transcript_rows = await conn.execute_fetchall(
        "SELECT rowid, session_id, message_id, message_json FROM session_messages "
        "WHERE instr(message_json, '\"child_agent\"') > 0"
    )
    for row in transcript_rows:
        location = f"session {row['session_id']} transcript message {row['message_id']}"
        message = _read_object(row["message_json"], location=location)
        if _rewrite_payload(
            message,
            owner_session_id=str(row["session_id"]),
            sessions=session_identities,
            runs=run_identities,
            location=location,
        ):
            await conn.execute(
                "UPDATE session_messages SET message_json = ? WHERE rowid = ?",
                (json.dumps(message, ensure_ascii=False, separators=(",", ":")), row["rowid"]),
            )

    event_rows = await conn.execute_fetchall(
        "SELECT rowid, session_id, seq, event_json FROM session_events WHERE instr(event_json, '\"child_agent\"') > 0"
    )
    for row in event_rows:
        location = f"session {row['session_id']} event {row['seq']}"
        event = _read_object(row["event_json"], location=location)
        if _rewrite_payload(
            event,
            owner_session_id=str(row["session_id"]),
            sessions=session_identities,
            runs=run_identities,
            location=location,
        ):
            await conn.execute(
                "UPDATE session_events SET event_json = ? WHERE rowid = ?",
                (json.dumps(event, ensure_ascii=False, separators=(",", ":")), row["rowid"]),
            )
