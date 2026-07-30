set dotenv-load := true
set shell := ["bash", "-uc"]

# List recipes.
default:
    @just --list

# Install dependencies for all apps.
install:
    cd apps/server && uv sync --locked --group dev
    cd apps/desktop && bun install --frozen-lockfile

# Run the backend server.
server:
    uv run --project apps/server arden-server serve

# Run the desktop client.
desktop:
    cd apps/desktop && bun run dev

# Run checks.
check:
    cd apps/server && uv run ruff check .
    cd apps/server && uv run ruff format --check .
    cd apps/server && uv run pytest tests
    cd apps/desktop && bun run typecheck

# Refresh committed model metadata from models.dev.
update-models:
    cd apps/server && uv run python scripts/update_models.py

# Build distributable artifacts.
build:
    cd apps/server && uv build
    cd apps/desktop && bun run build

# Start Docker Compose services.
up:
    docker compose up -d

# Stop Docker Compose services.
down:
    docker compose down
