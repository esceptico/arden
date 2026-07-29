"""Per-automation tool scoping — allowlist-only patterns applied as the hard
outer gate in ToolRegistry.get_schemas (design learned from dex's toolset:
no denylist, narrow the allowlist instead)."""

from datetime import UTC, datetime

import pytest

from arden.automation.models import Automation
from arden.automation.triggers import TimeTrigger
from arden.tools.core.scope import matches_scope


def test_matches_scope_grammar():
    assert matches_scope(("*",), "anything")
    assert matches_scope(("recall",), "recall")
    assert not matches_scope(("recall",), "recall_all")
    assert matches_scope(("slack_*",), "slack_search")
    assert not matches_scope(("slack_*",), "gmail_search")
    assert not matches_scope((), "recall")


def test_registry_scope_is_outer_gate_over_extras():
    from arden.tools.core.base import Tool
    from arden.tools.core.registry import ToolRegistry
    from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope

    reg = ToolRegistry()

    def make(action):
        class _T(Tool):
            description = "t"
            policy = ToolPolicy(action=action, scope=ToolScope.INTERNAL)

            async def execute(self, execution, **kwargs):  # pragma: no cover
                raise NotImplementedError

            def to_dict(self, name):
                return {"name": name}

        return _T()

    reg.register("slack_search", make(ToolAction.READ))
    reg.register("gmail_read", make(ToolAction.READ))
    reg.register("memory_patch", make(ToolAction.WRITE))

    names = {t["name"] for t in reg.get_schemas(scope=("slack_*",))}
    assert names == {"slack_search"}

    # scope gates even extra_names — no path widens past the allowlist
    names = {
        t["name"] for t in reg.get_schemas(read_only=True, extra_names=frozenset({"memory_patch"}), scope=("slack_*",))
    }
    assert names == {"slack_search"}

    # None = unrestricted (existing behavior untouched)
    names = {t["name"] for t in reg.get_schemas()}
    assert names == {"slack_search", "gmail_read", "memory_patch"}


@pytest.mark.asyncio
async def test_automation_tool_scope_roundtrip(tmp_path):
    import aiosqlite

    from arden.automation.store import AutomationStore

    conn = await aiosqlite.connect(tmp_path / "a.db")
    conn.row_factory = aiosqlite.Row
    store = AutomationStore(conn)
    await store.init_schema()
    trigger = TimeTrigger(at="06:30", days="daily")
    await store.save(
        Automation(
            task_id="t1",
            name="slack only",
            description=None,
            description_source=None,
            prompt="Use the allowed Slack tools.",
            model=None,
            triggers=[trigger],
            enabled=True,
            created_at=datetime.now(UTC),
            next_run_at=None,
            last_run_at=None,
            last_result=None,
            running_since=None,
            auto_approve=False,
            tool_scope=["slack_*", "current_time"],
        )
    )
    loaded = await store.get("t1")
    assert loaded.tool_scope == ["slack_*", "current_time"]

    await store.save(
        Automation(
            task_id="t2",
            name="unrestricted",
            description=None,
            description_source=None,
            prompt="Run without a tool scope.",
            model=None,
            triggers=[trigger],
            enabled=True,
            created_at=datetime.now(UTC),
            next_run_at=None,
            last_run_at=None,
            last_result=None,
            running_since=None,
            auto_approve=False,
        )
    )
    assert (await store.get("t2")).tool_scope is None
    await conn.close()


def test_with_read_floor_grants_on_top_of_reads():
    from arden.tools.core.scope import with_read_floor

    # A user scope bounds the dangerous surface, never the safe one: the
    # write grant rides on top of every read tool, deduped, order-stable.
    floor = ("list_recent_sessions", "read_session", "read_file")
    assert with_read_floor(("archive_session",), floor) == (
        "archive_session",
        "list_recent_sessions",
        "read_session",
        "read_file",
    )
    assert with_read_floor(("read_file", "bash"), floor) == (
        "read_file",
        "bash",
        "list_recent_sessions",
        "read_session",
    )


def test_registry_read_only_names_lists_only_read_tools():
    from arden.tools.core.base import Tool
    from arden.tools.core.registry import ToolRegistry
    from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope

    def make(action):
        class _T(Tool):
            description = "t"
            policy = ToolPolicy(action=action, scope=ToolScope.INTERNAL)

            async def execute(self, execution, **kwargs):  # pragma: no cover
                raise NotImplementedError

            def to_dict(self, name):
                return {"name": name}

        return _T()

    reg = ToolRegistry()
    reg.register("slack_search", make(ToolAction.READ))
    reg.register("read_file", make(ToolAction.READ))
    reg.register("slack_post_message", make(ToolAction.WRITE))

    assert set(reg.read_only_names()) == {"slack_search", "read_file"}


def test_executor_scope_adds_read_floor_without_unrelated_writes():
    from arden.tools.executor import ToolExecutor

    executor = ToolExecutor(get_services=lambda: {"wiki": object(), "wiki_post_commit": object()})
    names = {schema["function"]["name"] for schema in executor.get_tools(scope=("create_wiki_page",))}

    assert {"create_wiki_page", "list_wiki_pages", "read_wiki_page", "wiki_links"} <= names
    assert "edit_wiki_page" not in names
    assert "archive_wiki_page" not in names
    assert "send_email" not in names


def test_is_custodian_task_id_matches_only_bare_area_tasks():
    from arden.areas.agent import is_custodian_task_id

    assert is_custodian_task_id("area:ops")
    assert not is_custodian_task_id("area:ops:digest")  # child automation
    assert not is_custodian_task_id("congenial-caracal")
