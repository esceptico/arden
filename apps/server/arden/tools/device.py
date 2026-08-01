"""Tools that execute on a connected client executor (the user's device).

The Python handlers here never do the work: a client-placed tool is routed
to the device by the ExecutionRouter before the registry executes. The
bodies run only when no client execution backend is wired at all, and they
refuse — device tools must not silently fall back to server execution.
"""

from pydantic import BaseModel, Field

from arden.tools.core.base import ToolResult
from arden.tools.core.context import ToolExecution
from arden.tools.core.function import tool
from arden.tools.core.types import ToolAction, ToolPlacement, ToolPolicy, ToolScope

READ_DEVICE_FILE_DESCRIPTION = """Read a file from the user's device (the machine running the desktop app).

Use for files that live on the user's own computer rather than the server
workspace. Requires the desktop app to be running; fails cleanly when no
device is connected."""


class ReadDeviceFileInput(BaseModel):
    path: str = Field(description="Absolute path of the file on the user's device.")
    offset: int = Field(default=1, ge=1, description="1-based line to start reading from.")
    limit: int = Field(default=2000, ge=1, le=10000, description="Maximum lines to return.")


async def read_device_file(execution: ToolExecution, args: ReadDeviceFileInput) -> ToolResult:
    return ToolResult.failure(
        code="no_client_execution",
        message="read_device_file requires a connected device executor, and this server has none configured.",
        preview="No device executor",
        recovery_action="Use server workspace tools instead, or start the desktop app.",
    )


read_device_file_tool = tool(
    display_name="ReadDeviceFile",
    display_description="Read a file from the user's device.",
    description=READ_DEVICE_FILE_DESCRIPTION,
    input_model=ReadDeviceFileInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, placement=ToolPlacement.CLIENT),
    execute=read_device_file,
)
