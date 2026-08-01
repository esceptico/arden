import json

from arden.agent import ToolOutcomeStatus, ToolResult
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


def tool_result_from_record(record: InvocationRecord) -> ToolResult:
    """Rebuild the bounded ToolResult projection from a terminal invocation."""
    try:
        payload = json.loads(_payload_text(record))
    except (json.JSONDecodeError, OSError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {"content": str(payload)}
    content = str(payload.get("content", ""))
    preview = str(payload.get("preview", "")) or content[:80]

    if record.status == InvocationStatus.SUCCEEDED:
        data = payload.get("data")
        return ToolResult(
            content=content,
            preview=preview,
            data=data if isinstance(data, dict) else None,
        ).with_default_outcome()

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
