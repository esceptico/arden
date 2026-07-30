FROM ghcr.io/astral-sh/uv:0.11.16-python3.13-trixie-slim AS build

ENV UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/apps/server

COPY apps/server/pyproject.toml apps/server/uv.lock apps/server/README.md ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY apps/server/arden ./arden
COPY apps/server/skills ./skills

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

RUN .venv/bin/python -c "from pathlib import Path; from arden.skills.registry import SkillRegistry; from arden.skills.service import BUILTIN_SKILLS_DIR; expected = {path.parent.name for path in Path('skills').glob('*/SKILL.md')}; registry = SkillRegistry(); registry.load([(BUILTIN_SKILLS_DIR, 'builtin')]); assert expected and set(registry.names) == expected"

FROM python:3.13-slim-trixie

WORKDIR /app

RUN groupadd --gid 1000 arden \
    && useradd --uid 1000 --gid arden --create-home arden \
    && mkdir -p /app/data /home/arden/.arden \
    && chown -R arden:arden /app /home/arden/.arden

COPY --from=build --chown=arden:arden /app/apps/server/.venv /app/.venv

USER arden

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ARDEN_DIR=/home/arden/.arden \
    PATH="/app/.venv/bin:$PATH"

EXPOSE 6877

HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=3 \
    CMD python -c "import json, urllib.request; assert json.load(urllib.request.urlopen('http://localhost:6877/health', timeout=5))['status'] == 'ok'"

CMD ["python", "-m", "arden.cli", "serve", "--host", "0.0.0.0", "--port", "6877"]
