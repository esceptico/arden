from typing import Any

import httpx

from ntrp.llm.models import Model, Pricing, Provider, replace_provider_models
from ntrp.llm.openai_codex_auth import (
    CODEX_BASE_URL,
    CODEX_CLIENT_VERSION,
    codex_request_headers,
    get_valid_tokens,
)
from ntrp.logging import get_logger

_logger = get_logger(__name__)


def parse_codex_catalog(payload: object) -> list[Model]:
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        return []

    models: list[Model] = []
    for raw in payload["models"]:
        if not isinstance(raw, dict):
            continue
        if raw.get("visibility") != "list" or raw.get("supported_in_api") is not True:
            continue
        slug = raw.get("slug")
        context_window = raw.get("context_window")
        if not isinstance(slug, str) or not slug or not isinstance(context_window, int):
            continue
        if context_window <= 0:
            continue

        reasoning_efforts = tuple(
            effort
            for item in raw.get("supported_reasoning_levels", [])
            if isinstance(item, dict) and isinstance((effort := item.get("effort")), str)
        )
        max_output_tokens = raw.get("max_output_tokens")
        if not isinstance(max_output_tokens, int) or max_output_tokens <= 0:
            max_output_tokens = 128_000

        models.append(
            Model(
                id=f"openai-codex/{slug}",
                provider=Provider.OPENAI_CODEX,
                max_context_tokens=context_window,
                max_output_tokens=max_output_tokens,
                pricing=Pricing(0, 0),
                reasoning_efforts=reasoning_efforts,
                native_deferred_tools=raw.get("supports_search_tool") is True,
            )
        )
    return models


async def _fetch_catalog(client: Any) -> list[Model]:
    tokens = await get_valid_tokens()
    response = await client.get(
        f"{CODEX_BASE_URL}/models",
        params={"client_version": CODEX_CLIENT_VERSION},
        headers=codex_request_headers(tokens),
    )
    response.raise_for_status()
    return parse_codex_catalog(response.json())


async def refresh_codex_models(*, client: Any | None = None) -> bool:
    try:
        if client is not None:
            models = await _fetch_catalog(client)
        else:
            async with httpx.AsyncClient(timeout=30.0) as owned_client:
                models = await _fetch_catalog(owned_client)
    except RuntimeError:
        return False
    except (httpx.HTTPError, ValueError):
        _logger.warning("Failed to refresh OpenAI Codex model catalog", exc_info=True)
        return False

    if not models:
        _logger.warning("OpenAI Codex model catalog was empty; retaining existing models")
        return False
    return replace_provider_models(Provider.OPENAI_CODEX, models)
