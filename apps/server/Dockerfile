FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build

ENV UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y gcc g++ build-essential && rm -rf /var/lib/apt/lists/*

WORKDIR /app/apps/server

COPY apps/server/pyproject.toml apps/server/uv.lock apps/server/README.md ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY apps/server/arden ./arden

FROM python:3.13-slim

WORKDIR /app

RUN groupadd --gid 1000 arden \
    && useradd --uid 1000 --gid arden --create-home arden \
    && mkdir -p /app/data /home/arden/.arden \
    && chown -R arden:arden /app /home/arden/.arden

COPY --from=build --chown=arden:arden /app/apps/server/.venv /app/apps/server/.venv

COPY --chown=arden:arden apps/server/arden ./apps/server/arden
COPY --chown=arden:arden apps/server/skills ./apps/server/skills

USER arden

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/apps/server/.venv/bin:$PATH" \
    PYTHONPATH="/app/apps/server"

EXPOSE 6877

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:6877/health')" || exit 1

CMD ["uvicorn", "arden.server.app:app", "--host", "0.0.0.0", "--port", "6877"]
