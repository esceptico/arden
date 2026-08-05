from types import SimpleNamespace

import pytest

from arden.agent import ToolOutcomeStatus, ToolResult
from arden.integrations.base import IntegrationConnectionError
from arden.integrations.calendar.tools import CalendarCreateEventInput, CalendarDeleteEventInput, CalendarEditEventInput
from arden.integrations.gmail.tools import EmailReplyInput, EmailSendInput
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
        EmailReplyInput,
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


def test_calendar_mutations_require_exact_external_refs():
    assert CalendarCreateEventInput.model_fields["account"].is_required()
    assert "event_ref" in CalendarEditEventInput.model_fields
    assert "event_id" not in CalendarEditEventInput.model_fields
    assert "event_ref" in CalendarDeleteEventInput.model_fields
    assert "event_id" not in CalendarDeleteEventInput.model_fields


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


@pytest.mark.asyncio
async def test_failed_provider_mutation_keeps_claim_and_blocks_blind_retry(tmp_path):
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
        return ToolResult.failure(
            code="provider_error",
            message="Provider request failed.",
            preview="Send failed",
            retryable=True,
            recovery_action="Verify provider state before retrying.",
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
    assert first.outcome.status is ToolOutcomeStatus.UNCERTAIN
    assert first.outcome.error.code == "mutation_uncertain"
    assert second.outcome.error.code == "mutation_uncertain"


@pytest.mark.asyncio
async def test_proven_no_effect_failure_releases_claim_for_same_key_retry(tmp_path):
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
        if calls == 1:
            return ToolResult.failure(
                code="rate_limited",
                message="Provider rejected the request.",
                preview="Rate limited",
                retryable=True,
                recovery_action="Wait before retrying.",
            )
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
    replay = await execute_idempotent(
        execution,
        namespace="gmail:send:acct",
        idempotency_key="send-key-1",
        payload={"body": "hello"},
        invoke=invoke,
    )

    assert first.outcome.error.code == "rate_limited"
    assert second.outcome.status is ToolOutcomeStatus.SUCCEEDED
    assert replay.data["idempotent_replay"] is True
    assert calls == 2


@pytest.mark.asyncio
async def test_untyped_mutation_failure_propagates_and_blocks_blind_retry(tmp_path):
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
        raise RuntimeError("provider transport failed")

    with pytest.raises(RuntimeError, match="provider transport failed"):
        await execute_idempotent(
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

    assert second.outcome.status is ToolOutcomeStatus.UNCERTAIN
    assert calls == 1


@pytest.mark.asyncio
async def test_connection_error_releases_claim_and_uses_normal_recovery(tmp_path):
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
        if calls == 1:
            raise IntegrationConnectionError(
                integration_id="gmail",
                reason="auth_required",
                detail="Reconnect Gmail",
            )
        return mutation_result(
            content="sent",
            preview="Sent",
            operation="send",
            target="user@example.test",
            receipt="provider-1",
            after_ref="acct:message-1",
            observed="Provider returned acct:message-1",
        )

    with pytest.raises(IntegrationConnectionError, match="Reconnect Gmail"):
        await execute_idempotent(
            execution,
            namespace="gmail:send:acct",
            idempotency_key="send-key-1",
            payload={"body": "hello"},
            invoke=invoke,
        )
    retry = await execute_idempotent(
        execution,
        namespace="gmail:send:acct",
        idempotency_key="send-key-1",
        payload={"body": "hello"},
        invoke=invoke,
    )

    assert retry.outcome.status is ToolOutcomeStatus.SUCCEEDED
    assert calls == 2


@pytest.mark.asyncio
async def test_denied_mutation_releases_claim_for_same_key_retry(tmp_path):
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
        if calls == 1:
            return ToolResult.failure(
                code="permission_denied",
                message="Mutation was denied before execution.",
                preview="Denied",
                status=ToolOutcomeStatus.DENIED,
            )
        return mutation_result(
            content="sent",
            preview="Sent",
            operation="send",
            target="user@example.test",
            receipt="provider-1",
            after_ref="acct:message-1",
            observed="Provider returned acct:message-1",
        )

    denied = await execute_idempotent(
        execution,
        namespace="gmail:send:acct",
        idempotency_key="send-key-1",
        payload={"body": "hello"},
        invoke=invoke,
    )
    retry = await execute_idempotent(
        execution,
        namespace="gmail:send:acct",
        idempotency_key="send-key-1",
        payload={"body": "hello"},
        invoke=invoke,
    )

    assert denied.outcome.status is ToolOutcomeStatus.DENIED
    assert retry.outcome.status is ToolOutcomeStatus.SUCCEEDED
    assert calls == 2
