"""remember()/recall()/forget() tools over the FLAT RecordStore (ntrp/tools/memory.py).

The tools add/search/delete atomic records via a real tmp RecordStore
(`search_index=None` -> FTS-only, no embeddings, no search.db). No scope, no lens
tool. A minimal namespace stands in for ToolExecution (the executors read only
ctx.services / ctx.session_id and execution.tool_id).
"""

import json
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

import ntrp.database as database
import ntrp.tools.memory as memory_tools
from ntrp.context.models import SessionState
from ntrp.context.store import SessionStore
from ntrp.memory.curator import Curator
from ntrp.memory.file_store import FilePageStore
from ntrp.memory.models import SourceRef
from ntrp.memory.reconciler import RecordOperation
from ntrp.memory.records import RecordStore
from ntrp.services.session import SessionService
from ntrp.tools.memory import (
    MEMORY_RECONCILER_SERVICE,
    MEMORY_RECORDS_SERVICE,
    ForgetInput,
    RecallInput,
    RememberInput,
    SearchMemoryCandidatesInput,
    approve_forget,
    forget,
    recall,
    remember,
    search_memory_candidates,
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


async def _forget_by_query(execution, query: str):
    found = await search_memory_candidates(execution, SearchMemoryCandidatesInput(query=query))
    candidate = found.data["candidates"][0]
    return await forget(
        execution,
        ForgetInput(memory_ref=candidate["memory_ref"], expected_version=candidate["version"]),
    )


async def _ledger_dependencies(tmp_path: Path, *decisions: dict):
    vault = tmp_path / "vault"
    (vault / "raw").mkdir(parents=True)
    (vault / "me.md").write_text("# Me\n", encoding="utf-8")
    (vault / "raw" / "me.md").write_text(
        "<!-- ntrp:records schema=2 page=me.md -->\n",
        encoding="utf-8",
    )
    store = FilePageStore(vault)
    await store.open()

    conn = await database.connect(tmp_path / "sessions.db")
    session_store = SessionStore(conn)
    await session_store.init_schema()
    sessions = SessionService(session_store)
    state = SessionState(session_id="s1", started_at=datetime.now(UTC))
    await sessions.save(
        state,
        [
            {
                "message_id": "user-message-1",
                "role": "user",
                "content": "Please remember this.",
                "created_at": "2026-07-12T10:00:00Z",
            }
        ],
    )

    responses = [json.dumps({"records": [decision]}) for decision in decisions]
    reconciler = Curator(
        StubLLM(*responses),
        sessions=sessions,
        model="memory-model",
        db_path=tmp_path / "curator.db",
        record_store=store,
    )
    execution = _execution(store, reconciler)
    execution.ctx.services["session"] = sessions
    return store, reconciler, execution, conn


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


async def test_remember_empty_reconciliation_decision_is_error(store: RecordStore, tmp_path: Path):
    result = await remember(
        _execution(store, _reconciler(tmp_path, store)),
        RememberInput(text="the user prefers tea"),
    )

    assert result.is_error
    assert "reconciliation" in result.content.lower()
    assert await store.list(limit=None, scopes=None) == []


async def test_remember_rejects_injected_empty_typed_decision(store: RecordStore):
    class EmptyReconciler:
        async def reconcile_direct_memory(self, **kwargs):
            return ()

    result = await remember(
        _execution(store, EmptyReconciler()),
        RememberInput(text="the user prefers tea"),
    )

    assert result.is_error
    assert result.preview == "Reconciliation unavailable"


async def test_remember_explicit_typed_noop_is_already_known(store: RecordStore, tmp_path: Path):
    result = await remember(
        _execution(store, _reconciler(tmp_path, store, {"op": "NOOP"})),
        RememberInput(text="the user prefers tea"),
    )

    assert not result.is_error
    assert result.preview == "Already known"
    assert await store.list(limit=None, scopes=None) == []


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


async def test_remember_normalizes_triggering_message_timestamp_precision(store: RecordStore):
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
                        "created_at": "2026-07-15T12:51:30.427616+00:00",
                    }
                ]
            }

    reconciler = Reconciler()
    execution = _execution(store, reconciler)
    execution.ctx.services["session"] = Sessions()

    await remember(execution, RememberInput(text="the user prefers tea"))

    source = reconciler.sources[1]
    assert source.occurred_at == "2026-07-15T12:51:30.427+00:00"
    assert source.time_precision == "millisecond"


async def test_file_store_direct_remember_commits_complement_with_complete_evidence(tmp_path: Path):
    store, reconciler, execution, conn = await _ledger_dependencies(
        tmp_path,
        {"op": "ADD", "text": "the user dislikes coffee", "kind": "fact"},
    )
    try:
        store.apply_operations(
            (RecordOperation.add("the user prefers tea"),),
            SourceRef("chat_message", "seed-message"),
        )
        execution.tool_id = "remember-complement"

        result = await remember(execution, RememberInput(text="the user dislikes coffee"))

        assert result.preview == "Remembered"
        active = await store.list(limit=None, scopes=None)
        assert {record.text for record in active} == {
            "the user prefers tea",
            "the user dislikes coffee",
        }
        complement = next(record for record in active if record.text == "the user dislikes coffee")
        assert [(source.kind, source.ref) for source in complement.sources] == [
            ("tool_call", "remember-complement"),
            ("chat_message", "user-message-1"),
        ]
        assert store.operation_batch_committed("direct-remember:s1:remember-complement")
    finally:
        await reconciler.stop()
        await store.close()
        await conn.close()


async def test_file_store_direct_contradiction_and_forget_preserve_history_and_evidence(tmp_path: Path):
    store, reconciler, execution, conn = await _ledger_dependencies(tmp_path)
    try:
        store.apply_operations(
            (RecordOperation.add("the user works at Acme"),),
            SourceRef("chat_message", "seed-message"),
        )
        old = (await store.list(limit=None, scopes=None))[0]
        await reconciler.stop()
        reconciler = Curator(
            StubLLM(
                json.dumps(
                    {
                        "records": [
                            {
                                "op": "SUPERSEDE",
                                "id": old.id,
                                "text": "the user works at Globex",
                                "kind": "fact",
                            }
                        ]
                    }
                )
            ),
            sessions=execution.ctx.services["session"],
            model="memory-model",
            db_path=tmp_path / "curator.db",
            record_store=store,
        )
        execution.ctx.services[MEMORY_RECONCILER_SERVICE] = reconciler
        execution.tool_id = "remember-contradiction"

        remembered = await remember(execution, RememberInput(text="the user works at Globex"))

        assert remembered.preview == "Remembered"
        successor = (await store.list(limit=None, scopes=None))[0]
        assert successor.text == "the user works at Globex"
        assert [(source.kind, source.ref) for source in successor.sources] == [
            ("chat_message", "seed-message"),
            ("tool_call", "remember-contradiction"),
            ("chat_message", "user-message-1"),
        ]

        execution.tool_id = "forget-contradiction"
        forgotten = await _forget_by_query(execution, "Globex")

        assert forgotten.preview == "Forgotten"
        assert await store.list(limit=None, scopes=None) == []
        history = store.history(old.id)
        assert [entry.meta.operation for entry in history] == ["record", "record", "retract"]
        assert [(source.kind, source.ref) for source in history[-1].meta.sources] == [
            ("chat_message", "seed-message"),
            ("tool_call", "remember-contradiction"),
            ("chat_message", "user-message-1"),
            ("tool_call", "forget-contradiction"),
            ("chat_message", "user-message-1"),
        ]
    finally:
        await reconciler.stop()
        await store.close()
        await conn.close()


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

    forgotten = await _forget_by_query(execution, "artifact sync failures")

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


async def test_forget_deletes_exact_versioned_ref(store: RecordStore):
    execution = _execution(store)
    await store.add("the user dislikes coffee")

    result = await _forget_by_query(execution, "coffee")
    assert result.preview == "Forgotten"
    assert "coffee" in result.content.lower()
    assert await store.search("coffee") == []


async def test_forget_validates_retract_with_tool_evidence():
    class Store:
        applied = None

        record = types.SimpleNamespace(
            id="record",
            text="the user dislikes coffee",
            kind="fact",
            scope_kind="user",
            scope_key=None,
        )

        async def search(self, *args, **kwargs):
            return [self.record]

        async def get(self, record_id):
            return self.record if record_id == self.record.id else None

        async def list(self, *args, **kwargs):
            return [self.record]

        def apply_operations(self, operations, sources, **kwargs):
            self.applied = (operations, sources)

    store = Store()

    result = await _forget_by_query(_execution(store), "coffee")

    assert result.preview == "Forgotten"
    operations, sources = store.applied
    assert len(operations) == 1
    assert operations[0].op == "RETRACT"
    assert operations[0].target_ids == ("record",)
    assert sources[0].kind == "tool_call"
    assert sources[0].ref == "t1"


async def test_search_candidates_never_deletes_ambiguous_matches(store: RecordStore):
    execution = _execution(store)
    await store.add("the user likes green tea")
    await store.add("the user likes black tea")

    result = await search_memory_candidates(execution, SearchMemoryCandidatesInput(query="tea"))

    assert result.preview == "2 candidate(s)"
    assert len(result.data["candidates"]) == 2
    assert all(candidate["memory_ref"] and candidate["version"] for candidate in result.data["candidates"])
    remaining = await store.search("tea")
    assert len(remaining) == 2


async def test_search_candidates_not_found_is_non_mutating(store: RecordStore):
    execution = _execution(store)
    result = await search_memory_candidates(execution, SearchMemoryCandidatesInput(query="nothing stored"))
    assert result.preview == "0 candidates"


async def test_forget_rejects_stale_version_without_deleting(store: RecordStore):
    execution = _execution(store)
    record = await store.add("the user likes coffee")
    found = await search_memory_candidates(execution, SearchMemoryCandidatesInput(query="coffee"))
    candidate = found.data["candidates"][0]
    await store.update(record.id, "the user dislikes coffee")

    result = await forget(
        execution,
        ForgetInput(memory_ref=candidate["memory_ref"], expected_version=candidate["version"]),
    )

    assert result.is_error
    assert result.outcome.error.code == "write_conflict"
    assert await store.get(record.id) is not None


async def test_forget_approval_previews_exact_record(store: RecordStore):
    execution = _execution(store)
    await store.add("the user dislikes coffee")
    found = await search_memory_candidates(execution, SearchMemoryCandidatesInput(query="coffee"))
    candidate = found.data["candidates"][0]

    approval = await approve_forget(
        execution,
        ForgetInput(memory_ref=candidate["memory_ref"], expected_version=candidate["version"]),
    )

    assert approval is not None
    assert approval.preview == "the user dislikes coffee"
    assert candidate["memory_ref"] in approval.description


# --- unavailable service (shape preserved) ------------------------------------


async def test_remember_unavailable_service_errors():
    ctx = types.SimpleNamespace(services={}, session_id="s1", area=None)
    execution = types.SimpleNamespace(ctx=ctx, tool_id="t1")

    result = await remember(execution, RememberInput(text="anything"))
    assert result.is_error
    assert "not available" in result.content
