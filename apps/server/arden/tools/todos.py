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


class UpdateTodosInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    explanation: str | None = Field(default=None, max_length=2_000, description="Optional short reason for the update.")
    items: list[TodoItemInput] = Field(min_length=1, max_length=50, description="Complete current todo list.")

    @model_validator(mode="after")
    def validate_single_in_progress(self) -> "UpdateTodosInput":
        count = sum(1 for item in self.items if item.status == TodoStatus.IN_PROGRESS)
        if count > 1:
            raise ValueError("At most one todo can be in_progress.")
        return self


def _item_to_dict(item: TodoItemInput) -> dict[str, str]:
    return {"content": item.content, "status": item.status.value}


async def update_todos(execution: ToolExecution, args: UpdateTodosInput) -> ToolResult:
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
    # was already injected into this run's context).
    session_service = execution.ctx.services.get("session")
    if session_service is not None and hasattr(session_service, "clear_todo_override"):
        await session_service.clear_todo_override(execution.ctx.session_state.session_id)

    return ToolResult(content="Todo list updated.", preview=preview, data=data)


update_todos_tool = tool(
    display_name="Update Todos",
    description=(
        "Update the visible todo list for complex work. Use it for multi-step tasks, explicit todo/list requests, "
        "or when requirements change. Keep the list current, keep exactly one item in_progress when actively working, "
        "and do not mark an item completed until the work is actually verified."
    ),
    input_model=UpdateTodosInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({"session"}),
        audit=False,
        offload=False,
    ),
    execute=update_todos,
    kind="todo",
)
