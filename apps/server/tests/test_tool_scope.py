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


def test_read_floor_is_granted_only_when_declared():
    """The floor used to be unioned into every scoped run inside the executor,
    which made "can read anything" a property of the plumbing that no author
    had written down. It is an ordinary grant now."""
    from arden.tools.core.scope import READ_FLOOR, expand_scope

    floor = ("list_recent_sessions", "read_session", "read_file")

    # Declared: the write grant rides on top of every read tool, deduped,
    # order-stable, and the marker itself does not survive into the result.
    assert expand_scope((READ_FLOOR, "archive_session"), floor) == (
        "archive_session",
        "list_recent_sessions",
        "read_session",
        "read_file",
    )
    assert expand_scope(("read_file", READ_FLOOR, "bash"), floor) == (
        "read_file",
        "bash",
        "list_recent_sessions",
        "read_session",
    )

    # Undeclared: the scope stands exactly as authored.
    assert expand_scope(("archive_session",), floor) == ("archive_session",)


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


def test_executor_grants_the_read_floor_only_to_a_scope_that_declares_it():
    from arden.tools.core.scope import READ_FLOOR
    from arden.tools.executor import ToolExecutor

    executor = ToolExecutor(get_services=lambda: {"wiki": object(), "wiki_post_commit": object()})
    resolve = lambda scope: {s["function"]["name"] for s in executor.get_tools(scope=scope)}  # noqa: E731

    declared = resolve((READ_FLOOR, "create_wiki_page"))
    assert {"create_wiki_page", "list_wiki_pages", "read_wiki_page", "wiki_links"} <= declared
    # The floor is reads only — it never smuggles in a write the author omitted.
    assert "edit_wiki_page" not in declared
    assert "archive_wiki_page" not in declared
    assert "send_email" not in declared

    bare = resolve(("create_wiki_page",))
    assert bare == {"create_wiki_page"}


def test_is_custodian_task_id_matches_only_bare_area_tasks():
    from arden.areas.agent import is_custodian_task_id

    assert is_custodian_task_id("area:ops")
    assert not is_custodian_task_id("area:ops:digest")  # child automation
    assert not is_custodian_task_id("congenial-caracal")
