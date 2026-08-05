import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from arden.agent.tools.dispatch import dispatch_tools
from arden.agent.tools.runner import ToolRunner
from arden.agent.types.events import ToolCompleted
from arden.agent.types.llm import Role
from arden.agent.types.tool_call import FunctionCall, PendingToolCall, ToolCall
from arden.agent.types.tools import (
    ToolMeta,
    ToolOutcome,
    ToolOutcomeStatus,
    ToolResult,
    ToolResultPayloadError,
    ToolSourceRef,
    ToolSourceRefError,
    canonical_source_title,
    decode_source_refs,
    normalize_source_refs,
)
from arden.constants import OFFLOAD_THRESHOLD
from arden.core.raw_tool_results import RawToolResultBlob
from arden.core.tool_executor import ArdenToolExecutor
from arden.core.tool_result_data import persistable_tool_result_data
from arden.server.bus import BusRegistry
from arden.server.routers.session import get_session_history


def _source(
    *,
    provider: str = "slack",
    kind: str = "message",
    ref: str = "C1:1.0",
    title: str = "Decision",
    url: str | None = "https://example.slack.com/archives/C1/p1",
) -> ToolSourceRef:
    return ToolSourceRef(provider=provider, kind=kind, ref=ref, title=title, url=url)


def _page_source() -> ToolSourceRef:
    return _source(
        provider="web",
        kind="page",
        ref="https://example.com/docs",
        title="Documentation",
        url="https://example.com/docs",
    )


def test_source_refs_round_trip_through_strict_decode():
    source = _source()

    assert ToolSourceRef.decode(source.to_dict()) == source
    assert decode_source_refs([source.to_dict()]) == (source,)
    assert normalize_source_refs([source]) == (source,)
    assert source.to_dict() == {
        "provider": "slack",
        "kind": "message",
        "ref": "C1:1.0",
        "title": "Decision",
        "url": "https://example.slack.com/archives/C1/p1",
    }
    assert _source(url=None).to_dict() == {
        "provider": "slack",
        "kind": "message",
        "ref": "C1:1.0",
        "title": "Decision",
    }


@pytest.mark.parametrize("field", ["provider", "kind", "ref", "title"])
def test_source_refs_reject_missing_or_empty_required_fields(field: str):
    raw = _source().to_dict()
    raw[field] = "   "

    with pytest.raises(ToolSourceRefError, match=field):
        ToolSourceRef.decode(raw)

    raw.pop(field)
    with pytest.raises(ToolSourceRefError, match=field):
        ToolSourceRef.decode(raw)


@pytest.mark.parametrize(
    ("field", "limit"),
    [("provider", 64), ("kind", 64), ("ref", 2048)],
)
def test_source_refs_reject_identity_fields_over_their_limits(field: str, limit: int):
    values = _source().to_dict()
    values[field] = "x" * limit
    assert ToolSourceRef.decode(values)

    values[field] = "x" * (limit + 1)
    with pytest.raises(ToolSourceRefError, match=f"{field} exceeds"):
        ToolSourceRef.decode(values)


def test_source_refs_reject_oversized_titles():
    with pytest.raises(ToolSourceRefError, match="title exceeds 256"):
        _source(title="x" * 257)


@pytest.mark.parametrize(("field", "value"), [("provider", "web\ttool"), ("ref", "doc\u00a01"), ("title", "Title\x00")])
def test_source_refs_reject_control_whitespace(field: str, value: str):
    raw = _source().to_dict()
    raw[field] = value

    with pytest.raises(ToolSourceRefError, match="control whitespace"):
        ToolSourceRef.decode(raw)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "ftp://example.com/file",
        "https:///missing-host",
        "https://@example.com/private",
        "https://user@example.com/private",
        "https://user:secret@example.com/private",
        f"https://example.com/{'x' * 4090}",
        "not a url",
    ],
)
def test_source_refs_reject_unsafe_or_oversized_urls(url: str):
    with pytest.raises(ToolSourceRefError, match="url"):
        _source(url=url)


def test_source_refs_keep_safe_urls_at_the_length_limit():
    prefix = "https://example.com/"
    url = prefix + "x" * (4096 - len(prefix))

    assert _source(url=url).url == url


@pytest.mark.parametrize(
    "ref",
    [
        "https://user@example.com/private",
        "https://user:secret@example.com/private",
    ],
)
def test_source_refs_reject_http_identities_with_credentials(ref: str):
    with pytest.raises(ToolSourceRefError, match="ref must not contain HTTP credentials"):
        _source(ref=ref, title=ref, url=ref)


def test_source_refs_reject_credential_bearing_titles():
    with pytest.raises(ToolSourceRefError, match="title must not contain HTTP credentials"):
        _source(ref="opaque-123", title="https://user:secret@example.com/private", url=None)


def test_source_refs_deduplicate_provider_and_ref_then_reject_overflow():
    refs = [
        _source(title="First"),
        _source(title="Duplicate"),
        *[_source(ref=f"C1:{index}", title=f"Message {index}") for index in range(1, 55)],
    ]

    with pytest.raises(ToolSourceRefError, match="exceed 50 items"):
        normalize_source_refs(refs)


def test_source_refs_reject_overflow_even_when_later_items_are_duplicates():
    source = _source()

    with pytest.raises(ToolSourceRefError, match="exceed 50 items"):
        normalize_source_refs([source] * 51)


def test_source_ref_decoder_rejects_arrays_over_50_before_ignoring_malformed_tail():
    raw = [_source(ref=f"C1:{index}").to_dict() for index in range(50)]
    raw.append({"bad": "tail"})

    with pytest.raises(ToolSourceRefError, match="exceed 50 items"):
        decode_source_refs(raw)


def test_canonical_source_title_is_single_line_bounded_and_credential_safe():
    projected = canonical_source_title(
        "  Review\nhttps://user:secret@example.test/private  " + "x" * 300,
        fallback="doc-1",
    )

    assert projected.startswith("Review [redacted URL]")
    assert "secret" not in projected
    assert "\n" not in projected
    assert len(projected) == 256
    assert canonical_source_title("   ", fallback="doc-1") == "doc-1"


def test_source_ref_rejects_embedded_credentials_and_invalid_ports():
    with pytest.raises(ToolSourceRefError, match="title must not contain HTTP credentials"):
        _source(title="See https://user:secret@example.test/private", url=None)
    with pytest.raises(ToolSourceRefError, match="valid HTTP"):
        _source(url="https://example.test:notaport")


def test_persistable_tool_result_data_keeps_only_provenance_and_typed_source_refs():
    data = {
        "child_agent": {"child_run_id": "child-1", "wait": False},
        "provenance": {
            "query": "audit",
            "window": {"depth": "deep"},
            "derivation": {"child_run_id": "child-1"},
            "workspace_ref": "research-1:_provenance.json",
        },
        "source_refs": [
            _source().to_dict(),
            _source(title="Duplicate").to_dict(),
        ],
        "matches": [{"text": "large arbitrary result data"}],
    }

    assert persistable_tool_result_data(data) == {
        "child_agent": {"child_run_id": "child-1", "wait": False},
        "provenance": data["provenance"],
        "source_refs": [_source().to_dict()],
    }
    assert persistable_tool_result_data(None, (_source(url=None),)) == {"source_refs": [_source(url=None).to_dict()]}


def test_persisted_source_refs_raise_instead_of_disappearing() -> None:
    data = {"source_refs": [{"provider": "web", "kind": "page", "ref": "doc-1"}]}

    with pytest.raises(ToolSourceRefError, match="missing: title"):
        persistable_tool_result_data(data)


def test_source_ref_decode_rejects_unknown_fields() -> None:
    raw = {**_source().to_dict(), "internal_id": "secret"}

    with pytest.raises(ToolSourceRefError, match="unknown fields: internal_id"):
        ToolSourceRef.decode(raw)


def test_normalization_rejects_raw_mappings() -> None:
    with pytest.raises(ToolSourceRefError, match="only ToolSourceRef"):
        normalize_source_refs([_source().to_dict()])

    with pytest.raises(ToolSourceRefError, match="only ToolSourceRef"):
        ToolResult(content="result", preview="result", source_refs=(_source().to_dict(),))


def test_tool_result_decoder_raises_for_malformed_persisted_source_refs() -> None:
    payload = json.loads(ToolResult(content="result", preview="result", source_refs=(_source(),)).serialized_payload())
    payload["source_refs"][0].pop("title")

    with pytest.raises(ToolResultPayloadError, match="missing: title"):
        ToolResult.from_serialized_payload(json.dumps(payload))


class _SourceExecutor:
    def get_meta(self, name: str) -> ToolMeta:
        return ToolMeta(name=name, display_name="Source Tool")

    async def execute(self, name: str, args: dict, tool_call_id: str) -> ToolResult:
        return ToolResult(content="result", preview="source", source_refs=(_source(), _page_source()))


def _pending_call() -> tuple[PendingToolCall, ToolCall]:
    raw = ToolCall(id="call-1", type="function", function=FunctionCall(name="source_tool", arguments="{}"))
    return PendingToolCall(tool_call=raw, name="source_tool", args={}), raw


@pytest.mark.asyncio
async def test_tool_runner_carries_source_refs_to_completion():
    pending, _ = _pending_call()
    runner = ToolRunner(_SourceExecutor(), depth=0, parent_id=None)

    events = [event async for event in runner.execute_all([pending])]

    completed = next(event for event in events if isinstance(event, ToolCompleted))
    assert completed.source_refs == (_source(), _page_source())


def test_tool_executor_truncation_preserves_source_refs():
    result = ToolResult(content="long result", preview="long", source_refs=(_source(),))

    truncated = ArdenToolExecutor._truncate_result(object(), result, 4)

    assert truncated.content == "long... [truncated]"
    assert truncated.source_refs == (_source(),)


def test_tool_executor_offload_preserves_source_refs(monkeypatch, tmp_path: Path):
    import arden.core.tool_result_files as tool_result_files

    executor = object.__new__(ArdenToolExecutor)
    executor._ctx = SimpleNamespace(session_id="session-1")
    monkeypatch.setattr(tool_result_files, "RESULTS_BASE", tmp_path / "tool-results")
    monkeypatch.setattr("arden.core.tool_executor.persist_result", lambda *_args: tmp_path / "call-1.txt")
    result = ToolResult(
        content="x" * (OFFLOAD_THRESHOLD + 1),
        preview="large",
        data={
            "child_agent": {"child_run_id": "child-1", "wait": False},
            "provenance": {"workspace_ref": "research-1:_provenance.json"},
            "matches": [{"text": "not continuation metadata"}],
        },
        source_refs=(_source(),),
    )

    offloaded = executor._bound_result_payload(result, "call-1", offload_content=True)

    assert offloaded.source_refs == (_source(),)
    assert offloaded.data["child_agent"] == result.data["child_agent"]
    assert offloaded.data["provenance"] == result.data["provenance"]
    assert "matches" not in offloaded.data
    assert "saved to" in offloaded.content


def test_tool_executor_recovery_blob_read_failure_raises(monkeypatch):
    executor = object.__new__(ArdenToolExecutor)
    executor._ctx = SimpleNamespace(session_id="session-1")
    missing_blob = RawToolResultBlob(
        blob_ref="sha256:missing",
        blob_path="/missing/tool-result.gz",
        content_sha256="missing",
        content_bytes=1,
        stored_bytes=1,
    )
    result = ToolResult(
        content="x" * (OFFLOAD_THRESHOLD + 1),
        preview="preview",
        data=missing_blob.to_internal_data(),
    )

    def read_missing_blob(*_args, **_kwargs):
        raise OSError("blob is unavailable")

    monkeypatch.setattr(
        "arden.core.tool_executor.read_raw_tool_result",
        read_missing_blob,
    )

    with pytest.raises(OSError, match="blob is unavailable"):
        executor._bound_result_payload(result, "call-1")


def test_tool_executor_final_bound_limits_adversarial_metadata(monkeypatch, tmp_path: Path):
    import arden.core.tool_result_files as tool_result_files

    monkeypatch.setattr(tool_result_files, "RESULTS_BASE", tmp_path / "tool-results")
    executor = object.__new__(ArdenToolExecutor)
    executor._ctx = SimpleNamespace(session_id="session-1")
    long_keys = {f"{'k' * 2_000}{index}": index for index in range(50)}
    adversarial_data = {**long_keys, "has_more": True, "next_cursor": "z" * 100_000}
    long_url = "https://example.com/" + "a" * 3_900
    result = ToolResult(
        content="summary",
        preview="😀" * 20_000,
        data=adversarial_data,
        source_refs=(
            ToolSourceRef(
                provider="web",
                kind="page",
                ref="url-sha256:abc",
                title="Long source",
                url=long_url,
            ),
        ),
        outcome=ToolOutcome(status=ToolOutcomeStatus.SUCCEEDED, receipt="r" * 100_000),
    )

    bounded = executor._bound_result_payload(result, "call-adversarial-metadata")

    assert len(bounded.serialized_payload().encode("utf-8")) <= OFFLOAD_THRESHOLD
    assert len(bounded.preview.encode("utf-8")) <= 500
    assert all(len(key.encode("utf-8")) <= 128 for key in bounded.data["data_summary"]["keys"])
    assert bounded.outcome is not None
    assert len(bounded.outcome.receipt.encode("utf-8")) <= 512
    assert bounded.data["has_more"] is True
    assert "next_cursor" not in bounded.data
    assert bounded.data["continuation_fields_omitted"] == ["next_cursor"]

    exact = ToolResult.from_serialized_payload(
        tool_result_files.result_payload_file_path("session-1", "call-adversarial-metadata").read_text()
    )
    assert exact is not None
    assert exact.data == adversarial_data
    assert exact.preview == result.preview
    assert exact.source_refs[0].url == long_url
    assert exact.outcome is not None
    assert exact.outcome.receipt == result.outcome.receipt


def test_tool_executor_final_bound_uses_minimal_locator_for_multibyte_content(monkeypatch, tmp_path: Path):
    import arden.core.tool_result_files as tool_result_files

    monkeypatch.setattr(tool_result_files, "RESULTS_BASE", tmp_path / "tool-results")
    executor = object.__new__(ArdenToolExecutor)
    executor._ctx = SimpleNamespace(session_id="session-1")
    content = "😀" * 20_000

    bounded = executor._bound_result_payload(
        ToolResult(
            content=content,
            preview="multibyte",
            data={"has_more": True, "next_cursor": "z" * 100_000},
        ),
        "call-multibyte",
    )

    assert len(bounded.serialized_payload().encode("utf-8")) <= OFFLOAD_THRESHOLD
    assert content not in bounded.content
    assert "Full tool result payload" in bounded.content
    assert "file_read" in bounded.content
    assert bounded.data["has_more"] is True
    assert bounded.data["continuation_fields_omitted"] == ["next_cursor"]
    exact = ToolResult.from_serialized_payload(
        tool_result_files.result_payload_file_path("session-1", "call-multibyte").read_text()
    )
    assert exact is not None
    assert exact.content == content
    assert exact.data["next_cursor"] == "z" * 100_000


class _SourceRunner:
    async def execute_all(self, _calls):
        yield ToolCompleted(
            tool_id="call-1",
            name="source_tool",
            result="result body",
            preview="source",
            duration_ms=1,
            is_error=False,
            data={"matches": [{"text": "must not persist"}]},
            display_name="Source Tool",
            source_refs=(_source(), _page_source()),
        )


class _ResearchProvenanceRunner:
    async def execute_all(self, _calls):
        yield ToolCompleted(
            tool_id="call-1",
            name="research",
            result="Research started",
            preview="Research started",
            duration_ms=1,
            is_error=False,
            data={
                "research_scope_id": "research-fun-panda",
                "provenance": {
                    "query": "audit tool history",
                    "derivation": {"agent_ref": "audit~abc123"},
                    "workspace_ref": "research-fun-panda:_provenance.json",
                },
            },
            display_name="Research",
        )


@pytest.mark.asyncio
async def test_dispatch_persists_source_refs_outside_tool_result_content():
    pending, raw = _pending_call()
    messages: list[dict] = []

    async for _ in dispatch_tools(_SourceRunner(), messages, [pending], [raw]):
        pass

    assert messages == [
        {
            "role": Role.TOOL,
            "tool_call_id": "call-1",
            "content": "result body",
            "data": {"source_refs": [_source().to_dict(), _page_source().to_dict()]},
        }
    ]


@pytest.mark.asyncio
async def test_dispatch_keeps_research_provenance_without_internal_tool_call_id():
    pending, raw = _pending_call()
    messages: list[dict] = []

    async for _ in dispatch_tools(_ResearchProvenanceRunner(), messages, [pending], [raw]):
        pass

    assert messages == [
        {
            "role": Role.TOOL,
            "tool_call_id": "call-1",
            "content": "Research started",
            "data": {
                "provenance": {
                    "query": "audit tool history",
                    "derivation": {"agent_ref": "audit~abc123"},
                    "workspace_ref": "research-fun-panda:_provenance.json",
                }
            },
        }
    ]
    assert "tool_call_id" not in json.dumps(messages[0]["data"])


@pytest.mark.asyncio
async def test_session_history_restores_persisted_source_refs_unchanged():
    persisted_refs = [_source().to_dict()]

    class _Store:
        async def get_latest_session_event_seq(self, _session_id: str) -> int:
            return 0

        async def get_latest_session_checkpoint_seq(self, _session_id: str) -> int:
            return 0

        async def get_latest_chat_run_for_session(self, _session_id: str):
            return None

        async def list_tool_call_outcomes(self, **_kwargs):
            return {}

    class _SessionService:
        store = _Store()

        async def load_history_header(self, _session_id: str):
            return SimpleNamespace(
                state=SimpleNamespace(session_id="session-1", chat_model=None),
                last_input_tokens=None,
                active_message_count=0,
            )

        async def list_messages(self, *_args, **_kwargs):
            return {
                "messages": [
                    {
                        "message": {
                            "role": Role.TOOL,
                            "tool_call_id": "call-1",
                            "content": "result body",
                            "data": {"source_refs": persisted_refs},
                        },
                        "message_id": "message-1",
                        "seq": 1,
                    }
                ],
                "has_more_before": False,
                "has_more_after": False,
                "before": None,
                "after": None,
            }

    history = await get_session_history(
        svc=_SessionService(),
        runtime=SimpleNamespace(
            run_registry=None,
            executor=None,
            config=SimpleNamespace(
                chat_model="gpt-5.4-mini",
                compression_threshold=0.8,
                max_messages=120,
            ),
        ),
        buses=BusRegistry(),
        session_id="session-1",
        limit=50,
        before=None,
        after=None,
        around=None,
        around_seq=None,
    )

    assert history["messages"][0]["data"] == {"source_refs": persisted_refs}
