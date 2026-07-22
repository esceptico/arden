import asyncio
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from arden.constants import BASH_MAX_OUTPUT_CHARS, BASH_TIMEOUT
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope

SAFE_COMMANDS = frozenset(
    {
        "ls",
        "cat",
        "head",
        "tail",
        "wc",
        "file",
        "stat",
        "du",
        "df",
        "find",
        "locate",
        "which",
        "whereis",
        "type",
        "grep",
        "awk",
        "sed",
        "cut",
        "sort",
        "uniq",
        "tr",
        "diff",
        "pwd",
        "whoami",
        "hostname",
        "uname",
        "date",
        "uptime",
        "env",
        "printenv",
        "git status",
        "git log",
        "git diff",
        "git branch",
        "git show",
        "git remote",
        "git tag",
        "git stash list",
        "npm list",
        "pip list",
        "pip show",
        "curl",
        "wget",
        "ping",
        "host",
        "dig",
        "nslookup",
    }
)

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

BASH_DESCRIPTION = """Execute a bash command in the user's shell.

Each command runs in a fresh subprocess — no state (env vars, shell functions, cwd) persists between calls. Commands run in the area's default cwd when set, otherwise in the server's working directory. Use the working_dir parameter to run in a different directory instead of 'cd'.

PREFER OTHER TOOLS:
- For listing/finding files: use list_files() or find_files()
- For searching file content: use search_text()
- For reading files: use read_file()
- For editing/writing files: load the files group, then use edit_file() or write_file()

USE bash FOR:
- System commands: git, npm, pip, brew
- File operations that do not have a native tool yet: mkdir, cp, mv
- Checking system state: pwd, whoami, date

Every Bash command requires interactive approval and cannot run in a headless
auto-approved session. The small denylist is defense-in-depth, not the
security boundary."""


def is_safe_command(command: str) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if not parts:
        return False
    base_cmd = parts[0]
    if base_cmd in SAFE_COMMANDS:
        return True
    if len(parts) >= 2:
        cmd_with_arg = f"{base_cmd} {parts[1]}"
        if cmd_with_arg in SAFE_COMMANDS:
            return True
    if command.endswith("--version") or " --version" in command:
        return True
    return False


def is_blocked_command(command: str) -> bool:
    cmd_lower = command.lower().strip()
    return any(blocked in cmd_lower for blocked in BLOCKED_PATTERNS)


@dataclass(frozen=True, slots=True)
class BashExecution:
    content: str
    returncode: int | None = None
    timed_out: bool = False
    failed_to_start: bool = False


def execute_bash(command: str, working_dir: str | None = None, timeout: int = BASH_TIMEOUT) -> BashExecution:
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=working_dir,
        )

        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            if output:
                output += "\n"
            output += f"[stderr]\n{result.stderr}"

        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"

        if len(output) > BASH_MAX_OUTPUT_CHARS:
            half = BASH_MAX_OUTPUT_CHARS // 2
            omitted = len(output) - BASH_MAX_OUTPUT_CHARS
            output = f"{output[:half]}\n\n[... {omitted} chars elided ...]\n\n{output[-half:]}"

        return BashExecution(content=output if output else "(no output)", returncode=result.returncode)

    except subprocess.TimeoutExpired:
        return BashExecution(content=f"Command timed out after {timeout}s", timed_out=True)
    except Exception:
        return BashExecution(content="Command could not be started", failed_to_start=True)


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
    if is_blocked_command(args.command):
        return ToolResult.failure(
            code="permission_denied",
            message=f"Blocked command: {args.command}",
            preview="Blocked",
            recovery_action="Use a safer command or a purpose-built file tool.",
        )

    execution_result = await asyncio.to_thread(
        execute_bash,
        args.command,
        _working_dir(execution, args),
        BASH_TIMEOUT,
    )
    if execution_result.timed_out:
        return ToolResult.failure(
            code="timed_out",
            message=execution_result.content,
            preview="Timed out",
            retryable=True,
            recovery_action="Retry with a narrower command or a longer-running workflow.",
        )
    if execution_result.failed_to_start:
        return ToolResult.failure(
            code="command_failed",
            message=execution_result.content,
            preview="Command failed",
            recovery_action="Check the executable and working directory, then retry.",
        )
    if execution_result.returncode:
        return ToolResult.failure(
            code="command_failed",
            message=execution_result.content,
            preview=f"Exit {execution_result.returncode}",
            recovery_action="Inspect stderr and retry with corrected arguments.",
        )
    lines = execution_result.content.count("\n") + 1
    return ToolResult(content=execution_result.content, preview=f"{lines} lines")


bash_tool = tool(
    display_name="Bash",
    description=BASH_DESCRIPTION,
    input_model=BashInput,
    policy=ToolPolicy(
        action=ToolAction.EXECUTE,
        scope=ToolScope.INTERNAL,
        requires_approval=True,
        allow_approval_bypass=False,
    ),
    approval=approve_bash,
    execute=run_bash,
)
