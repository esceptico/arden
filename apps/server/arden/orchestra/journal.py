"""Durable journal of a workflow execution's agent spawns.

Records live in the InvocationStore (placement="workflow"), keyed
`wf:{stable_key}:{seq}[:fmt]:{hash16}` — the content hash is part of the id
(the settled design keys the triple (workflow, seq, sha)), so a re-execution
whose arguments changed at some seq simply misses instead of colliding with
the stale record. Non-succeeded terminal records occupy their slot; retries
chain as `...:r1`, `...:r2` so a later successful re-run is still journaled
and replayable instead of a transient failure freezing into a permanent hole.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from arden.core.raw_tool_results import read_raw_tool_result_by_ref
from arden.execution.models import TERMINAL_STATUSES, InvocationConflictError, InvocationRecord, InvocationStatus
from arden.execution.store import InvocationStore
from arden.logging import get_logger

_logger = get_logger(__name__)

PLACEMENT = "workflow"


def spawn_hash(task: str, system_prompt: str | None, model: str | None, agent_type: str | None) -> str:
    material = "\x00".join((task, system_prompt or "", model or "", agent_type or ""))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def formatter_hash(call_hash: str, worker_answer: str, schema_json: str) -> str:
    material = "\x00".join((call_hash, worker_answer, schema_json))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class JournalSlot:
    """Outcome of claiming a journal slot: either a cached text (serve it,
    nothing to run) or an invocation_id to complete after running live."""

    invocation_id: str | None = None
    cached_text: str | None = None


class WorkflowJournal:
    def __init__(
        self,
        store: InvocationStore,
        *,
        key: str,
        run_id: str,
        session_id: str,
        tool_call_id: str,
    ):
        self._store = store
        self._key = key
        self._run_id = run_id
        self._session_id = session_id
        self._tool_call_id = tool_call_id
        # Records created at or after this instant belong to THIS execution
        # (a concurrent double-exec of the same tool call); older succeeded
        # records are the previous execution's prefix and may only be served
        # while prefix replay is still open.
        self._started = datetime.now(UTC)

    def _slot_id(self, seq: int, content_hash: str, retry: int, *, formatter: bool) -> str:
        kind = ":fmt" if formatter else ""
        suffix = f":r{retry}" if retry else ""
        return f"wf:{self._key}:{seq}{kind}:{content_hash[:16]}{suffix}"

    async def begin(
        self,
        seq: int,
        content_hash: str,
        arguments: dict,
        *,
        allow_replay: bool,
        formatter: bool = False,
    ) -> JournalSlot:
        """Claim the journal slot for (seq, hash), walking to the LAST record
        in the retry chain — the latest attempt is the one consistent with the
        rest of the journal (an earlier attempt at this seq may predate a live
        upstream re-run).

        A last-attempt SUCCEEDED record is served from cache when
        `allow_replay` (prefix replay still open) or when it was written by
        this execution (double-exec dedupe); otherwise the next retry slot is
        claimed so the call runs live per prefix semantics. Failed / uncertain
        / cancelled last attempts claim the next slot likewise (transient
        failures re-run, and the re-run's outcome is still journaled). A
        REQUESTED leftover from a crashed attempt is reclaimed in place.
        """
        retry = 0
        record = await self._store.get(self._slot_id(seq, content_hash, 0, formatter=formatter))
        while record is not None:
            nxt = await self._store.get(self._slot_id(seq, content_hash, retry + 1, formatter=formatter))
            if nxt is None:
                break
            retry += 1
            record = nxt
        if record is not None:
            if record.status == InvocationStatus.SUCCEEDED and (allow_replay or record.created_at >= self._started):
                return JournalSlot(cached_text=self._payload_text(record))
            if record.status not in TERMINAL_STATUSES:
                return JournalSlot(invocation_id=record.invocation_id)
            retry += 1
        invocation_id = self._slot_id(seq, content_hash, retry, formatter=formatter)
        record = await self._store.create(
            invocation_id=invocation_id,
            run_id=self._run_id,
            session_id=self._session_id,
            tool_call_id=self._tool_call_id,
            tool_name="workflow.format" if formatter else "workflow.agent",
            placement=PLACEMENT,
            arguments_json=json.dumps(arguments, sort_keys=True, separators=(",", ":")),
        )
        if record.status == InvocationStatus.SUCCEEDED:
            # Settled by a concurrent double-exec between the get and create.
            return JournalSlot(cached_text=self._payload_text(record))
        return JournalSlot(invocation_id=invocation_id)

    async def finish(self, invocation_id: str, text: str) -> None:
        try:
            await self._store.complete(
                invocation_id,
                status=InvocationStatus.SUCCEEDED,
                result_payload=json.dumps({"text": text}),
            )
        except InvocationConflictError:
            # A concurrent double-exec settled this slot first with a different
            # text; either answer is a valid live result — keep theirs durably,
            # serve ours in-process.
            _logger.warning("workflow journal slot %s already settled by a concurrent execution", invocation_id)

    async def fail(self, invocation_id: str, error: str) -> None:
        try:
            await self._store.complete(
                invocation_id,
                status=InvocationStatus.FAILED,
                result_payload=json.dumps({"error": error[:2000]}),
                error_code="workflow_spawn_failed",
            )
        except InvocationConflictError:
            pass

    def _payload_text(self, record: InvocationRecord) -> str:
        payload = record.result_payload
        if payload is None and record.result_blob_ref:
            payload = read_raw_tool_result_by_ref(record.result_blob_ref)
        return str(json.loads(payload or "{}").get("text") or "")
