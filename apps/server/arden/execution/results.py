import json

from arden.agent import ToolOutcomeStatus, ToolResult
from arden.agent.types.tools import ToolEffect, ToolOutcome, ToolSourceRef, normalize_source_refs
from arden.core.raw_tool_results import RAW_TOOL_RESULTS_BASE, read_raw_tool_result
from arden.execution.models import InvocationRecord, InvocationStatus

_STATUS_TO_OUTCOME = {
    InvocationStatus.FAILED: ToolOutcomeStatus.FAILED,
    InvocationStatus.CANCELLED: ToolOutcomeStatus.UNCERTAIN,
    InvocationStatus.UNCERTAIN: ToolOutcomeStatus.UNCERTAIN,
}


def _payload_text(record: InvocationRecord) -> str:
    if record.result_payload is not None:
        return record.result_payload
    assert record.result_sha256 is not None
    blob_path = RAW_TOOL_RESULTS_BASE / record.result_sha256[:2] / f"{record.result_sha256}.txt.gz"
    return read_raw_tool_result(str(blob_path))


def result_payload(record: InvocationRecord) -> dict:
    """The device's bounded result projection, parsed; {} when unparseable."""
    try:
        payload = json.loads(_payload_text(record))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {"content": str(payload)}


def _source_refs(payload: dict) -> tuple[ToolSourceRef, ...]:
    raw = payload.get("source_refs")
    if not isinstance(raw, list):
        return ()
    refs = []
    for item in raw:
        if not isinstance(item, dict) or "provider" not in item or "kind" not in item or "ref" not in item:
            continue
        refs.append(
            ToolSourceRef(
                provider=str(item["provider"]),
                kind=str(item["kind"]),
                ref=str(item["ref"]),
                title=str(item["title"]) if item.get("title") is not None else None,
            )
        )
    return normalize_source_refs(tuple(refs))


def _success_outcome(payload: dict) -> ToolOutcome:
    effect = payload.get("effect")
    if isinstance(effect, dict) and effect.get("operation") and effect.get("target"):
        return ToolOutcome(
            status=ToolOutcomeStatus.SUCCEEDED,
            effect=ToolEffect(operation=str(effect["operation"]), target=str(effect["target"])),
        )
    return ToolOutcome(status=ToolOutcomeStatus.SUCCEEDED)


def tool_result_from_payload(record: InvocationRecord, payload: dict) -> ToolResult:
    """Rebuild the bounded ToolResult projection from a terminal invocation."""
    content = str(payload.get("content", ""))
    preview = str(payload.get("preview", "")) or content[:80]

    if record.status == InvocationStatus.SUCCEEDED:
        data = payload.get("data")
        return ToolResult(
            content=content,
            preview=preview,
            data=data if isinstance(data, dict) else None,
            source_refs=_source_refs(payload),
            outcome=_success_outcome(payload),
        )

    return ToolResult.failure(
        code=record.error_code or "tool_error",
        message=content or f"Client execution ended as {record.status}.",
        preview=preview or record.status,
        status=_STATUS_TO_OUTCOME[record.status],
        retryable=record.status == InvocationStatus.FAILED,
        recovery_action=(
            "Verify the device-side state before retrying."
            if record.status != InvocationStatus.FAILED
            else "Retry once the device executor is available."
        ),
    )


def tool_result_from_record(record: InvocationRecord) -> ToolResult:
    return tool_result_from_payload(record, result_payload(record))
