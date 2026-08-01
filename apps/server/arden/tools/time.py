from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from arden.config import get_config
from arden.tools.core import EmptyInput, ToolResult, tool
from arden.tools.core.collections import format_timestamp
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope


async def current_time(execution: ToolExecution, args: EmptyInput) -> ToolResult:
    # The configured user timezone, not the host's: a remote server must
    # report the user's clock.
    timezone = get_config().timezone
    now = datetime.now(ZoneInfo(timezone))
    formatted = format_timestamp(now)
    return ToolResult(
        content=formatted,
        preview=formatted,
        data={"timestamp": formatted, "timezone": timezone, "utc": format_timestamp(now.astimezone(UTC))},
    )


current_time_tool = tool(
    display_name="Current Time",
    description="Get the current date and time in the user's timezone.",
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
    execute=current_time,
)
