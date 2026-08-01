from pathlib import Path

import pytest
import pytest_asyncio

import arden.database as database
from arden.skills.device_store import DeviceSkillStore
from arden.skills.registry import SkillMeta, SkillRegistry

SKILL_MD = "---\nname: mac-notes\ndescription: Search local notes\n---\n\n# Steps\nDo the thing.\n"


@pytest_asyncio.fixture
async def store(tmp_path):
    conn = await database.connect(tmp_path / "sessions.db")
    store = DeviceSkillStore(conn)
    await store.init_schema()
    yield store
    await conn.close()


def _ad(name: str, sha: str, description: str = "d") -> dict:
    return {"name": name, "description": description, "sha256": sha}


# -- store --


@pytest.mark.asyncio
async def test_snapshot_reports_needed_bodies_then_stores_them(store):
    needed = await store.replace_snapshot("exec_1", [_ad("mac-notes", "aaa"), _ad("other", "bbb")])
    assert needed == ["mac-notes", "other"]

    await store.save_bodies("exec_1", [{"name": "mac-notes", "sha256": "aaa", "body": SKILL_MD}])
    loaded = await store.all_loaded()
    assert [skill.name for skill in loaded] == ["mac-notes"]
    assert loaded[0].body == SKILL_MD


@pytest.mark.asyncio
async def test_unchanged_sha_keeps_body_changed_sha_requests_again(store):
    await store.replace_snapshot("exec_1", [_ad("mac-notes", "aaa")])
    await store.save_bodies("exec_1", [{"name": "mac-notes", "sha256": "aaa", "body": SKILL_MD}])

    assert await store.replace_snapshot("exec_1", [_ad("mac-notes", "aaa")]) == []
    assert await store.replace_snapshot("exec_1", [_ad("mac-notes", "NEW")]) == ["mac-notes"]
    assert await store.all_loaded() == []


@pytest.mark.asyncio
async def test_deletions_propagate(store):
    await store.replace_snapshot("exec_1", [_ad("mac-notes", "aaa"), _ad("gone", "bbb")])
    await store.save_bodies("exec_1", [{"name": "gone", "sha256": "bbb", "body": "x"}])

    await store.replace_snapshot("exec_1", [_ad("mac-notes", "aaa")])
    assert await store.all_loaded() == []  # gone deleted; mac-notes has no body yet


@pytest.mark.asyncio
async def test_stale_body_upload_is_ignored(store):
    await store.replace_snapshot("exec_1", [_ad("mac-notes", "NEW")])
    await store.save_bodies("exec_1", [{"name": "mac-notes", "sha256": "OLD", "body": "stale"}])
    assert await store.all_loaded() == []


@pytest.mark.asyncio
async def test_empty_snapshot_clears_executor(store):
    await store.replace_snapshot("exec_1", [_ad("mac-notes", "aaa")])
    await store.save_bodies("exec_1", [{"name": "mac-notes", "sha256": "aaa", "body": "x"}])
    assert await store.replace_snapshot("exec_1", []) == []
    assert await store.all_loaded() == []


# -- registry merge --


def _device_entry(name: str, description: str = "From the device"):
    meta = SkillMeta(name=name, description=description, path=Path("~/.agents/skills") / name, location="device")
    return (meta, SKILL_MD)


def test_registry_merges_device_skills():
    registry = SkillRegistry()
    registry.set_device_skills([_device_entry("mac-notes")])

    assert registry.get("mac-notes") is not None
    assert registry.get("mac-notes").location == "device"
    assert "mac-notes" in registry.names
    assert registry.load_body("mac-notes") == "# Steps\nDo the thing."
    assert "<name>mac-notes</name>" in registry.to_prompt_xml()
    assert bool(registry)


def test_directory_skills_win_name_conflicts(tmp_path):
    (tmp_path / "mac-notes").mkdir()
    (tmp_path / "mac-notes" / "SKILL.md").write_text(
        "---\nname: mac-notes\ndescription: Server copy\n---\n\n# Server body\n"
    )
    registry = SkillRegistry()
    registry.load([(tmp_path, "global")])
    registry.set_device_skills([_device_entry("mac-notes", description="Device copy")])

    assert registry.get("mac-notes").description == "Server copy"
    assert registry.load_body("mac-notes") == "# Server body"
    assert len(registry) == 1


def test_device_skills_survive_reload(tmp_path):
    registry = SkillRegistry()
    registry.set_device_skills([_device_entry("mac-notes")])
    registry.reload([(tmp_path, "global")])
    assert registry.get("mac-notes") is not None


def test_render_skill_xml_for_device_skill():
    registry = SkillRegistry()
    registry.set_device_skills([_device_entry("mac-notes")])
    xml = registry.render_skill_xml("mac-notes", "find todo")
    assert xml is not None
    assert "# Steps" in xml
    assert "ARGUMENTS: find todo" in xml
