from dataclasses import dataclass, field
from urllib.parse import urlparse

from arden.tools.core.types import ToolPolicy

_HTTP_SCHEMES = frozenset({"http", "https"})


@dataclass(frozen=True)
class StdioTransport:
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class HttpTransport:
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    auth: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    redirect_port: int | None = None
    scope: str | None = None
    client_name: str | None = None


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    transport: StdioTransport | HttpTransport
    tools: list[str] | None = None
    tool_policies: dict[str, ToolPolicy] = field(default_factory=dict)
    trust_tool_annotations: bool = False


def _normalize_http_url(name: str, raw_url: object) -> str:
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise ValueError(f"MCP server {name!r}: http transport requires 'url'")

    url = raw_url.strip()
    if "://" not in url:
        url = f"http://{url}"

    parsed = urlparse(url)
    if parsed.scheme not in _HTTP_SCHEMES or not parsed.netloc:
        raise ValueError(f"MCP server {name!r}: http url must use http:// or https://")
    return url


def parse_server_config(name: str, raw: dict) -> MCPServerConfig:
    transport_type = raw.get("transport")
    if transport_type == "stdio":
        command = raw.get("command")
        if not command:
            raise ValueError(f"MCP server {name!r}: stdio transport requires 'command'")
        transport = StdioTransport(
            command=command,
            args=raw.get("args", []),
            env=raw.get("env"),
        )
    elif transport_type == "http":
        auth = raw.get("auth")
        if auth is not None and auth != "oauth":
            raise ValueError(f"MCP server {name!r}: http auth must be 'oauth' when set")
        transport = HttpTransport(
            url=_normalize_http_url(name, raw.get("url")),
            headers=raw.get("headers", {}),
            auth=auth,
            client_id=raw.get("client_id"),
            client_secret=raw.get("client_secret"),
            redirect_port=raw.get("redirect_port"),
            scope=raw.get("scope"),
            client_name=raw.get("client_name"),
        )
    else:
        raise ValueError(f"MCP server {name!r}: unknown transport {transport_type!r} (expected 'stdio' or 'http')")
    tools = raw.get("tools")
    raw_policies = raw.get("tool_policies", {})
    if raw_policies is None:
        raw_policies = {}
    if not isinstance(raw_policies, dict):
        raise ValueError(f"MCP server {name!r}: tool_policies must be an object")

    try:
        tool_policies = {tool_name: ToolPolicy.model_validate(policy) for tool_name, policy in raw_policies.items()}
    except Exception as e:
        raise ValueError(f"MCP server {name!r}: invalid tool_policies: {e}") from e

    return MCPServerConfig(
        name=name,
        transport=transport,
        tools=tools,
        tool_policies=tool_policies,
        trust_tool_annotations=bool(raw.get("trust_tool_annotations", False)),
    )
