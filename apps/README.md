# apps

Top-level runnable surfaces live here:

- `server` — self-contained uv project with the FastAPI backend, agent runtime, tools, integrations, memory system, builtin skills, tests, `pyproject.toml`, `uv.lock`, and Dockerfile.
- `desktop` — self-contained Bun/Electron desktop client with chat, approvals, traces, memory UI, and HTML widget rendering.

The Python package name stays `arden`, so imports and the `arden-server` entrypoint do not change.

There is intentionally no root Python or Bun workspace yet. Each app owns its own project config and lockfile until there is real shared code worth extracting.
