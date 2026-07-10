import asyncio
import shlex
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from ntrp.constants import BASH_MAX_OUTPUT_CHARS, BASH_TIMEOUT
from ntrp.tools.core import ToolResult, tool
from ntrp.tools.core.context import ToolExecution
from ntrp.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope

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

Destructive commands (rm -rf) are blocked. Non-safe commands require approval."""


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


def execute_bash(command: str, working_dir: str | None = None, timeout: int = BASH_TIMEOUT) -> str:
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

        return output if output else "(no output)"

    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


class BashInput(BaseModel):
    command: str = Field(description="The shell command to execute")
    working_dir: str | None = Field(default=None, description="Working directory (optional, defaults to current)")


def _working_dir(execution: ToolExecution, args: BashInput) -> str | None:
    return args.working_dir or (execution.ctx.area.default_cwd if execution.ctx.area else None)


async def approve_bash(execution: ToolExecution, args: BashInput) -> ApprovalInfo | None:
    if not is_safe_command(args.command) and not is_blocked_command(args.command):
        cwd = _working_dir(execution, args) or str(Path.cwd())
        return ApprovalInfo(description=f"{args.command}\n\ncwd: {cwd}", preview=None, diff=None)
    return None


async def run_bash(execution: ToolExecution, args: BashInput) -> ToolResult:
    if is_blocked_command(args.command):
        return ToolResult(content=f"Blocked: {args.command}", preview="Blocked", is_error=True)

    output = await asyncio.to_thread(execute_bash, args.command, _working_dir(execution, args), BASH_TIMEOUT)
    lines = output.count("\n") + 1
    return ToolResult(content=output, preview=f"{lines} lines")


bash_tool = tool(
    display_name="Bash",
    description=BASH_DESCRIPTION,
    input_model=BashInput,
    policy=ToolPolicy(action=ToolAction.EXECUTE, scope=ToolScope.INTERNAL, requires_approval=True),
    approval=approve_bash,
    execute=run_bash,
)
