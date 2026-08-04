from pathlib import Path

from pydantic import BaseModel, Field

from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPlacement, ToolPolicy, ToolScope

BLOCKED_PATTERNS = frozenset(
    {
        "rm -rf /",
        "rm -rf ~",
        "rm -rf *",
        "dd if=",
        "mkfs",
        "fdisk",
        ":(){:|:&};:",
        "> /dev/sd",
        "chmod -R 777 /",
    }
)

BASH_DESCRIPTION = """Execute a bash command on the user's device (the machine running the desktop app).

Each command runs in a fresh subprocess — no state (env vars, shell functions, cwd) persists between calls. Commands run in the area's default cwd when set, otherwise in the executor's working directory. Use the working_dir parameter to run in a different directory instead of 'cd'.

PREFER OTHER TOOLS:
- For listing/finding files: use file_list() or file_find()
- For searching file content: use file_search_text()
- For reading files: use file_read()
- For editing/writing files: load the files group, then use file_edit() or file_write()

USE bash FOR:
- System commands: git, npm, pip, brew
- File operations that do not have a native tool yet: mkdir, cp, mv
- Checking system state: pwd, whoami, date

Requires the desktop app to be running; fails cleanly when no device is
connected. Every Bash command requires interactive approval and cannot run in
a headless auto-approved session. The small denylist is defense-in-depth, not
the security boundary."""


def is_blocked_command(command: str) -> bool:
    cmd_lower = command.lower().strip()
    return any(blocked in cmd_lower for blocked in BLOCKED_PATTERNS)


class BashInput(BaseModel):
    command: str = Field(description="The shell command to execute")
    working_dir: str | None = Field(default=None, description="Working directory (optional, defaults to current)")


def _working_dir(execution: ToolExecution, args: BashInput) -> str | None:
    return args.working_dir or (execution.ctx.area.default_cwd if execution.ctx.area else None)


async def approve_bash(execution: ToolExecution, args: BashInput) -> ApprovalInfo | None:
    cwd = _working_dir(execution, args) or str(Path.cwd())
    classification = "Blocked" if is_blocked_command(args.command) else "Command"
    return ApprovalInfo(
        description="Run shell command",
        preview=f"{classification}: {args.command[:1_200]}\nCWD: {cwd}",
        diff=None,
    )


async def run_bash(execution: ToolExecution, args: BashInput) -> ToolResult:
    # bash is client-placed: the ExecutionRouter dispatches it to the device
    # executor before the registry runs. This body executes only when no
    # client execution backend is wired, and it refuses — shell commands
    # never silently fall back to server execution.
    return ToolResult.failure(
        code="no_client_execution",
        message="bash runs on the user's device, and this server has no client execution backend configured.",
        preview="No device executor",
        recovery_action="Start the desktop app, or use server-side tools instead.",
    )


bash_tool = tool(
    display_name="Bash",
    display_description="Run a shell command on the user's device.",
    description=BASH_DESCRIPTION,
    input_model=BashInput,
    policy=ToolPolicy(
        action=ToolAction.EXECUTE,
        scope=ToolScope.INTERNAL,
        placement=ToolPlacement.CLIENT,
        requires_approval=True,
        allow_approval_bypass=False,
        concurrency_group="filesystem",
    ),
    approval=approve_bash,
    execute=run_bash,
)
