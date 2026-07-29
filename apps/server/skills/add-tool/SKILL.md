---
name: add-tool
description: Create a custom user tool in ~/.arden/tools/ using the current tool(...) API.
---

# Add User Tool

Help the user create a custom tool. User tools live in `~/.arden/tools/` as Python files and are discovered when the server starts.

**Important**: Use `bash` to run the scaffold script and apply edits. Use `read_file` to read and verify. Do not create class-based tools. The only supported user-tool registration shape is a module-level `tools: dict[str, Tool]` built with `tool(...)`.

## Step 1: Gather requirements

Ask the user:
1. What should the tool do?
2. What parameters does it need?
3. Does it modify source-of-truth state? (if yes -> `policy.requires_approval=True`, needs an approval function)
4. Does it need an existing source or service? (see available services below)
5. What bounds, stable references, and retry/idempotency contract does it need?

## Step 2: Scaffold the tool file

Run the scaffold script (path is relative to the `path` attribute from the `<skill>` tag above):

```bash
bash <skill_path>/scripts/scaffold.sh <tool_name>
```

This creates `~/.arden/tools/<tool_name>.py` from the current `tool(...)` template.

## Step 3: Customize

Use `read_file` on `~/.arden/tools/<tool_name>.py`, then use `bash` to apply edits:

- Rename `ToolInput` and `execute_tool` if clearer
- Fill in `display_name` and `description`
- Update `ToolInput` fields to match the user's parameters
- Implement the execute function
- Set `ToolPolicy.action` and `ToolPolicy.scope` to match the tool behavior
- If `requires_approval=True`, uncomment and implement the approval function, then pass it as `approval=...`
- If the tool needs a source/service, add `permissions=frozenset({...})` and service access (see patterns below)
- Keep the module-level `tools = {"tool_name": tool(...)}` mapping

### Non-negotiable harness contract

- Bound every string/list/range input with Pydantic and cap every returned collection; say when more may exist and provide a cursor when continuation is supported.
- Return `ToolResult.failure(code=..., recovery_action=...)` for actionable failures. Do not encode failures as success prose or leak provider exceptions.
- Attach `ToolSourceRef` for each stable provider/file/session ref. Never fabricate provenance or URLs.
- Every source-of-truth mutation needs an approval preview, stable `idempotency_key`, and a `ToolOutcome` with effect, receipt, and verification (or an explicit uncertain status).
- Search/list first, inspect the exact ref, preview the change, mutate once, then verify via the returned `after_ref`.

## Required shape

```python
from pydantic import BaseModel, Field

from arden.tools.core import ToolAction, ToolPolicy, ToolResult, ToolScope, tool
from arden.tools.core.context import ToolExecution


class MyInput(BaseModel):
    query: str = Field(description="Search query")


async def my_tool(execution: ToolExecution, args: MyInput) -> ToolResult:
    return ToolResult(content=args.query, preview="Done")


tools = {
    "my_tool": tool(
        display_name="MyTool",
        description="Describe when the agent should use this tool.",
        input_model=MyInput,
        policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
        execute=my_tool,
    )
}
```

The execute function must return `ToolResult`. Returning a string, dict, or arbitrary object is invalid.

## Service access patterns

### Client-backed

```python
from arden.integrations.slack.client import SlackClient


async def search_slack(execution: ToolExecution, args: MyInput) -> ToolResult:
    client = execution.ctx.get_client("slack", SlackClient)
    if client is None:
        return ToolResult.failure(
            code="not_configured",
            message="Slack is not connected.",
            preview="Slack unavailable",
            recovery_action="Ask the user to connect Slack, then retry.",
        )
    results = await client.search_messages(args.query)
    lines = [f"{item.title}: {item.content}" for item in results]
    return ToolResult(content="\n".join(lines), preview=f"{len(results)} results")


tools = {
    "search_slack": tool(
        description="Search Slack messages.",
        input_model=MyInput,
        policy=ToolPolicy(
            action=ToolAction.READ,
            scope=ToolScope.EXTERNAL,
            permissions=frozenset({"slack"}),
        ),
        execute=search_slack,
    )
}
```

### Generic service lookup

```python
async def read_facts(execution: ToolExecution, args: MyInput) -> ToolResult:
    facts = execution.ctx.services["facts"]
```

## Available services

Keys for `policy.permissions` and `execution.ctx.services`:

| Key | Type | What it provides |
|-----|------|-----------------|
| `gmail` | `MultiGmailSource` | Email read/search/send |
| `calendar` | `MultiCalendarSource` | Calendar events CRUD |
| `web` | `WebClient` | Web search and content fetch |
| `facts` | `FactService` | Canonical fact read, planning, and commit operations |
| `automation` | `AutomationService` | Scheduled automation management |
| `wiki` | `WikiService` | Managed wiki reads; use the built-in wiki mutation tools for page writes |
| `session` | `SessionService` | Current and recent chat sessions |
| `skill_registry` | `SkillRegistry` | Skill lookup and loading |
| `skill_service` | `SkillService` | Global skill creation and management |
| `search_index` | `SearchIndex` | Vector search across indexed sources |
| `slack` | `SlackClient` | Slack search/read APIs |
| `mcp` | `MCPManager` | Connected MCP tools |
| `notifiers` | `NotifierService` | Configured notifiers |
| `connections` | `ConnectionService` | Integration connection requests |
| `app_control` | `AppControlService` | Chat and app navigation actions |

Use `execution.ctx.get_client("service_id", ClientType)` for integration clients when you can import the concrete client type. Use `execution.ctx.services["key"]` for internal services such as `facts` and `automation`. Add the matching permission key so unavailable services hide the tool instead of failing at runtime.

## `tool(...)` arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `description` | yes | The LLM reads this to decide when to call the tool |
| `execute` | yes | Async function receiving `(ToolExecution, args)` and returning `ToolResult` |
| `display_name` | no | Shown in the UI |
| `input_model` | no | Pydantic `BaseModel`; omitted means no parameters |
| `policy` | yes | `ToolPolicy(action=..., scope=..., ...)`; controls visibility, approval, audit, and result handling |
| `approval` | no | Async function returning `ApprovalInfo | None` before execution |

Policy fields:

| Field | Description |
|-------|-------------|
| `action` | `READ`, `DRAFT`, `WRITE`, or `EXECUTE` |
| `scope` | `INTERNAL` for arden/local state, `EXTERNAL` for third-party systems |
| `requires_approval` | `True` pauses for user approval before execution |
| `permissions` | Service keys; tool is hidden when any is missing |
| `timeout_seconds` | Optional per-tool execution timeout |
| `audit` | Whether calls should be auditable |
| `max_result_chars` | Optional context result limit |
| `offload` | Whether large results can be offloaded to a reference |

## Step 4: Verify and inform

1. Use `read_file` to verify the final tool file
2. Tell the user to restart the server (`arden-server serve`) for discovery
3. Name conflicts with built-ins are skipped with a warning; import errors are logged and skipped

## Notes

- User tools use the same `tool(...)` API as built-in tools
- User tools can use existing sources/services but cannot define new ones
- External packages must be installed in the environment (`uv pip install ...`)
- Multiple tools in one file are allowed: add more entries to the module-level `tools` mapping
