from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from arden.events.sse import TodoUpdatedEvent
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope


class TodoStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class TodoItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=500, description="Short imperative task description.")
    status: TodoStatus = Field(description="Current task status.")


class TodoUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    explanation: str | None = Field(default=None, max_length=2_000, description="Optional short reason for the update.")
    items: list[TodoItemInput] = Field(min_length=1, max_length=50, description="Complete current todo list.")

    @model_validator(mode="after")
    def validate_single_in_progress(self) -> "TodoUpdateInput":
        count = sum(1 for item in self.items if item.status == TodoStatus.IN_PROGRESS)
        if count > 1:
            raise ValueError("At most one todo can be in_progress.")
        return self


def _item_to_dict(item: TodoItemInput) -> dict[str, str]:
    return {"content": item.content, "status": item.status.value}


async def todo_update(execution: ToolExecution, args: TodoUpdateInput) -> ToolResult:
    items = [_item_to_dict(item) for item in args.items]
    completed = sum(1 for item in items if item["status"] == TodoStatus.COMPLETED.value)
    preview = f"{completed}/{len(items)} done"
    data = {"items": items, "explanation": args.explanation}

    if execution.ctx.io.emit:
        await execution.ctx.io.emit(
            TodoUpdatedEvent(
                run_id=execution.ctx.run.run_id,
                tool_call_id=execution.tool_id,
                explanation=args.explanation,
                items=items,
            )
        )

    # The agent's list supersedes any manual edit the user made (the override
    # was already injected into this run's context). The session-level slot is
    # what the sidebar shows; an all-completed list RETIRES it — finished work
    # must not linger as an eternal unresolved item.
    session_service = execution.ctx.services.get("session")
    session_id = execution.ctx.session_state.session_id
    if session_service is not None and hasattr(session_service, "clear_todo_override"):
        await session_service.clear_todo_override(session_id)
    if session_service is not None and hasattr(session_service, "set_session_todos"):
        if completed == len(items):
            await session_service.clear_session_todos(session_id)
        else:
            await session_service.set_session_todos(session_id, items, args.explanation)

    return ToolResult(content="Todo list updated.", preview=preview, data=data)


todo_update_tool = tool(
    display_name="Update Todos",
    display_description="Update the task todo list.",
    description=(
        "Update the visible todo list for complex work. Use it for multi-step tasks, explicit todo/list requests, "
        "or when requirements change. Keep the list current, keep exactly one item in_progress when actively working, "
        "and do not mark an item completed until the work is actually verified. When the work is finished, send the "
        "final list with every item completed — that retires the list. Replace items that became irrelevant instead "
        "of leaving them pending forever."
    ),
    input_model=TodoUpdateInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({"session"}),
        audit=False,
        offload=False,
        destructive=True,
        open_world=False,
        idempotent=True,
    ),
    execute=todo_update,
    kind="todo",
)
