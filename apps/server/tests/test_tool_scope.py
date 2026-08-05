"""Tool scoping: a filter algebra built in code, keyed by name where it persists.

Nothing stored mentions a tool, so renaming a tool cannot narrow a saved scope.
The UI and the agent may only choose between `read_only` and `all`; the
fine-grained keys belong to custodians and builtin workers.
"""

from datetime import UTC, datetime

import pytest

from arden.automation.models import Automation, create_automation_ref
from arden.automation.triggers import TimeTrigger
from arden.tools.core.base import Tool
from arden.tools.core.registry import ToolRegistry
from arden.tools.core.scope import ToolFacts, tools
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope
from arden.tools.scopes import SCOPES, SETTABLE_SCOPES, resolve


def _tool(action: ToolAction) -> Tool:
    class _T(Tool):
        description = "t"
        policy = ToolPolicy(action=action, scope=ToolScope.INTERNAL)

        async def execute(self, execution, **kwargs):  # pragma: no cover
            raise NotImplementedError

        def to_dict(self, name):
            return {"name": name}

    return _T()


FACTS = (
    ToolFacts("slack_search", ToolAction.READ, "slack", True),
    ToolFacts("slack_post_message", ToolAction.WRITE, "slack", True),
    ToolFacts("email_send", ToolAction.WRITE, "gmail", True),
    ToolFacts("recall", ToolAction.READ, "_facts", False),
    ToolFacts("memory_patch", ToolAction.WRITE, "_facts", False),
)


def _granted(scope, facts=FACTS) -> set[str]:
    return {fact.name for fact in facts if scope.matches(fact)}


def test_leaves_select_on_registry_facts_not_on_the_name_string():
    assert _granted(tools.read) == {"slack_search", "recall"}
    assert _granted(tools.system) == {"recall", "memory_patch"}
    assert _granted(tools.integrations) == {"slack_search", "slack_post_message", "email_send"}
    assert _granted(tools.source("slack")) == {"slack_search", "slack_post_message"}
    assert _granted(tools.named("recall")) == {"recall"}
    assert _granted(tools.prefix("slack_")) == {"slack_search", "slack_post_message"}
    assert _granted(tools.ALL) == {fact.name for fact in FACTS}


def test_union_intersection_and_narrowing_compose():
    assert _granted(tools.system | (tools.integrations & tools.read)) == {
        "slack_search",
        "recall",
        "memory_patch",
    }
    assert _granted(tools.integrations & ~tools.source("slack")) == {"email_send"}


def test_expressions_render_with_correct_precedence():
    assert str(tools.read | tools.named("email_send") | tools.prefix("slack_")) == "read | email_send | slack_*"
    assert str(tools.system | (tools.integrations & tools.read)) == "system | integrations & read"
    # `&` binds tighter, so a nested union must carry its own parens.
    assert str((tools.read | tools.write) & ~tools.source("slack")) == "(read | write) & ~source(slack)"


def test_a_source_selector_survives_a_tool_rename_where_a_prefix_would_not():
    renamed = (ToolFacts("slack_v2_search", ToolAction.READ, "slack", True),)
    assert _granted(tools.source("slack"), renamed) == {"slack_v2_search"}
    assert _granted(tools.prefix("slack_search"), renamed) == set()


def test_every_key_resolves_and_only_two_are_settable():
    for key in SCOPES:
        assert resolve(key) is SCOPES[key]
    assert SETTABLE_SCOPES == ("read_only", "all")
    # The fine-grained contracts exist but are never offered as a choice.
    assert {"area_observe", "area_act", "wiki_producer"} <= set(SCOPES) - set(SETTABLE_SCOPES)


def test_an_unknown_key_fails_loudly_rather_than_granting_nothing():
    with pytest.raises(ValueError, match="unknown tool scope 'read_onlyy'"):
        resolve("read_onlyy")


def test_the_settable_lever_is_read_only_versus_everything():
    assert _granted(resolve("read_only")) == {"slack_search", "recall"}
    assert _granted(resolve("all")) == {fact.name for fact in FACTS}


def test_scope_is_the_only_capability_filter_and_names_cannot_widen_it():
    """Scope replaced read_only/actions/extra_names outright. `names` (the
    deferred middleware's visible/deferred split) narrows within a scope and
    can never reach past it."""
    reg = ToolRegistry()
    reg.register("slack_search", _tool(ToolAction.READ), source="slack")
    reg.register("slack_post_message", _tool(ToolAction.WRITE), source="slack")
    reg.register("recall", _tool(ToolAction.READ), source="_facts")
    reg.register("memory_patch", _tool(ToolAction.WRITE), source="_facts")

    assert {t["name"] for t in reg.get_schemas(scope=tools.source("slack"))} == {
        "slack_search",
        "slack_post_message",
    }

    # A name outside the scope stays outside it.
    assert {
        t["name"]
        for t in reg.get_schemas(
            names={"slack_search", "memory_patch"},
            scope=tools.source("slack"),
        )
    } == {"slack_search"}

    # None = unrestricted.
    assert len(reg.get_schemas()) == 4


def test_read_only_and_named_additions_are_one_expression_now():
    """What `read_only=True, extra_names={"memory_patch"}` used to mean is a
    union in the algebra — one axis, so the two cannot disagree."""
    reg = ToolRegistry()
    reg.register("read_thing", _tool(ToolAction.READ), source="_system")
    reg.register("memory_patch", _tool(ToolAction.WRITE), source="_facts")
    reg.register("bash", _tool(ToolAction.EXECUTE), source="_system")

    assert {t["name"] for t in reg.get_schemas(scope=tools.read)} == {"read_thing"}
    assert {t["name"] for t in reg.get_schemas(scope=tools.read | tools.named("memory_patch"))} == {
        "read_thing",
        "memory_patch",
    }


def test_registry_owns_the_system_versus_service_classification():
    """No caller encodes the `_` prefix — that was the original coupling."""
    reg = ToolRegistry()
    reg.register("recall", _tool(ToolAction.READ), source="_facts")
    reg.register("slack_search", _tool(ToolAction.READ), source="slack")

    assert reg.facts("recall").external is False
    assert reg.facts("slack_search").external is True
    assert {t["name"] for t in reg.get_schemas(scope=tools.system)} == {"recall"}


def test_read_only_never_smuggles_in_a_write():
    from arden.tools.executor import ToolExecutor

    executor = ToolExecutor(get_services=lambda: {"wiki": object(), "wiki_post_commit": object()})
    granted = {s["function"]["name"] for s in executor.get_tools(scope=resolve("read_only"))}

    assert {"wiki_list_pages", "wiki_read_page", "wiki_links"} <= granted
    for name in ("wiki_create_page", "wiki_edit_page", "wiki_patch_page", "wiki_archive_page", "email_send"):
        assert name not in granted, name


@pytest.mark.asyncio
async def test_automation_scope_roundtrips_as_a_key(tmp_path):
    import aiosqlite

    from arden.automation.store import AutomationStore

    conn = await aiosqlite.connect(tmp_path / "a.db")
    conn.row_factory = aiosqlite.Row
    store = AutomationStore(conn)
    await store.init_schema()

    def automation(task_id: str, scope: str) -> Automation:
        return Automation(
            task_id=task_id,
            automation_ref=create_automation_ref(task_id, task_id),
            name=task_id,
            description=None,
            description_source=None,
            prompt="Use the allowed tools.",
            model=None,
            triggers=[TimeTrigger(at="06:30", days="daily")],
            enabled=True,
            created_at=datetime.now(UTC),
            next_run_at=None,
            last_run_at=None,
            last_result=None,
            running_since=None,
            auto_approve=False,
            tool_scope=scope,
        )

    for key in ("read_only", "all", "area_observe", "wiki_producer"):
        await store.save(automation(f"t-{key}", key))
        assert (await store.get(f"t-{key}")).tool_scope == key

    await conn.close()


def test_is_custodian_task_id_matches_only_bare_area_tasks():
    from arden.areas.agent import is_custodian_task_id

    assert is_custodian_task_id("area:ops")
    assert not is_custodian_task_id("area:ops:digest")  # child automation
    assert not is_custodian_task_id("congenial-caracal")
