import asyncio

import pytest
from pydantic import BaseModel, ConfigDict, Field

from arden.agent import ToolResult, ToolStarted
from arden.events.sse import ToolCallArgsEvent, ToolCallStartEvent, agent_events_to_sse
from arden.tools.core.base import RESERVED_ARG_KEYS, TITLE_ARG, Tool
from arden.tools.core.registry import ToolRegistry
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope


class _StrictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # mirrors EmptyInput / todos
    path: str


class _StrictTool(Tool):
    description = "reads a path"
    policy = ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL)
    input_model = _StrictIn

    async def execute(self, execution, **kwargs):  # type: ignore[override]
        return ToolResult(content=f"ok:{kwargs['path']}", preview="ok")


class _TitleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str


class _TitleTool(Tool):
    description = "uses a real title"
    policy = ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL)
    input_model = _TitleIn

    async def execute(self, execution, **kwargs):  # type: ignore[override]
        return ToolResult(content=kwargs["title"], preview="ok")


class _CollisionIn(BaseModel):
    display_title: str = Field(alias=TITLE_ARG)


class _CollisionTool(_TitleTool):
    input_model = _CollisionIn


def test_title_is_injected_into_every_tool_schema_as_optional():
    params = _StrictTool().to_dict("readish")["function"]["parameters"]
    assert TITLE_ARG in params["properties"]
    assert TITLE_ARG not in params["required"]  # optional — never forces the model
    # Injected first so it streams before the real args.
    assert next(iter(params["properties"])) == TITLE_ARG


def test_title_is_stripped_before_execute_even_for_forbid_models():
    reg = ToolRegistry()
    reg.register("readish", _StrictTool(), source="_system")
    # The model emits display metadata alongside the real arg; an extra="forbid"
    # input model would raise if it reached the tool — the registry must strip it.
    res = asyncio.run(reg.execute("readish", None, {TITLE_ARG: "Reading the doc", "path": "a.py"}))
    assert not res.is_error
    assert res.content == "ok:a.py"


def test_reserved_keys_cover_title():
    assert TITLE_ARG in RESERVED_ARG_KEYS


def test_real_title_argument_is_not_reserved_or_stripped():
    params = _TitleTool().to_dict("publish")["function"]["parameters"]
    assert params["properties"]["title"]["type"] == "string"
    assert "title" in params["required"]
    assert TITLE_ARG != "title"

    reg = ToolRegistry()
    reg.register("publish", _TitleTool(), source="_system")
    result = asyncio.run(
        reg.execute(
            "publish",
            None,
            {"title": "Quarterly report", TITLE_ARG: "Publishing the report"},
        )
    )

    assert result.content == "Quarterly report"


def test_tool_schema_rejects_reserved_display_metadata_collision():
    with pytest.raises(ValueError, match="reserved tool argument"):
        _CollisionTool().to_dict("collision")


def test_display_title_is_projected_separately_from_real_arguments():
    events = agent_events_to_sse(
        ToolStarted(
            tool_id="call-1",
            name="readish",
            display_name="Readish",
            args={TITLE_ARG: "Reading the doc", "path": "a.py"},
        )
    )

    started = next(event for event in events if isinstance(event, ToolCallStartEvent))
    args = next(event for event in events if isinstance(event, ToolCallArgsEvent))
    assert started.description == "Reading the doc"
    assert TITLE_ARG not in args.delta
    assert '"path": "a.py"' in args.delta
