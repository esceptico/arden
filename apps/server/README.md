# Arden server

Backend package for arden.

It provides:

- `arden-server` CLI entrypoint
- FastAPI HTTP/SSE server
- agent runtime and multi-agent tooling
- integrations and deferred tool loading
- canonical facts, managed wiki pages, search, areas, and admin APIs
- builtin skills and user-tool loading
- sandboxed `render_html` widget tool support for interactive desktop clients

Run from source:

```bash
uv sync --locked --group dev
uv run arden-server serve
```

Run tests:

```bash
uv run pytest
```

Repository and full documentation: https://github.com/esceptico/ntrp
