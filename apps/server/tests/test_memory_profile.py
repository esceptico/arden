"""resident_profile — the always-resident memory block (ntrp/memory/profile.py).

Areas directives + durable user facts + pins from the flat pool into the
system-prompt block that rides every turn. Hermetic: a tmp memory.db, FTS-only.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from ntrp.memory.profile import DIRECTIVE_CHAR_BUDGET, FACT_CHAR_BUDGET, ROOT_MAP_CHAR_BUDGET, resident_profile
from ntrp.memory.records import RecordStore

pytestmark = pytest.mark.asyncio


def _store(tmp_path: Path) -> RecordStore:
    return RecordStore(tmp_path / "memory.db", search_index=None)


async def test_profile_carries_directives_and_facts_not_sources(tmp_path: Path):
    store = _store(tmp_path)
    await store.add("respond tersely", kind="directive", scope_kind="global")
    await store.add("the user lives in Munich", kind="fact", scope_kind="user")
    await store.add("https://example.com", kind="source", scope_kind="user")

    block = await resident_profile(store)

    assert block.startswith("## Profile")
    assert "respond tersely" in block
    assert "the user lives in Munich" in block
    assert "example.com" not in block  # sources are not standing memory
    # directives sort ahead of facts so behaviour rules survive truncation
    assert block.index("respond tersely") < block.index("Munich")
    await store.close()


async def test_profile_is_none_without_a_store():
    assert await resident_profile(None) is None


async def test_profile_includes_compact_root_map_without_nested_file_content(tmp_path: Path):
    from ntrp.memory.file_store import FilePageStore

    root = tmp_path / "memory"
    (root / "research" / "models").mkdir(parents=True)
    (root / "research" / "README.md").write_text("---\nsummary: Research library\n---\n", encoding="utf-8")
    (root / "research" / "models" / "private.md").write_text("# Secret experiment\n\nDo not embed this body.\n", encoding="utf-8")
    store = FilePageStore(root)
    await store.open()

    block = await resident_profile(store)

    assert "## Memory map" in block
    assert "research/ — Research library" in block
    assert "private.md" not in block
    assert "Do not embed this body" not in block
    await store.close()


async def test_profile_root_map_enforces_budget_for_one_large_description(tmp_path: Path):
    from ntrp.memory.file_store import FilePageStore

    root = tmp_path / "memory"
    root.mkdir()
    (root / "notes.md").write_text(f"---\nsummary: {'x' * 5000}\n---\n", encoding="utf-8")
    store = FilePageStore(root)
    await store.open()

    block = await resident_profile(store)

    assert len(block) <= ROOT_MAP_CHAR_BUDGET
    await store.close()


async def test_profile_root_map_budget_counts_row_separators(tmp_path: Path):
    from ntrp.memory.file_store import FilePageStore

    root = tmp_path / "memory"
    root.mkdir()
    for index in range(200):
        (root / f"note-{index:03}.txt").write_text("short description\n", encoding="utf-8")
    store = FilePageStore(root)
    await store.open()

    block = await resident_profile(store)
    assert len(block) <= ROOT_MAP_CHAR_BUDGET
    await store.close()


async def test_profile_surfaces_learned_playbook(tmp_path: Path):
    """Continual learning: distilled `lesson` records ride in the resident block as a
    Playbook section, so the agent applies what it learned."""
    store = _store(tmp_path)
    await store.add("the user lives in Munich", kind="fact", scope_kind="user")
    await store.add("Verify against the running system before reporting status.", kind="lesson", scope_kind="global")

    block = await resident_profile(store)

    assert "## Playbook (learned)" in block
    assert "Verify against the running system before reporting status." in block
    assert "the user lives in Munich" in block  # profile + playbook coexist
    await store.close()


async def test_profile_respects_char_budget(tmp_path: Path):
    store = _store(tmp_path)
    await store.add("ALWAYS keep this rule", kind="directive", scope_kind="global")
    for i in range(400):
        await store.add(f"fact number {i} " + "x" * 40, kind="fact", scope_kind="user")

    block = await resident_profile(store)

    assert len(block) <= DIRECTIVE_CHAR_BUDGET + FACT_CHAR_BUDGET + len("## Profile\n\n") + 160
    assert "ALWAYS keep this rule" in block  # the directive is never truncated away
    await store.close()


async def test_verbose_directives_do_not_starve_facts(tmp_path: Path):
    store = _store(tmp_path)
    for i in range(20):
        await store.add("rule " + "y" * 300 + f" {i}", kind="directive", scope_kind="global")
    await store.add("the user is named Tim", kind="fact", scope_kind="user")

    block = await resident_profile(store)

    assert "the user is named Tim" in block  # facts get their own guaranteed area
    await store.close()


async def test_playbook_is_salience_ranked_not_recency_ranked(tmp_path: Path):
    """A char-budgeted playbook must carry the BEST lessons: a high-imp old lesson
    outranks a flood of newer low-imp ones."""
    from ntrp.memory.file_store import FilePageStore
    from ntrp.memory.models import SourceRef

    store = FilePageStore(tmp_path / "memory")
    await store.open()
    keeper = await store.add("KEEPER: never push to origin without approval.", kind="lesson", source_ref=SourceRef("curator", ""))
    await store.set_pinned(keeper.id, False)  # ensure normal path
    # score the keeper high; bury it under many newer low-imp filler lessons
    for path, line in [store._find(keeper.id)]:
        store._replace_ledger_entry(path, replace(line, imp=9))
    for i in range(80):
            r = await store.add(f"filler lesson {i} " + "y" * 30, kind="lesson", source_ref=SourceRef("curator", ""))
            found = store._find(r.id)
            store._replace_ledger_entry(found[0], replace(found[1], imp=1))

    block = await resident_profile(store)

    assert "KEEPER: never push to origin" in block, "high-salience lesson survives the budget"
    await store.close()
