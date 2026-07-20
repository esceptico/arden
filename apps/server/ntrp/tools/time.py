from datetime import UTC, datetime

from ntrp.tools.core import EmptyInput, ToolResult, tool
from ntrp.tools.core.collections import format_timestamp
from ntrp.tools.core.context import ToolExecution
from ntrp.tools.core.types import ToolAction, ToolPolicy, ToolScope


async def current_time(execution: ToolExecution, args: EmptyInput) -> ToolResult:
    now = datetime.now().astimezone()
    formatted = format_timestamp(now)
    return ToolResult(content=formatted, preview=formatted, data={"timestamp": formatted, "utc": format_timestamp(now.astimezone(UTC))})


current_time_tool = tool(
    display_name="Current Time",
    description="Get the current date and time.",
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
    execute=current_time,
)
