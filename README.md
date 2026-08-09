# Arden

> This repository preserves the MIT-licensed public version of Arden as an artifact.

**Arden** is a personal AI agent that connects your conversations, memory, tools, and recurring work in one durable workspace.

A local-first personal assistant. Not a coding agent: a place to keep the moving parts of your life in one system that remembers them and tells you what needs you. Python backend, Electron desktop app.

![Arden](docs/images/main.png)

**Documentation: [arden.timganiev.com](https://arden.timganiev.com)**

## Install

The desktop app and the supported development setup run from a source checkout:

```bash
git clone https://github.com/esceptico/arden.git arden
cd arden
cp .env.example .env  # set one provider key, or connect in Desktop
just install
just server      # terminal 1
just desktop     # terminal 2
```

Paste the API key printed by the server on first desktop launch. Full walkthrough in the [quickstart](https://arden.timganiev.com/quickstart).

Server-only Docker deployment:

```bash
cp .env.example .env
docker compose up --build
```

## Commands

```bash
just              # list recipes
just install      # install server and desktop deps
just server       # run backend
just desktop      # run desktop client
just check        # backend tests + desktop typecheck
```

## Layout

- `apps/server`: FastAPI backend, agent runtime, memory, areas, tools, integrations.
- `apps/desktop`: Electron client.
- `docs`: Public documentation.

## License

MIT
