"""remember()/recall()/forget() tools over the FLAT RecordStore (ntrp/tools/memory.py).

The tools add/search/delete atomic records via a real tmp RecordStore
(`search_index=None` -> FTS-only, no embeddings, no search.db). No scope, no lens
tool. A minimal namespace stands in for ToolExecution (the executors read only
ctx.services / ctx.session_id and execution.tool_id).
"""

import json
import types
from pathlib import Path

import pytest

import ntrp.tools.memory as memory_tools
from ntrp.memory.curator import Curator
from ntrp.memory.reconciler import RecordOperation
from ntrp.memory.records import RecordStore
from ntrp.tools.memory import (
    MEMORY_RECONCILER_SERVICE,
    MEMORY_RECORDS_SERVICE,
    ForgetInput,
    RecallInput,
    RememberInput,
    forget,
    recall,
    remember,
)
from tests.conftest import completion_response

pytestmark = pytest.mark.asyncio


@pytest.fixture
def store(tmp_path: Path) -> RecordStore:
    return RecordStore(tmp_path / "memory.db", search_index=None)


class StubLLM:
    def __init__(self, *responses: str):
        self._responses = list(responses)

    async def completion(self, **kwargs):
        body = self._responses.pop(0) if self._responses else ""
        return completion_response(body)


def _reconciler(tmp_path: Path, store: RecordStore, *records: dict) -> Curator:
    response = {"records": list(records)}
    return Curator(
        StubLLM(json.dumps(response)),
        sessions=None,
        model="memory-model",
        db_path=tmp_path / "curator.db",
        record_store=store,
    )


def _execution(store, reconciler=None):
    ctx = types.SimpleNamespace(
        services={MEMORY_RECORDS_SERVICE: store, MEMORY_RECONCILER_SERVICE: reconciler},
        session_id="s1",
        area=None,
    )
    return types.SimpleNamespace(ctx=ctx, tool_id="t1")


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")


# --- remember -----------------------------------------------------------------


async def test_remember_adds_a_record_with_kind(store: RecordStore, tmp_path: Path):
    execution = _execution(
        store,
        _reconciler(tmp_path, store, {"op": "ADD", "text": "the user prefers tea", "kind": "fact"}),
    )
    result = await remember(
        execution, RememberInput(text="the user prefers tea", kind="fact")
    )

    assert not result.is_error
    assert result.preview == "Remembered"

    hits = await store.search("tea")
    assert len(hits) == 1
    assert hits[0].text == "the user prefers tea"
    assert hits[0].kind == "fact"
    # Direct writes retain the tool call as evidence.
    assert hits[0].source_ref is not None
    assert hits[0].source_ref.kind == "tool_call"
    assert hits[0].source_ref.ref == "t1"


async def test_remember_defaults_kind_to_fact(store: RecordStore, tmp_path: Path):
    execution = _execution(
        store,
        _reconciler(tmp_path, store, {"op": "ADD", "text": "a loose observation", "kind": "fact"}),
    )
    await remember(execution, RememberInput(text="a loose observation"))
    hits = await store.search("observation")
    assert hits[0].kind == "fact"


async def test_remember_does_not_double_write_duplicate(store: RecordStore, tmp_path: Path):
    """The cheap pre-write dedup confirms the existing record instead of minting
    a second copy when the text is lexically equivalent."""
    execution = _execution(
        store,
        _reconciler(tmp_path, store, {"op": "ADD", "text": "the user prefers tea", "kind": "fact"}),
    )
    first = await remember(execution, RememberInput(text="the user prefers tea", kind="fact"))
    assert first.preview == "Remembered"

    execution.ctx.services[MEMORY_RECONCILER_SERVICE] = None
    again = await remember(execution, RememberInput(text="The user prefers tea.", kind="fact"))
    assert again.preview == "Already known"

    hits = await store.search("tea")
    assert len(hits) == 1  # only one record, no duplicate


async def test_remember_keeps_complementary_fact_selected_as_add(store: RecordStore, tmp_path: Path):
    await store.add("the user prefers tea")
    execution = _execution(
        store,
        _reconciler(
            tmp_path,
            store,
            {"op": "ADD", "text": "the user dislikes coffee", "kind": "fact"},
        ),
    )

    other = await remember(execution, RememberInput(text="the user dislikes coffee", kind="fact"))
    assert other.preview == "Remembered"

    assert len({h.id for h in await store.search("the user")}) == 2


@pytest.mark.parametrize("decision", ["ADD", "SUPERSEDE"])
async def test_remember_applies_model_selected_contradiction(
    store: RecordStore, tmp_path: Path, decision: str
):
    old = await store.add("the user works at Acme")
    raw = {"op": decision, "text": "the user works at Globex", "kind": "fact"}
    if decision == "SUPERSEDE":
        raw["id"] = old.id
    execution = _execution(store, _reconciler(tmp_path, store, raw))

    result = await remember(execution, RememberInput(text="the user works at Globex"))

    assert not result.is_error
    active = await store.list(limit=None, scopes=None)
    assert any(record.text == "the user works at Globex" for record in active)
    assert len(active) == (2 if decision == "ADD" else 1)


async def test_remember_nonidentical_candidate_errors_when_reconciliation_unavailable(store: RecordStore):
    await store.add("the user prefers green tea")

    result = await remember(_execution(store), RememberInput(text="the user prefers tea"))

    assert result.is_error
    assert "reconciliation" in result.content.lower()
    assert [record.text for record in await store.list(limit=None, scopes=None)] == ["the user prefers green tea"]


async def test_remember_includes_triggering_user_message_evidence(store: RecordStore):
    class Reconciler:
        sources = ()

        async def reconcile_direct_memory(self, **kwargs):
            self.sources = kwargs["sources"]
            return (RecordOperation.noop(),)

    class Sessions:
        async def list_messages(self, session_id, limit):
            return {
                "messages": [
                    {
                        "message_id": "user-message-1",
                        "seq": 7,
                        "role": "user",
                        "created_at": "2026-07-12T10:00:00Z",
                    }
                ]
            }

    reconciler = Reconciler()
    execution = _execution(store, reconciler)
    execution.ctx.services["session"] = Sessions()

    result = await remember(execution, RememberInput(text="the user prefers tea"))

    assert result.preview == "Already known"
    assert [(source.kind, source.ref) for source in reconciler.sources] == [
        ("tool_call", "t1"),
        ("chat_message", "user-message-1"),
    ]


async def test_memory_tools_return_after_committed_mutation_when_artifact_sync_fails(
    store: RecordStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    artifacts_dir = tmp_path / "artifacts"
    outside = tmp_path / "outside"
    artifacts_dir.mkdir()
    outside.mkdir()
    _symlink_or_skip(artifacts_dir / "changelog.md", outside / "missing.md")
    monkeypatch.setattr(
        memory_tools,
        "get_config",
        lambda: types.SimpleNamespace(memory_db_path=store._db_path, memory_artifacts_dir=artifacts_dir),
    )
    execution = _execution(
        store,
        _reconciler(
            tmp_path,
            store,
            {"op": "ADD", "text": "artifact sync failures should not mask writes", "kind": "fact"},
        ),
    )

    remembered = await remember(execution, RememberInput(text="artifact sync failures should not mask writes"))

    assert remembered.preview == "Remembered"
    assert await store.search("artifact sync failures")

    forgotten = await forget(execution, ForgetInput(query="artifact sync failures"))

    assert forgotten.preview == "Forgotten"
    assert "artifact sync failures should not mask writes" in forgotten.content
    assert await store.search("artifact sync failures") == []


# --- recall -------------------------------------------------------------------


async def test_recall_returns_hybrid_hits(store: RecordStore):
    execution = _execution(store)
    await store.add("the user lives in Berlin")
    await store.add("the user enjoys hiking")

    result = await recall(execution, RecallInput(query="Berlin"))
    assert not result.is_error
    assert "Berlin" in result.content


async def test_recall_no_matches(store: RecordStore):
    execution = _execution(store)
    await store.add("the user likes tea")
    result = await recall(execution, RecallInput(query="quantum chromodynamics"))
    assert result.preview == "No matches"


# --- forget -------------------------------------------------------------------


async def test_forget_deletes_best_match(store: RecordStore):
    execution = _execution(store)
    await store.add("the user dislikes coffee")

    result = await forget(execution, ForgetInput(query="coffee"))
    assert result.preview == "Forgotten"
    assert "coffee" in result.content.lower()
    assert await store.search("coffee") == []


async def test_forget_validates_retract_with_tool_evidence():
    class Store:
        applied = None

        async def search(self, *args, **kwargs):
            return [types.SimpleNamespace(id="record", text="the user dislikes coffee", kind="fact", scope_kind="user", scope_key=None)]

        async def list(self, *args, **kwargs):
            return [types.SimpleNamespace(id="record", text="the user dislikes coffee", kind="fact", scope_kind="user", scope_key=None)]

        def apply_operations(self, operations, sources, **kwargs):
            self.applied = (operations, sources)

    store = Store()

    result = await forget(_execution(store), ForgetInput(query="coffee"))

    assert result.preview == "Forgotten"
    operations, sources = store.applied
    assert len(operations) == 1
    assert operations[0].op == "RETRACT"
    assert operations[0].target_ids == ("record",)
    assert sources[0].kind == "tool_call"
    assert sources[0].ref == "t1"


async def test_forget_lists_other_matches(store: RecordStore):
    execution = _execution(store)
    await store.add("the user likes green tea")
    await store.add("the user likes black tea")

    result = await forget(execution, ForgetInput(query="tea"))
    assert result.preview == "Forgotten"
    assert "Other matches" in result.content
    remaining = await store.search("tea")
    assert len(remaining) == 1


async def test_forget_not_found(store: RecordStore):
    execution = _execution(store)
    result = await forget(execution, ForgetInput(query="nothing stored"))
    assert result.preview == "Not found"


# --- unavailable service (shape preserved) ------------------------------------


async def test_remember_unavailable_service_errors():
    ctx = types.SimpleNamespace(services={}, session_id="s1", area=None)
    execution = types.SimpleNamespace(ctx=ctx, tool_id="t1")

    result = await remember(execution, RememberInput(text="anything"))
    assert result.is_error
    assert "not available" in result.content
