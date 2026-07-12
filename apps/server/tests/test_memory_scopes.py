from pathlib import Path
from types import SimpleNamespace

import pytest

from ntrp.memory.file_store import FilePageStore
from ntrp.memory.models import Kind, Record, SourceRef
from ntrp.memory.records import RecordStore
from ntrp.memory.scopes import (
    GLOBAL_SCOPE,
    USER_SCOPE,
    MemoryScope,
    apply_scope_to_source,
    project_scope,
    scope_for_write,
    scopes_for_read,
)


def area(scope="ks", pid="pid"):
    return SimpleNamespace(knowledge_scope=scope, area_id=pid)


def test_scope_for_write_rules():
    explicit = MemoryScope("session", "s0")
    assert scope_for_write(kind="fact", explicit_scope=explicit) == explicit
    assert scope_for_write(kind=Kind.DIRECTIVE, project=area()) == GLOBAL_SCOPE
    assert scope_for_write(kind="fact", project=area("ks1", "p1")) == MemoryScope("area", "p1")
    assert scope_for_write(kind="fact", project=area(None, "p1")) == MemoryScope("area", "p1")
    src = SourceRef(kind="slack", ref="C123")
    assert scope_for_write(kind="source", source_ref=src) == MemoryScope("integration", "slack:C123")
    # 'summary' is no longer a writable kind: it routes like any plain fact (user scope),
    # not to a per-session scope.
    assert scope_for_write(kind="summary", session_id="s1") == USER_SCOPE
    assert scope_for_write(kind="fact", session_id="s1") == USER_SCOPE
    assert scope_for_write(kind="fact") == USER_SCOPE


def test_scopes_for_read_and_source_mirroring():
    assert project_scope(area("ks", "p")) == MemoryScope("area", "p")
    assert scopes_for_read(project=area("ks", "p"), session_id="s") == [GLOBAL_SCOPE, USER_SCOPE, MemoryScope("area", "p")]
    # No "session" read leg — nothing writes scope_kind="session", so reads are
    # global + user (session_id is accepted but does not scope reads).
    assert scopes_for_read(session_id="s") == [GLOBAL_SCOPE, USER_SCOPE]
    src = apply_scope_to_source(SourceRef(kind="chat_turn", ref="s:t"), MemoryScope("session", "s"))
    assert src is not None
    assert src.scope_kind == "session"
    assert src.scope_key == "s"


def test_legacy_project_scope_is_an_area_alias_for_reads():
    legacy = Record(id="legacy", text="Legacy", scope_kind="project", scope_key="a1")
    assert FilePageStore._scope_ok(legacy, [("area", "a1")])


@pytest.mark.anyio
async def test_file_store_new_area_write_round_trips_canonical_scope(tmp_path: Path):
    store = FilePageStore(tmp_path / "vault")
    await store.open()

    record = await store.add("Area fact", scope_kind="area", scope_key="a1")

    assert (record.scope_kind, record.scope_key) == ("area", "a1")
    await store.close()


@pytest.mark.anyio
async def test_record_store_scoped_list_and_search(tmp_path: Path):
    store = RecordStore(tmp_path / "memory.db")
    g = await store.add("global pineapple", kind="fact", scope_kind="global")
    p = await store.add("area pineapple", kind="fact", scope_kind="area", scope_key="p1")
    await store.add("session pineapple", kind="fact", scope_kind="session", scope_key="s1")

    scoped = await store.list(scopes=[("global", None), ("area", "p1")], limit=10)
    assert {r.id for r in scoped} == {g.id, p.id}
    assert await store.list(scopes=[], limit=10) == []

    hits = await store.search("pineapple", scopes=[("area", "p1")], limit=10)
    assert [h.id for h in hits] == [p.id]
    all_hits = await store.search("pineapple", scopes=None, limit=10)
    assert {h.scope_kind for h in all_hits} == {"global", "area", "session"}
