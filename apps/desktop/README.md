# Arden desktop

Electron client for the local Arden server.

It provides:

- chat over the server's SSE stream
- tool approval cards and activity traces
- managed wiki browsing/editing with links, records, and reversible history
- sandboxed HTML widgets rendered from the agent's `render_html` tool
- local server URL/API key storage; API keys use Electron `safeStorage` when supported by the OS

## Development

Start the backend first and keep the one-time API key it prints:

```bash
cd ../server
uv sync --locked --group dev
uv run arden-server serve
```

Then run the desktop client:

```bash
bun install
bun run dev
```

Use Node `^20.19.0` or `>=22.12.0`. The checked-in `.node-version` pins `22.12.0` for local version managers.

The app connects to `http://localhost:6877` by default and asks for the server API key on first launch.

## Checks

```bash
bun run typecheck
bun test
```

## Builds

```bash
bun run dist          # current platform
bun run dist:mac      # macOS dmg/zip
bun run dist:win      # Windows NSIS
bun run dist:linux    # Linux AppImage
```
