from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from arden.execution.models import InvocationRecord, InvocationStatus
from arden.execution.results import (
    DeviceResultEnvelope,
    InvocationResultPayloadError,
    result_payload,
    validate_device_result,
)


def _record(payload: str | None, *, status: InvocationStatus = InvocationStatus.SUCCEEDED) -> InvocationRecord:
    now = datetime.now(UTC)
    return InvocationRecord(
        invocation_id="inv-1",
        run_id="run-1",
        session_id="session-1",
        tool_call_id="call-1",
        tool_name="file_read",
        placement="client",
        arguments_json="{}",
        status=status,
        attempts=1,
        cancel_requested=False,
        created_at=now,
        updated_at=now,
        result_payload=payload,
    )


@pytest.mark.parametrize(
    "payload",
    (
        "not-json",
        "{}",
        '{"content":"ok","preview":"Done","unknown":true}',
        '{"content":1,"preview":"Done"}',
        '{"content":"ok","preview":"Done","observations":[{"id":"file:1","content_read":"yes"}]}',
    ),
)
def test_device_result_payload_rejects_malformed_or_coerced_data(payload: str):
    with pytest.raises(InvocationResultPayloadError, match="invalid"):
        result_payload(_record(payload))


def test_device_result_envelope_validates_nested_sources_effects_and_json_data():
    with pytest.raises(ValidationError):
        DeviceResultEnvelope.model_validate(
            {
                "content": "ok",
                "preview": "Done",
                "source_refs": (
                    {
                        "provider": "filesystem",
                        "kind": "file",
                        "ref": " bad ",
                        "title": "bad",
                    },
                ),
            }
        )
    with pytest.raises(ValidationError):
        DeviceResultEnvelope(content="ok", preview="Done", data={"value": float("nan")})
    with pytest.raises(ValidationError, match="nesting limit"):
        nested: object = "leaf"
        for _ in range(17):
            nested = {"next": nested}
        DeviceResultEnvelope(content="ok", preview="Done", data={"root": nested})
    with pytest.raises(ValidationError, match="string_too_long"):
        DeviceResultEnvelope(content="x" * 1_100_001, preview="Done")


def test_device_result_status_contract_is_explicit():
    success = DeviceResultEnvelope(
        content="wrote file",
        preview="Wrote",
        effect={"operation": "write", "target": "/tmp/file"},
    )
    validate_device_result(InvocationStatus.SUCCEEDED, success, None)

    with pytest.raises(ValueError, match="error_code"):
        validate_device_result(InvocationStatus.FAILED, success, None)
    with pytest.raises(ValueError, match="effect"):
        validate_device_result(InvocationStatus.FAILED, success, "write_failed")
    with pytest.raises(ValueError, match="terminal"):
        validate_device_result(InvocationStatus.RUNNING, success, None)


def test_terminal_device_result_without_inline_or_blob_payload_is_corrupt():
    with pytest.raises(InvocationResultPayloadError, match="no result payload"):
        result_payload(_record(None))
