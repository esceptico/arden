from types import SimpleNamespace

import pytest

from arden.integrations.calendar.tools import CalendarCreateEventInput, CalendarDeleteEventInput, CalendarEditEventInput
from arden.integrations.gmail.tools import EmailSendInput
from arden.integrations.google_drive.tools import (
    DriveCreateDocInput,
    DriveCreateSheetInput,
    DriveEditDocInput,
    SheetWriteInput,
)
from arden.integrations.mutations import (
    IDEMPOTENCY_LEDGER_SERVICE,
    IdempotencyLedger,
    execute_idempotent,
    mutation_result,
)
from arden.integrations.slack.tools import SlackPostBlocksInput, SlackPostMessageInput
from arden.tools.core.context import ToolExecution


def test_external_mutation_inputs_require_idempotency_keys():
    input_models = (
        EmailSendInput,
        CalendarCreateEventInput,
        CalendarEditEventInput,
        CalendarDeleteEventInput,
        SlackPostMessageInput,
        SlackPostBlocksInput,
        DriveCreateDocInput,
        DriveEditDocInput,
        DriveCreateSheetInput,
        SheetWriteInput,
    )
    assert all(model.model_fields["idempotency_key"].is_required() for model in input_models)


@pytest.mark.asyncio
async def test_idempotency_ledger_replays_completed_receipt_without_duplicate_call(tmp_path):
    ledger = IdempotencyLedger(tmp_path / "idempotency.sqlite3")
    execution = ToolExecution(
        tool_id="call-1",
        tool_name="send",
        ctx=SimpleNamespace(services={IDEMPOTENCY_LEDGER_SERVICE: ledger}),
    )
    calls = 0

    async def invoke():
        nonlocal calls
        calls += 1
        return mutation_result(
            content="sent",
            preview="Sent",
            operation="send",
            target="user@example.test",
            receipt="provider-1",
            after_ref="acct:message-1",
            observed="Provider returned acct:message-1",
        )

    first = await execute_idempotent(
        execution,
        namespace="gmail:send:acct",
        idempotency_key="send-key-1",
        payload={"body": "hello"},
        invoke=invoke,
    )
    second = await execute_idempotent(
        execution,
        namespace="gmail:send:acct",
        idempotency_key="send-key-1",
        payload={"body": "hello"},
        invoke=invoke,
    )

    assert calls == 1
    assert first.outcome.receipt == "provider-1"
    assert second.outcome.receipt == "provider-1"
    assert second.data["idempotent_replay"] is True


@pytest.mark.asyncio
async def test_idempotency_key_rejects_different_payload(tmp_path):
    ledger = IdempotencyLedger(tmp_path / "idempotency.sqlite3")
    execution = ToolExecution(
        tool_id="call-1",
        tool_name="send",
        ctx=SimpleNamespace(services={IDEMPOTENCY_LEDGER_SERVICE: ledger}),
    )

    async def invoke():
        return mutation_result(
            content="sent",
            preview="Sent",
            operation="send",
            target="user@example.test",
            receipt="provider-1",
            after_ref="acct:message-1",
            observed="Provider returned acct:message-1",
        )

    await execute_idempotent(
        execution, namespace="gmail:send:acct", idempotency_key="send-key-1", payload={"body": "one"}, invoke=invoke
    )
    conflict = await execute_idempotent(
        execution, namespace="gmail:send:acct", idempotency_key="send-key-1", payload={"body": "two"}, invoke=invoke
    )

    assert conflict.is_error
    assert conflict.outcome.error.code == "idempotency_conflict"
