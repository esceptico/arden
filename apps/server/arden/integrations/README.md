# Integrations

An **Integration** bundles everything Arden needs to connect to an external service: client code, agent tools, notifier classes, and config fields. Each integration lives in its own directory under `arden/integrations/` and is registered once in `ALL_INTEGRATIONS`.

## Anatomy

```
arden/integrations/slack/
  __init__.py    # exports the SLACK Integration declaration
  client.py      # SlackClient — composition root
  transport.py   # HTTP, authentication, payload validation, pagination
  directory.py   # channels, users, and direct messages
  messages.py    # search, history, and posting
  media.py       # threads, files, and image downloads
  read_tools.py  # read tool declarations and input models
  write_tools.py # mutation tool declarations and approvals
  tool_*.py      # shared input and formatting helpers
  notifier.py    # SlackNotifier (optional)
```

An integration is a frozen dataclass:

```python
@dataclass(frozen=True)
class Integration:
    id: str  # "slack"
    label: str  # "Slack"
    service_fields: list[IntegrationField]  # user-facing config keys
    tools: dict[str, Tool]  # name-to-tool map
    notifier_class: type[Notifier] | None  # optional notifier
    build: Callable[[Config], object | None] | None  # returns client, or None if unconfigured
```

`build` receives the current `Config` and returns a client instance, or `None` if the integration isn't configured. If it raises, the error is captured and surfaced to the UI.

## Adding a new integration

1. Create `arden/integrations/<name>/` with:
   - `client.py` — API wrapper, no inheritance from any protocol
   - `tools.py` — a `dict[str, Tool]` built from `tool(...)` declarations
   - `notifier.py` — optional `Notifier` subclass
   - `__init__.py` — exports a module-level `<NAME>: Integration` constant
2. Add `<NAME>` to `ALL_INTEGRATIONS` in `arden/integrations/__init__.py`
3. Add any new config fields to `arden/config.py` (e.g. `linear_api_key: str | None = Field(default=None, alias="LINEAR_API_KEY")`)

That's it. The registry handles tool registration, notifier class lookup, service endpoint listing, sidebar rendering, and hot reload.

## Deferred tool loading

Integration tools can be hidden from the model until needed. `ToolExecutor` still registers every tool, but `DeferredToolsModelRequestMiddleware` filters the schemas sent to the model. The agent can make a deferred group visible for the next model step by calling `load_tools`.

Deferred source ids are declared in `arden/tools/deferred.py`. Native integration ids such as `gmail`, `calendar`, and `slack` are deferred there; internal integrations use underscore ids such as `_automation`, `_background`, `_notifications`, and `_directives`. MCP tools are deferred by server with groups like `mcp:obsidian`.

When adding a new integration, decide deliberately:

- Frequent, small, generally useful tools should stay always visible.
- Large external-source toolsets should usually be deferred by integration id.
- State-changing tools still need `approval=` plus a `ToolPolicy` with `requires_approval=True`; deferred loading does not replace approval.
- If the integration is deferred, make sure `GROUP_ALIASES` and `GROUP_DESCRIPTIONS` describe how the model should load it.

## Example: minimal integration

```python
# Arden/integrations/linear/__init__.py
from arden.config import Config
from arden.integrations.base import Integration, IntegrationField
from arden.integrations.linear.client import LinearClient
from arden.integrations.linear.tools import linear_tools


def _build(config: Config) -> LinearClient | None:
    if not config.linear_api_key:
        return None
    return LinearClient(api_key=config.linear_api_key)


LINEAR = Integration(
    id="linear",
    label="Linear",
    service_fields=[
        IntegrationField("linear_api_key", "Linear API key", secret=True, env_var="LINEAR_API_KEY"),
    ],
    tools=linear_tools,
    build=_build,
)
```

```python
# Arden/integrations/linear/tools.py
from pydantic import BaseModel, Field

from arden.integrations.linear.client import LinearClient
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope


class LinearSearchInput(BaseModel):
    query: str = Field(description="Search query")


async def linear_search(execution: ToolExecution, args: LinearSearchInput) -> ToolResult:
    client = execution.ctx.get_client("linear", LinearClient)
    issues = await client.search(args.query)
    return ToolResult(content=format_issues(issues), preview=f"{len(issues)} issues")


linear_tools = {
    "linear_search": tool(
        display_name="LinearSearch",
        description="Search Linear issues by text",
        input_model=LinearSearchInput,
        policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"linear"})),
        execute=linear_search,
    )
}
```

Tool execution flows through `ToolRegistry` middleware. The default pipeline
validates arguments against `input_model`, asks for approval when a tool returns
approval metadata, then runs the tool. Extra middleware can wrap that pipeline
for logging, tracing, policy, or result transforms without changing individual
tool implementations.

## Setup assistants

User onboarding for native integrations should prefer a guided setup assistant over raw env-var docs when setup is multi-step or error-prone.

Current assistant endpoints live under `apps/server/arden/server/routers/setup.py`:

- `GET /setup/status` — passive status for Google, Slack, and MCP. This must not refresh tokens or start OAuth.
- `POST /setup/google/credentials` — import Google OAuth Desktop app credentials by path or pasted JSON.
- `POST /setup/google/preflight` — validate Google service choice and credentials before account add.
- `POST /setup/slack/verify` — verify a Slack `xoxb-` or `xoxp-` token with Slack `auth.test` before saving.

Desktop assistants live under `apps/desktop/src/components/settings/setup/`. When adding a new integration with a painful setup path, add concise assistant copy, preflight/status APIs if useful, and tests for both server contracts and setup helpers.

## Native integration vs MCP

**Use a native integration** when you need:
- Platform-specific auth (Google OAuth, Slack dual-token, PKCE)
- Notifier integration (proactive sends)
- Custom query semantics beyond what an MCP tool exposes
- Explicit ingestion into memory/search via a source-owned sync path
- In-process performance

**Use MCP** when:
- A vendor ships an official MCP server
- You want tools without writing API client code
- It's a quick experiment — you can promote to native later

MCP servers appear alongside native integrations in `GET /tool-providers` with `kind="mcp"`.

## Search indexing

Search indexing is owned by memory. Native integrations expose tools and
services; they are not implicitly scanned by the indexer. If an external source
needs retrieval, add explicit sync/outbox behavior for that source instead of
making the integration client double as an index source.

## Hot reload

`runtime.reload_config()` rebuilds all integration clients from the fresh config. It's called automatically when a user connects/disconnects a service via the UI, and is exposed manually at `POST /settings/reload` for after direct `.env` / `settings.json` edits.

## Notifier-only integrations

An integration with `build=None` contributes only a notifier class (e.g. Telegram). It doesn't appear in the sources sidebar but does show in the notifier type picker.

## Shared auth (Google)

Gmail and Calendar share the same Google OAuth flow. The helper module `arden/integrations/google_auth/` isn't an Integration — it's a shared module both `gmail/` and `calendar/` import from. If two integrations share auth, follow the same pattern: one helper module, two separate Integration definitions.
