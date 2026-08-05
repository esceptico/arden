import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from judgeval import Tracer

from arden import logging
from arden.agent import ToolMeta, ToolOutcome, ToolOutcomeStatus, ToolResult
from arden.agent.ledger import SharedLedger, access_key, format_arguments
from arden.agent.types.tool_presentation import tool_presentation
from arden.agent.types.tools import ToolSourceRef
from arden.constants import (
    DEFAULT_EXTERNAL_TOOL_TIMEOUT_SECONDS,
    OFFLOAD_PREVIEW_CHARS,
    OFFLOAD_PREVIEW_LINES,
    OFFLOAD_THRESHOLD,
    is_external_source,
)
from arden.core.raw_tool_results import (
    internal_blob_from_data,
    persist_raw_tool_result,
    read_raw_tool_result,
    strip_internal_raw_tool_result_data,
)
from arden.core.tool_result_files import persist_result, persist_result_payload
from arden.integrations.base import IntegrationConnectionError
from arden.tool_call_metadata import split_tool_arguments
from arden.tools.connections import ConnectionService
from arden.tools.core.context import ToolContext, ToolExecution
from arden.tools.core.execution import ToolInvocation
from arden.tools.core.types import ToolAction, ToolScope
from arden.tools.deferred import is_deferred_tool
from arden.tools.executor import ToolExecutor

# The live agent-status listing must not be deduped by SharedLedger: a second
# caller needs the CURRENT state, not the first caller's snapshot. read_session
# stays deduped — it is a cursor-keyed transcript read.
LIVE_READ_TOOLS = frozenset({"session_list"})
AUDIT_PREVIEW_MAX_CHARS = 500
OFFLOAD_CONTINUATION_FIELDS = frozenset({"has_more", "next_cursor", "next_offset"})
OFFLOAD_DATA_SUMMARY_MAX_KEYS = 50
OFFLOAD_DATA_KEY_MAX_BYTES = 128
OFFLOAD_OUTCOME_FIELD_MAX_BYTES = 512
OFFLOAD_CONTINUATION_VALUE_MAX_BYTES = 4_096
_logger = logging.get_logger(__name__)


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    marker = "..."
    return encoded[: max_bytes - len(marker)].decode("utf-8", errors="ignore").rstrip() + marker


def _bounded_optional(value: str | None, max_bytes: int = OFFLOAD_OUTCOME_FIELD_MAX_BYTES) -> str | None:
    return _truncate_utf8(value, max_bytes) if value else None


def _compact_outcome(outcome: ToolOutcome | None) -> ToolOutcome | None:
    if outcome is None:
        return None
    error = (
        replace(
            outcome.error,
            code=_truncate_utf8(outcome.error.code, OFFLOAD_DATA_KEY_MAX_BYTES),
            recovery_action=_bounded_optional(outcome.error.recovery_action),
            diagnostic_ref=_bounded_optional(outcome.error.diagnostic_ref),
        )
        if outcome.error
        else None
    )
    effect = (
        replace(
            outcome.effect,
            operation=_truncate_utf8(outcome.effect.operation, OFFLOAD_DATA_KEY_MAX_BYTES),
            target=_truncate_utf8(outcome.effect.target, OFFLOAD_OUTCOME_FIELD_MAX_BYTES),
            before_ref=_bounded_optional(outcome.effect.before_ref),
            after_ref=_bounded_optional(outcome.effect.after_ref),
        )
        if outcome.effect
        else None
    )
    verification = (
        replace(
            outcome.verification,
            postcondition=_truncate_utf8(outcome.verification.postcondition, OFFLOAD_OUTCOME_FIELD_MAX_BYTES),
            observed=_truncate_utf8(outcome.verification.observed, OFFLOAD_OUTCOME_FIELD_MAX_BYTES),
        )
        if outcome.verification
        else None
    )
    return replace(
        outcome,
        error=error,
        effect=effect,
        verification=verification,
        receipt=_bounded_optional(outcome.receipt),
    )


def _bounded_continuation_data(data: dict | None) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    bounded: dict[str, Any] = {}
    omitted: set[str] = set()
    existing_omissions = data.get("continuation_fields_omitted")
    if isinstance(existing_omissions, list | tuple):
        omitted.update(
            value for value in existing_omissions if isinstance(value, str) and value in OFFLOAD_CONTINUATION_FIELDS
        )
    for key in OFFLOAD_CONTINUATION_FIELDS:
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, str) and len(value.encode("utf-8")) > OFFLOAD_CONTINUATION_VALUE_MAX_BYTES:
            omitted.add(key)
        elif isinstance(value, str | int | float | bool) or value is None:
            bounded[key] = value
    if omitted:
        bounded["continuation_fields_omitted"] = sorted(omitted)
    return bounded


def _effective_timeout_seconds(tool: Any) -> int | float | None:
    if tool.policy.timeout_seconds is not None:
        return tool.policy.timeout_seconds
    if tool.policy.scope == ToolScope.EXTERNAL:
        return DEFAULT_EXTERNAL_TOOL_TIMEOUT_SECONDS
    return None


class ArdenToolExecutor:
    def __init__(
        self,
        executor: ToolExecutor,
        ctx: ToolContext,
        ledger: SharedLedger | None = None,
        *,
        skip_duplicate_reads: bool = False,
    ):
        self._executor = executor
        self._ctx = ctx
        self._ledger = ledger
        self._skip_duplicate_reads = skip_duplicate_reads
        self._meta_cache: dict[str, ToolMeta | None] = {}

    @property
    def ctx(self) -> ToolContext:
        return self._ctx

    def mark_provider_loaded_tools(self, names: set[str]) -> None:
        if self._ctx.run.deferred_tools_enabled:
            self._ctx.run.loaded_tools.update(names)

    def prepare_recovered_result(self, result: ToolResult, tool_call_id: str) -> ToolResult:
        """Apply the same ingestion bound to a result replayed from durable storage.

        Recovery bypasses normal tool execution, so it must explicitly pass
        through the universal serialized-payload bound before the result is
        appended to model history.
        """
        return self._bound_result_payload(result, tool_call_id).with_default_outcome()

    async def execute(self, name: str, args: dict, tool_call_id: str) -> ToolResult:
        with Tracer.span(f"tool.{name}"):
            Tracer.set_tool_span()
            return await self._execute(name, args, tool_call_id)

    async def _execute(self, name: str, args: dict, tool_call_id: str) -> ToolResult:
        tool = self._executor.registry.get(name)
        if not tool:
            return ToolResult.failure(
                code="unknown_tool",
                message=f"Unknown tool: {name}. Check available tools in the system prompt.",
                preview="Unknown tool",
                recovery_action="Use a tool exposed in the current system prompt.",
            )

        loader = self._ctx.run.deferred_tool_loader
        if (
            self._ctx.run.allowed_tool_names is not None
            and name not in self._ctx.run.allowed_tool_names
            and not (name == loader and self._ctx.run.deferred_tools_enabled)
        ):
            return ToolResult.failure(
                code="permission_denied",
                message=f"Tool {name!r} is not available in this run. Use only tools exposed in the system prompt.",
                preview="Tool not allowed",
                status=ToolOutcomeStatus.DENIED,
                recovery_action="Use a tool exposed in this run.",
            )

        if (
            self._ctx.run.deferred_tools_enabled
            and is_deferred_tool(name, self._executor.registry)
            and name not in self._ctx.run.loaded_tools
        ):
            return ToolResult.failure(
                code="tool_not_loaded",
                message=(
                    f"Tool {name!r} is deferred and has not been loaded in this run. "
                    + (
                        f"Use the provider-native tool search for {name!r} first, then retry."
                        if loader == "tool_search"
                        else f"Call load_tools(names=['{name}']) first, then retry."
                    )
                ),
                preview="Tool not loaded",
                recovery_action=(
                    f"Search for {name!r} with the provider-native tool search, then retry."
                    if loader == "tool_search"
                    else f"Load {name!r} with {loader}, then retry."
                ),
            )

        # Presentation metadata must not change execution, audit identity,
        # connection recovery, or shared-read deduplication.
        _, args = split_tool_arguments(args)

        store = self._audit_store() if tool.policy.audit else None
        if store:
            await self._record_tool_call_started(store, name, args, tool_call_id, tool.policy.action, tool.policy.scope)

        read_key: str | None = None
        if self._ledger and tool.policy.action == ToolAction.READ and name not in LIVE_READ_TOOLS:
            if self._skip_duplicate_reads:
                read_key = await self._ledger.claim_read(name, args)
                if read_key is None:
                    content = f"[Already read by another agent in this run: {name} {format_arguments(args)}]"
                    result = ToolResult(content=content, preview="Already read").with_default_outcome()
                    if store:
                        await self._record_tool_call_finished(
                            store,
                            tool_call_id,
                            "success",
                            result.preview,
                            result.outcome.to_dict(),
                        )
                    return result
            else:
                await self._ledger.mark_accessed(access_key(name, args))

        execution = ToolExecution(tool_id=tool_call_id, tool_name=name, ctx=self._ctx)
        timeout_seconds = _effective_timeout_seconds(tool)
        invocation = ToolInvocation(
            invocation_id=tool_call_id, tool_name=name, arguments=args, timeout_seconds=timeout_seconds
        )
        result: ToolResult | None = None
        read_succeeded = False
        finish_status: str | None = None
        finish_preview: str | None = None
        try:
            execute = self._executor.router.execute(invocation, execution)
            try:
                if timeout_seconds is None:
                    result = await execute
                else:
                    result = await asyncio.wait_for(execute, timeout=timeout_seconds)
            except TimeoutError:
                mutation = tool.policy.action in {ToolAction.WRITE, ToolAction.EXECUTE}
                result = ToolResult.failure(
                    code="mutation_timeout" if mutation else "timeout",
                    message="Tool call timed out; provider completion is unknown."
                    if mutation
                    else "Tool call timed out.",
                    preview="Completion unknown" if mutation else "Timed out",
                    status=ToolOutcomeStatus.UNCERTAIN if mutation else ToolOutcomeStatus.FAILED,
                    retryable=not mutation,
                    recovery_action=(
                        "Verify provider state using the operation's idempotency key before retrying."
                        if mutation
                        else "Check whether the operation completed before retrying."
                    ),
                )
                finish_status = "timeout"
                finish_preview = result.preview
                return result
            except IntegrationConnectionError as exc:
                result = await self._recover_connection(
                    exc,
                    tool=tool,
                    execution=execution,
                    name=name,
                    args=args,
                    timeout_seconds=timeout_seconds,
                )

            result = self._truncate_result(result, tool.policy.max_result_chars, tool_call_id=tool_call_id)
            if tool.policy.offload:
                result = self._maybe_offload(result, tool_call_id)
            result = self._bound_result_payload(result, tool_call_id)
            result = result.with_default_outcome()

            finish_status = self._audit_status(result)
            finish_preview = result.preview
            read_succeeded = not result.is_error
            return result
        except asyncio.CancelledError:
            finish_status = "cancelled"
            result = ToolResult.failure(
                code="tool_cancelled",
                message="Tool execution was cancelled before a terminal result was observed.",
                preview="Cancelled",
                status=ToolOutcomeStatus.UNCERTAIN,
                recovery_action="Verify whether the operation completed before retrying.",
            )
            finish_preview = result.preview
            raise
        except Exception:
            finish_status = "error"
            diagnostic_ref = f"tool-call:{tool_call_id}"
            result = ToolResult.failure(
                code="internal_error",
                message="Tool execution failed unexpectedly.",
                preview="Tool execution failed",
                status=ToolOutcomeStatus.UNCERTAIN,
                recovery_action="Check the target's current state before retrying.",
                diagnostic_ref=diagnostic_ref,
            )
            finish_preview = result.preview
            raise
        finally:
            if read_key is not None:
                self._ledger.finish_read(read_key, succeeded=read_succeeded)
            if store and finish_status is not None:
                await self._record_tool_call_finished(
                    store,
                    tool_call_id,
                    finish_status,
                    finish_preview,
                    result.outcome.to_dict() if result and result.outcome else None,
                )

    async def _recover_connection(
        self,
        error: IntegrationConnectionError,
        *,
        tool: Any,
        execution: ToolExecution,
        name: str,
        args: dict,
        timeout_seconds: int | float | None,
    ) -> ToolResult:
        service = self._ctx.get_client("connections", ConnectionService)
        descriptor = service.recovery_descriptor(error) if service else None
        if descriptor is None:
            return ToolResult.failure(
                code="connection_required",
                message=error.detail,
                preview="Connection required",
                retryable=True,
                recovery_action="Repair the integration connection in settings, then retry.",
            )

        accepted = await execution.request_connection(descriptor, source="recovery", detail=error.detail)
        if not accepted:
            return ToolResult.failure(
                code="connection_declined",
                message=f"{descriptor.label} was not reconnected.",
                preview="Connection declined",
                recovery_action="Continue without this integration or reconnect it later.",
            )

        if not error.retry_safe or tool.policy.action != ToolAction.READ:
            return ToolResult.failure(
                code="connection_retry_required",
                message=f"{descriptor.label} is connected. Retry the operation explicitly.",
                preview="Ready to retry",
                retryable=True,
                recovery_action="Retry the operation once.",
            )

        try:
            invocation = ToolInvocation(
                invocation_id=execution.tool_id, tool_name=name, arguments=args, timeout_seconds=timeout_seconds
            )
            retry = self._executor.router.execute(invocation, execution)
            if timeout_seconds is None:
                return await retry
            return await asyncio.wait_for(retry, timeout=timeout_seconds)
        except IntegrationConnectionError as retry_error:
            return ToolResult.failure(
                code="connection_still_unavailable",
                message=retry_error.detail,
                preview="Connection unavailable",
                retryable=True,
                recovery_action="Check the integration settings before retrying.",
            )
        except TimeoutError:
            return ToolResult.failure(
                code="timeout",
                message="Tool call timed out after reconnecting.",
                preview="Timed out",
                retryable=True,
                recovery_action="Check whether the operation completed before retrying.",
            )

    def _audit_store(self) -> Any | None:
        store = self._ctx.services.get("store")
        if store:
            return store

        session_service = self._ctx.services.get("session")
        return getattr(session_service, "store", None)

    def _args_hash(self, args: dict) -> str:
        serialized = json.dumps(args, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _audit_preview(self, preview: str) -> str:
        if len(preview) <= AUDIT_PREVIEW_MAX_CHARS:
            return preview
        return f"{preview[:AUDIT_PREVIEW_MAX_CHARS]}... [truncated]"

    def _audit_status(self, result: ToolResult) -> str:
        if result.is_error or result.preview == "Rejected":
            return "error"
        return "success"

    async def _record_tool_call_started(
        self,
        store: Any,
        name: str,
        args: dict,
        tool_call_id: str,
        action: Any,
        scope: Any,
    ) -> None:
        try:
            await store.record_tool_call_started(
                run_id=self._ctx.run.run_id,
                session_id=self._ctx.session_id,
                tool_call_id=tool_call_id,
                tool_name=name,
                action=str(action),
                scope=str(scope),
                args_hash=self._args_hash(args),
            )
        except Exception:
            _logger.warning("Failed to record tool call audit start", exc_info=True)

    async def _record_tool_call_finished(
        self,
        store: Any,
        tool_call_id: str,
        status: str,
        result_preview: str | None,
        outcome: dict | None = None,
    ) -> None:
        try:
            await store.record_tool_call_finished(
                run_id=self._ctx.run.run_id,
                tool_call_id=tool_call_id,
                status=status,
                result_preview=self._audit_preview(result_preview) if result_preview is not None else None,
                outcome=outcome,
            )
        except Exception:
            _logger.warning("Failed to record tool call audit finish", exc_info=True)

    def _truncate_result(
        self,
        result: ToolResult,
        max_chars: int | None,
        *,
        tool_call_id: str | None = None,
    ) -> ToolResult:
        if max_chars is None or len(result.content) <= max_chars:
            return result

        data = result.data
        note = ""
        if tool_call_id is not None:
            raw_payload = result.serialized_payload()
            path = persist_result_payload(self._ctx.session_id, tool_call_id, raw_payload)
            blob = persist_raw_tool_result(raw_payload)
            data = {
                "truncated": True,
                "raw_ref": blob.blob_ref,
                "raw_bytes": blob.content_bytes,
                **blob.to_internal_data(),
            }
            note = f"\n[Full result saved to {path}. Use file_read(path={str(path)!r}) to retrieve it.]"
        return ToolResult(
            content=f"{result.content[:max_chars]}... [truncated]{note}",
            preview=result.preview,
            is_error=result.is_error,
            data=data,
            model_content=() if tool_call_id is not None else result.model_content,
            source_refs=result.source_refs,
            outcome=result.outcome,
        )

    def _bound_result_payload(self, result: ToolResult, tool_call_id: str) -> ToolResult:
        result = result.with_default_outcome()
        serialized = result.serialized_payload()
        if len(serialized.encode("utf-8")) <= OFFLOAD_THRESHOLD:
            return result

        exact_serialized = self._exact_serialized_payload(result, serialized)
        path = persist_result_payload(self._ctx.session_id, tool_call_id, exact_serialized)
        blob = persist_raw_tool_result(exact_serialized)
        lines = result.content.split("\n")
        preview, _preview_lines = _bounded_offload_preview(lines)
        data: dict[str, Any] = {
            "truncated": True,
            "raw_ref": blob.blob_ref,
            "raw_bytes": blob.content_bytes,
            **blob.to_internal_data(),
            **_bounded_continuation_data(result.data),
        }
        if isinstance(result.data, dict):
            public_keys = sorted(
                _truncate_utf8(key, OFFLOAD_DATA_KEY_MAX_BYTES)
                for key in result.data
                if isinstance(key, str) and not key.startswith("_")
            )[:OFFLOAD_DATA_SUMMARY_MAX_KEYS]
            if public_keys:
                data["data_summary"] = {"keys": public_keys}
        compact = (
            f"{preview}\n\n"
            f"[Full tool result payload ({blob.content_bytes} bytes) saved to {path}.]\n"
            f"Use file_read(path={str(path)!r}, offset=N, limit=M) to retrieve it."
        )
        compact_result = ToolResult(
            content=compact,
            preview=_truncate_utf8(result.preview, AUDIT_PREVIEW_MAX_CHARS),
            is_error=result.is_error,
            data=data,
            model_content=(),
            source_refs=result.source_refs,
            outcome=_compact_outcome(result.outcome),
        )
        if len(compact_result.serialized_payload().encode("utf-8")) <= OFFLOAD_THRESHOLD:
            return compact_result
        compact_result = self._compact_source_refs(compact_result)
        if len(compact_result.serialized_payload().encode("utf-8")) <= OFFLOAD_THRESHOLD:
            return compact_result
        return self._minimal_offloaded_result(
            compact_result,
            payload_path=path,
            raw_ref=blob.blob_ref,
            raw_bytes=blob.content_bytes,
            raw_data=blob.to_internal_data(),
        )

    def _compact_source_refs(self, result: ToolResult) -> ToolResult:
        """Fit provenance into the live envelope; the exact payload is already durable."""
        total = len(result.source_refs)
        if total == 0:
            return result

        kept: list[tuple[ToolSourceRef, ToolSourceRef]] = []
        metadata = {
            **(result.data or {}),
            "source_refs_compacted": True,
            "source_refs_total": total,
            "source_refs_included": total,
            "source_ref_urls_omitted": total,
        }
        for source_ref in result.source_refs:
            identity_ref = replace(source_ref, url=None) if source_ref.url else source_ref
            current_refs = tuple(compact for _original, compact in kept)
            candidate = replace(
                result,
                data=metadata,
                source_refs=(*current_refs, identity_ref),
            )
            if len(candidate.serialized_payload().encode("utf-8")) <= OFFLOAD_THRESHOLD:
                kept.append((source_ref, identity_ref))

        for index, (source_ref, _compact_ref) in enumerate(kept):
            if source_ref.url is None:
                continue
            upgraded = [candidate_ref for _original, candidate_ref in kept]
            upgraded[index] = source_ref
            candidate = replace(result, data=metadata, source_refs=tuple(upgraded))
            if len(candidate.serialized_payload().encode("utf-8")) <= OFFLOAD_THRESHOLD:
                kept[index] = (source_ref, source_ref)

        compact_refs = tuple(compact_ref for _original, compact_ref in kept)
        urls_omitted = sum(original.url is not None and compact.url is None for original, compact in kept)

        return replace(
            result,
            data={
                **metadata,
                "source_refs_included": len(compact_refs),
                "source_ref_urls_omitted": urls_omitted,
            },
            source_refs=compact_refs,
        )

    def _minimal_offloaded_result(
        self,
        result: ToolResult,
        *,
        payload_path: Path,
        raw_ref: str,
        raw_bytes: int,
        raw_data: dict[str, Any],
    ) -> ToolResult:
        data: dict[str, Any] = {
            "truncated": True,
            "raw_ref": raw_ref,
            "raw_bytes": raw_bytes,
            **raw_data,
            **_bounded_continuation_data(result.data),
        }
        source_refs_total = (
            result.data.get("source_refs_total", len(result.source_refs))
            if isinstance(result.data, dict)
            else len(result.source_refs)
        )
        if isinstance(source_refs_total, int) and source_refs_total > 0:
            data.update(
                {
                    "source_refs_compacted": True,
                    "source_refs_total": source_refs_total,
                    "source_refs_included": 0,
                }
            )
        return ToolResult(
            content=(
                f"Full tool result payload ({raw_bytes} bytes) saved to {payload_path}.\n"
                f"Use file_read(path={str(payload_path)!r}, offset=N, limit=M) to retrieve it."
            ),
            preview=_truncate_utf8(result.preview, AUDIT_PREVIEW_MAX_CHARS),
            is_error=result.is_error,
            data=data,
            source_refs=(),
            outcome=_compact_outcome(result.outcome),
        )

    def _exact_serialized_payload(self, result: ToolResult, serialized: str) -> str:
        """Undo a readable-content offload before persisting the exact envelope."""
        raw_blob = internal_blob_from_data(result.data)
        if raw_blob is None:
            return serialized
        try:
            raw = read_raw_tool_result(raw_blob.blob_path, compression=raw_blob.compression)
        except OSError:
            return serialized
        if ToolResult.from_serialized_payload(raw) is not None:
            return raw
        exact = ToolResult(
            content=raw,
            preview=result.preview,
            is_error=result.is_error,
            data=strip_internal_raw_tool_result_data(result.data),
            model_content=result.model_content,
            source_refs=result.source_refs,
            outcome=result.outcome,
        )
        return exact.serialized_payload()

    def get_meta(self, name: str) -> ToolMeta | None:
        if name not in self._meta_cache:
            tool = self._executor.registry.get(name)
            if tool:
                source = self._executor.registry.get_source(name)
                icon, noun = tool_presentation(name, source)
                default_concurrency_group = source if source and is_external_source(source) else name
                self._meta_cache[name] = ToolMeta(
                    name=name,
                    display_name=tool.display_name or name,
                    kind=tool.kind,
                    icon=icon,
                    noun=noun,
                    source=source,
                    changes_state=tool.policy.action in {ToolAction.DRAFT, ToolAction.WRITE, ToolAction.EXECUTE},
                    concurrency_group=tool.policy.concurrency_group or default_concurrency_group,
                    ends_turn=tool.ends_turn,
                )
            else:
                self._meta_cache[name] = None
        return self._meta_cache[name]

    def _maybe_offload(self, result: ToolResult, tool_call_id: str) -> ToolResult:
        content = result.content
        if len(content) <= OFFLOAD_THRESHOLD:
            return result

        path = persist_result(self._ctx.session_id, tool_call_id, content)
        exact_payload = result.serialized_payload()
        persist_result_payload(self._ctx.session_id, tool_call_id, exact_payload)
        payload_blob = persist_raw_tool_result(exact_payload)
        data = {**(result.data or {}), **payload_blob.to_internal_data()}
        lines = content.split("\n")
        total = len(lines)
        preview, preview_lines = _bounded_offload_preview(lines)
        hidden_lines = max(0, total - preview_lines)
        compact = (
            f"{preview}\n\n"
            f"[Full result ({total} lines, {hidden_lines} not shown here) saved to {path}.]\n"
            f"Use file_read(path={str(path)!r}, offset=N, limit=M) to read more, "
            f"or file_search_text / bash grep over that file to find specific content."
        )
        return ToolResult(
            content=compact,
            preview=result.preview,
            is_error=result.is_error,
            data=data,
            model_content=result.model_content,
            source_refs=result.source_refs,
            outcome=result.outcome,
        )


def _take_lines(lines: list[str], char_budget: int) -> tuple[str, int]:
    out: list[str] = []
    used = 0
    for line in lines:
        remaining = char_budget - used
        if remaining <= 0:
            break
        if len(line) > remaining:
            out.append(f"{line[:remaining]}... [truncated]")
            return "\n".join(out), len(out)
        out.append(line)
        used += len(line) + 1
    return "\n".join(out), len(out)


def _bounded_offload_preview(lines: list[str]) -> tuple[str, int]:
    # Keep a head AND a tail: errors/setup tend to appear early, final results/exit codes late.
    if len(lines) <= OFFLOAD_PREVIEW_LINES:
        return _take_lines(lines, OFFLOAD_PREVIEW_CHARS)

    head_line_budget = OFFLOAD_PREVIEW_LINES * 3 // 5
    tail_line_budget = OFFLOAD_PREVIEW_LINES - head_line_budget
    head_char_budget = OFFLOAD_PREVIEW_CHARS * 3 // 5
    tail_char_budget = OFFLOAD_PREVIEW_CHARS - head_char_budget

    head_text, head_n = _take_lines(lines[:head_line_budget], head_char_budget)
    tail_text, tail_n = _take_lines(lines[-tail_line_budget:], tail_char_budget)
    omitted = max(0, len(lines) - head_n - tail_n)
    preview = f"{head_text}\n... [{omitted} lines omitted] ...\n{tail_text}"
    return preview, head_n + tail_n
