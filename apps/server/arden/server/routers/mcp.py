import asyncio
from urllib.parse import urlparse, urlunparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from arden.mcp.models import HttpTransport, parse_server_config
from arden.mcp.oauth import OAuthOptions, clear_tokens, run_mcp_oauth
from arden.mcp.tool import MCPTool
from arden.server.deps import require_config_service
from arden.server.runtime import Runtime, get_runtime
from arden.services.config import ConfigService

router = APIRouter(prefix="/mcp", tags=["mcp"])

OAUTH_DISCOVERY_HEADER = "MCP-Protocol-Version"
OAUTH_DISCOVERY_VERSION = "2024-11-05"
OAUTH_DISCOVERY_TIMEOUT = 5.0


def _oauth_discovery_paths(base_path: str) -> list[str]:
    trimmed = base_path.strip("/").strip()
    canonical = "/.well-known/oauth-authorization-server"
    if not trimmed:
        return [canonical]

    candidates: list[str] = []

    def push_unique(candidate: str) -> None:
        if candidate not in candidates:
            candidates.append(candidate)

    push_unique(f"{canonical}/{trimmed}")
    push_unique(f"/{trimmed}/.well-known/oauth-authorization-server")
    push_unique(canonical)
    return candidates


def _with_path(url: str, path: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(path=path, params="", query="", fragment=""))


async def discover_mcp_oauth(url: str) -> bool:
    parsed = urlparse(url)
    headers = {OAUTH_DISCOVERY_HEADER: OAUTH_DISCOVERY_VERSION}
    async with httpx.AsyncClient(timeout=OAUTH_DISCOVERY_TIMEOUT, follow_redirects=False, trust_env=False) as client:
        for path in _oauth_discovery_paths(parsed.path):
            try:
                response = await client.get(_with_path(url, path), headers=headers)
            except httpx.HTTPError:
                continue
            if response.status_code != 200:
                continue
            try:
                metadata = response.json()
            except ValueError:
                continue
            if metadata.get("authorization_endpoint") and metadata.get("token_endpoint"):
                return True
    return False


def prepare_mcp_server_config(name: str, requested: dict, existing: dict | None = None) -> dict:
    config = dict(requested)
    existing = existing or {}
    transport = config.get("transport", existing.get("transport"))
    requested_has_headers = "headers" in config
    effective_auth = config.get("auth", existing.get("auth"))

    if requested_has_headers:
        effective_auth = None
        config.pop("auth", None)
        for key in ("client_id", "client_secret", "redirect_port", "scope", "client_name"):
            config.pop(key, None)

    if transport == "stdio" and "env" not in config and "env" in existing:
        config["env"] = existing["env"]
    if transport == "http" and effective_auth != "oauth":
        existing_headers = existing.get("headers") or {}
        if "headers" in config:
            # The UI shows header keys with masked values; an empty value means
            # "keep the existing secret". Merge per-key so masked headers survive.
            merged: dict[str, str] = {}
            for key, value in (config["headers"] or {}).items():
                if value == "" and key in existing_headers:
                    merged[key] = existing_headers[key]
                elif value != "":
                    merged[key] = value
            config["headers"] = merged
        elif existing_headers:
            config["headers"] = existing_headers
    if effective_auth == "oauth":
        config["auth"] = "oauth"
        config.pop("headers", None)

    if effective_auth == "oauth" and "client_secret" not in config:
        if existing.get("client_secret"):
            config["client_secret"] = existing["client_secret"]

    parsed = parse_server_config(name, config)
    if isinstance(parsed.transport, HttpTransport):
        config["url"] = parsed.transport.url
    return config


async def prepare_mcp_server_config_for_save(name: str, requested: dict, existing: dict | None = None) -> dict:
    config = prepare_mcp_server_config(name, requested, existing=existing)
    parsed = parse_server_config(name, config)
    if isinstance(parsed.transport, HttpTransport) and not parsed.transport.auth and not parsed.transport.headers:
        if await discover_mcp_oauth(parsed.transport.url):
            config["auth"] = "oauth"
            config.pop("headers", None)
            config = prepare_mcp_server_config(name, config, existing=existing)
    return config


@router.get("/servers")
async def list_mcp_servers(runtime: Runtime = Depends(get_runtime)):
    configs = runtime.config.mcp_servers or {}
    manager = runtime.mcp_manager
    servers = []
    for name, raw in configs.items():
        session = manager.sessions.get(name) if manager else None
        error = manager.errors.get(name) if manager else None
        whitelist = raw.get("tools")
        allowed = set(whitelist) if whitelist is not None else None
        tools = []
        if session and session.connected:
            for t in session.all_tools:
                policy_tool = MCPTool(
                    name,
                    t,
                    session,
                    policy=session.config.tool_policies.get(t.name),
                    trust_annotations=session.config.trust_tool_annotations,
                )
                metadata = policy_tool.get_metadata(policy_tool.name)
                tools.append(
                    {
                        "name": t.name,
                        "full_name": policy_tool.name,
                        "description": t.description or "",
                        "enabled": allowed is None or t.name in allowed,
                        "policy": metadata["policy"],
                        "override": runtime.config.tool_overrides.get(policy_tool.name),
                    }
                )
        servers.append(
            {
                "name": name,
                "transport": raw.get("transport", "unknown"),
                "connected": session.connected if session else False,
                "tool_count": len(session.tools) if session else 0,
                "error": error,
                "command": raw.get("command"),
                "args": raw.get("args"),
                "url": raw.get("url"),
                "tools": tools,
                "enabled": raw.get("enabled", True),
                "auth": raw.get("auth"),
                "header_keys": list((raw.get("headers") or {}).keys()),
                "client_id": raw.get("client_id"),
                "redirect_port": raw.get("redirect_port"),
                "scope": raw.get("scope"),
                "client_name": raw.get("client_name"),
                "has_client_credentials": bool(raw.get("client_id")),
                "has_client_secret": bool(raw.get("client_secret")),
            }
        )
    return {"servers": servers}


class AddMCPServerRequest(BaseModel):
    name: str
    config: dict


@router.post("/servers")
async def add_mcp_server(
    req: AddMCPServerRequest,
    runtime: Runtime = Depends(get_runtime),
    cfg_svc: ConfigService = Depends(require_config_service),
):
    existing = runtime.config.mcp_servers or {}
    if req.name in existing:
        raise HTTPException(status_code=409, detail=f"MCP server {req.name!r} already exists")

    try:
        config = await prepare_mcp_server_config_for_save(req.name, req.config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        await cfg_svc.add_mcp_server(req.name, config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    manager = runtime.mcp_manager
    session = manager.sessions.get(req.name) if manager else None
    error = manager.errors.get(req.name) if manager else None
    return {
        "status": "added",
        "name": req.name,
        "connected": session.connected if session else False,
        "tool_count": len(session.tools) if session else 0,
        "error": error,
    }


class UpdateMCPServerRequest(BaseModel):
    config: dict


@router.put("/servers/{name}")
async def update_mcp_server_route(
    name: str,
    req: UpdateMCPServerRequest,
    runtime: Runtime = Depends(get_runtime),
    cfg_svc: ConfigService = Depends(require_config_service),
):
    existing = runtime.config.mcp_servers or {}
    if name not in existing:
        raise HTTPException(status_code=404, detail=f"MCP server {name!r} not found")

    existing_transport = existing[name].get("transport")
    new_transport = req.config.get("transport", existing_transport)
    if new_transport != existing_transport:
        raise HTTPException(
            status_code=400,
            detail="Cannot switch transport type; uninstall and re-add the server",
        )

    try:
        config = await prepare_mcp_server_config_for_save(name, req.config, existing=existing[name])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        await cfg_svc.update_mcp_server(name, config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    manager = runtime.mcp_manager
    session = manager.sessions.get(name) if manager else None
    error = manager.errors.get(name) if manager else None
    return {
        "status": "updated",
        "name": name,
        "connected": session.connected if session else False,
        "tool_count": len(session.tools) if session else 0,
        "error": error,
    }


class UpdateToolsRequest(BaseModel):
    tools: list[str] | None


@router.put("/servers/{name}/tools")
async def update_mcp_tools(
    name: str,
    req: UpdateToolsRequest,
    runtime: Runtime = Depends(get_runtime),
    cfg_svc: ConfigService = Depends(require_config_service),
):
    existing = runtime.config.mcp_servers or {}
    if name not in existing:
        raise HTTPException(status_code=404, detail=f"MCP server {name!r} not found")

    config = dict(existing[name])
    if req.tools is not None:
        config["tools"] = req.tools
    else:
        config.pop("tools", None)

    try:
        await cfg_svc.update_mcp_server(name, config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"status": "updated", "name": name, "tool_count": len(req.tools) if req.tools else None}


class ToggleEnabledRequest(BaseModel):
    enabled: bool


@router.put("/servers/{name}/enabled")
async def toggle_mcp_server(
    name: str,
    req: ToggleEnabledRequest,
    runtime: Runtime = Depends(get_runtime),
    cfg_svc: ConfigService = Depends(require_config_service),
):
    existing = runtime.config.mcp_servers or {}
    if name not in existing:
        raise HTTPException(status_code=404, detail=f"MCP server {name!r} not found")

    try:
        await cfg_svc.toggle_mcp_server(name, req.enabled)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"status": "toggled", "name": name, "enabled": req.enabled}


@router.post("/servers/{name}/oauth")
async def mcp_oauth(
    name: str,
    runtime: Runtime = Depends(get_runtime),
):
    existing = runtime.config.mcp_servers or {}
    if name not in existing:
        raise HTTPException(status_code=404, detail=f"MCP server {name!r} not found")

    raw = existing[name]
    try:
        parsed = parse_server_config(name, raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    transport = parsed.transport
    if not isinstance(transport, HttpTransport) or transport.auth != "oauth":
        raise HTTPException(status_code=400, detail="Server does not use OAuth authentication")

    opts = OAuthOptions(
        client_id=transport.client_id,
        client_secret=transport.client_secret,
        redirect_port=transport.redirect_port,
        scope=transport.scope,
        client_name=transport.client_name or "ARDEN",
    )
    try:
        await asyncio.to_thread(run_mcp_oauth, name, transport.url, opts)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Reconnect MCP servers to pick up the new tokens
    await runtime.sync_mcp()

    manager = runtime.mcp_manager
    session = manager.sessions.get(name) if manager else None
    error = manager.errors.get(name) if manager else None
    return {
        "status": "connected",
        "name": name,
        "connected": session.connected if session else False,
        "tool_count": len(session.tools) if session else 0,
        "error": error,
    }


@router.delete("/servers/{name}")
async def remove_mcp_server(
    name: str,
    runtime: Runtime = Depends(get_runtime),
    cfg_svc: ConfigService = Depends(require_config_service),
):
    existing = runtime.config.mcp_servers or {}
    if name not in existing:
        raise HTTPException(status_code=404, detail=f"MCP server {name!r} not found")

    try:
        await cfg_svc.remove_mcp_server(name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Clean up OAuth tokens if any
    try:
        clear_tokens(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return {"status": "removed", "name": name}
