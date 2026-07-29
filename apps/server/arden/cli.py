import asyncio
import json
import socket

import click
import uvicorn
from rich.console import Console

from arden.agent import Role
from arden.config import get_config
from arden.core.factory import AgentConfig, create_agent
from arden.core.ids import generate_run_id
from arden.core.prompts import build_system_prompt
from arden.events.internal import RunCompleted
from arden.llm.models import supports_native_deferred_tools
from arden.logging import UVICORN_LOG_CONFIG
from arden.observability import observed_trace
from arden.server.runtime import Runtime
from arden.settings import generate_api_key, load_user_settings, save_user_settings
from arden.tools.core.context import IOBridge
from arden.tools.deferred import (
    build_deferred_tools_prompt_for_schemas,
    build_native_deferred_tools_prompt_for_schemas,
)

console = Console()


def _require_chat_model(config) -> None:
    if not config.chat_model:
        console.print("[red]Error:[/red] No chat model configured.")
        console.print()
        console.print("Set a provider API key:")
        console.print("  ANTHROPIC_API_KEY")
        console.print("  OPENAI_API_KEY")
        console.print("  GEMINI_API_KEY")
        console.print()
        console.print("Or specify a model directly: ARDEN_CHAT_MODEL=<model>")
        raise SystemExit(1)


@click.group()
@click.version_option(package_name="arden")
def main():
    """arden - personal entropy reduction system"""


@main.command()
def status():
    """Show current status of arden."""
    config = get_config()
    console.print("[bold]arden status[/bold]")
    console.print()
    console.print(f"Database dir: [cyan]{config.db_dir}[/cyan]")
    console.print(f"Chat model: {config.chat_model or '[dim]not set[/dim]'}")
    console.print(f"Auxiliary model: {config.auxiliary_model or '[dim]not set[/dim]'}")
    console.print(f"Memory model: {config.memory_model or '[dim]not set[/dim]'}")
    console.print(f"Embedding model: {config.embedding_model or '[dim]not set[/dim]'}")


@main.command("info")
@click.option("--json", "as_json", is_flag=True, help="Print raw runtime info JSON")
def info(as_json: bool):
    """Show the active runtime surface."""
    from arden.agent_surface.runtime_info import build_runtime_info

    data = build_runtime_info()
    dumped = data.model_dump(mode="json")
    if as_json:
        console.print(json.dumps(dumped, indent=2, sort_keys=True))
        return
    console.print("[bold]Runtime[/bold]")
    console.print(f"Version: [cyan]{data.version}[/cyan]")
    console.print(f"Agent root: [cyan]{data.agent_surface.root}[/cyan]")
    console.print(f"Manifest: [cyan]{data.agent_surface.manifest_path}[/cyan]")
    console.print(f"Tools: {len(data.tools)}")
    console.print(f"Skills: {len(data.skills)}")
    console.print(f"Schedules: {len(data.schedules)}")
    if data.warnings:
        console.print(f"Warnings: {len(data.warnings)}")


def _rotate_api_key(config, *, label: str) -> str:
    settings = load_user_settings()
    plaintext, hashed = generate_api_key()
    settings["api_key_hash"] = hashed
    save_user_settings(settings)
    config.api_key_hash = hashed
    console.print(f"[bold]{label}:[/bold] [cyan]{plaintext}[/cyan]")
    console.print("[dim]Enter this in a desktop client to connect. It won't be shown again.[/dim]")
    console.print()
    return plaintext


def _show_pairing(host: str, port: int, api_key: str) -> None:
    from arden.pairing import build_pairing

    lan_host, link, qr = build_pairing(host, port, api_key)
    console.print("[bold]Scan to pair a phone:[/bold]")
    console.print(qr, highlight=False)
    console.print(f"[dim]Deep link:[/dim] [cyan]{link}[/cyan]")
    if lan_host != host:
        console.print(f"[dim]LAN URL: http://{lan_host}:{port} (phone must be on the same network)[/dim]")
    console.print()


@main.command()
@click.option("--host", default=None, help="Host to bind to (or ARDEN_HOST)")
@click.option("--port", default=None, type=int, help="Port to bind to (or ARDEN_PORT)")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
@click.option("--reset-key", is_flag=True, help="Generate a new API key")
@click.option("--qr", is_flag=True, help="Show a pairing QR code for the mobile app")
def serve(host: str | None, port: int | None, reload: bool, reset_key: bool, qr: bool):
    """Start the arden API server."""
    config = get_config()

    plaintext: str | None = None
    if reset_key or not config.api_key_hash:
        label = "New API key" if reset_key else "Your API key"
        plaintext = _rotate_api_key(config, label=label)

    host = host or config.host
    port = port or config.port

    if qr:
        if plaintext is None:
            console.print(
                "[yellow]--qr needs a fresh plaintext key.[/yellow] "
                "Re-run with [bold]--qr --reset-key[/bold] to rotate the key and show the code."
            )
            console.print()
        else:
            _show_pairing(host, port, plaintext)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            console.print(f"[red]Error:[/red] Port {port} is already in use")
            console.print("[dim]Kill the existing process or use --port to pick another[/dim]")
            raise SystemExit(1)

    console.print(f"[bold]Arden server[/bold] starting on http://{host}:{port}")
    console.print("[dim]Press Ctrl+C to stop[/dim]")
    console.print()

    uvicorn.run(
        "arden.server.app:app",
        host=host,
        port=port,
        reload=reload,
        log_config=UVICORN_LOG_CONFIG,
        timeout_graceful_shutdown=3,
        # Long-lived SSE streams: the keep-alive idle timer must stay safely
        # above the SSE KEEPALIVE_INTERVAL (5s, server/sse_stream.py) or the
        # socket can idle-close between heartbeats and surface as a spurious
        # mid-stream disconnect. uvicorn's 5s default is too close to the
        # heartbeat; 75s gives a wide margin.
        timeout_keep_alive=75,
    )


@main.command()
@click.option("--host", default=None, help="Host advertised to the phone (or ARDEN_HOST)")
@click.option("--port", default=None, type=int, help="Port advertised to the phone (or ARDEN_PORT)")
def pair(host: str | None, port: int | None):
    """Pair a phone: rotate the API key and show a QR + deep link.

    The plaintext key is never stored, so pairing rotates it. Any existing
    desktop client must re-enter the new key after running this.
    """
    config = get_config()
    host = host or config.host
    port = port or config.port

    plaintext = _rotate_api_key(config, label="Pairing API key")
    _show_pairing(host, port, plaintext)
    console.print("[dim]Start the server with[/dim] [bold]arden serve[/bold] [dim]if it isn't running.[/dim]")


@main.command()
@click.option("-p", "--prompt", required=True, help="The prompt to execute")
def run(prompt: str):
    """Run agent once with a prompt (headless, non-interactive mode)."""
    config = get_config()
    _require_chat_model(config)
    asyncio.run(_run_headless(prompt))


@main.command("mcp")
@click.option(
    "--transport",
    type=click.Choice(["stdio", "streamable-http"]),
    default="stdio",
    show_default=True,
    help="MCP transport to serve.",
)
@click.option("--host", default=None, help="Host for streamable-http transport")
@click.option("--port", default=None, type=int, help="Port for streamable-http transport")
def mcp_command(transport: str, host: str | None, port: int | None):
    """Start the arden MCP server."""
    config = get_config()
    if not config.chat_model and not config.research_model:
        _require_chat_model(config)

    from arden.mcp.server import create_mcp_server

    host = host or config.host
    port = port or 6878
    api_key_hash = None
    if transport == "streamable-http":
        if not config.api_key_hash:
            settings = load_user_settings()
            plaintext, hashed = generate_api_key()
            settings["api_key_hash"] = hashed
            save_user_settings(settings)
            config.api_key_hash = hashed
            console.print(f"[bold]MCP API key:[/bold] [cyan]{plaintext}[/cyan]")
            console.print("[dim]Use this as Authorization: Bearer <key>. It won't be shown again.[/dim]")
            console.print()
        api_key_hash = config.api_key_hash

    server = create_mcp_server(
        host=host,
        port=port,
        api_key_hash=api_key_hash,
        public_url=f"http://{host}:{port}",
    )
    server.run(transport=transport)


@observed_trace("cli", tags="cli")
async def _run_headless(prompt: str):
    runtime = Runtime()
    await runtime.connect()

    try:
        config = AgentConfig.from_config(runtime.config)
        tools = runtime.executor.get_tools()
        native_deferred_tools = supports_native_deferred_tools(config.model)
        deferred_tools_context = (
            (
                build_native_deferred_tools_prompt_for_schemas
                if native_deferred_tools
                else build_deferred_tools_prompt_for_schemas
            )(runtime.executor.registry, frozenset(runtime.executor.tool_services), tools)
            if config.deferred_tools
            else None
        )
        system_prompt = build_system_prompt(
            source_details={},
            memory_context=None,
            deferred_tools_context=deferred_tools_context,
            native_deferred_tools=native_deferred_tools,
        )

        run_id = generate_run_id()
        session_state = runtime.session_service.create()

        agent = create_agent(
            executor=runtime.executor,
            config=config,
            tools=tools,
            session_state=session_state,
            run_id=run_id,
            io=IOBridge(),
        )

        messages = [
            {"role": Role.SYSTEM, "content": system_prompt},
            {"role": Role.USER, "content": prompt},
        ]

        console.print(f"[dim]Running: {prompt}[/dim]\n")
        run_result = await agent.run(messages)
        console.print(run_result.text)
        await runtime.stores.outbox.enqueue_run_completed(
            RunCompleted(
                run_id=run_id,
                session_id=session_state.session_id,
                messages=tuple(messages),
                usage=run_result.usage,
                result=run_result.text,
            )
        )
    finally:
        await runtime.close()


if __name__ == "__main__":
    main()
