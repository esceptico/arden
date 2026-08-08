from typing import Any

import httpx

from arden.llm.codex_catalog import decode_codex_catalog
from arden.llm.models import Model, Pricing, Provider, replace_provider_models
from arden.llm.openai_codex_auth import (
    CODEX_BASE_URL,
    CODEX_CLIENT_VERSION,
    codex_request_headers,
    get_valid_tokens,
)
from arden.logging import get_logger

_logger = get_logger(__name__)


def parse_codex_catalog(payload: object) -> list[Model]:
    return [
        Model(
            id=f"openai-codex/{spec.slug}",
            provider=Provider.OPENAI_CODEX,
            max_context_tokens=spec.context_window,
            max_output_tokens=spec.max_output_tokens,
            pricing=Pricing(0, 0),
            reasoning_efforts=spec.reasoning_efforts,
            native_deferred_tools=spec.native_deferred_tools,
        )
        for spec in decode_codex_catalog(payload)
    ]


async def _fetch_catalog(client: Any) -> list[Model]:
    tokens = await get_valid_tokens()
    headers = codex_request_headers(tokens)
    headers["Authorization"] = f"Bearer {tokens.access}"
    response = await client.get(
        f"{CODEX_BASE_URL}/models",
        params={"client_version": CODEX_CLIENT_VERSION},
        headers=headers,
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
    except (httpx.HTTPError, ValueError) as exc:
        _logger.warning("Failed to refresh OpenAI Codex model catalog: %s", exc)
        return False

    if not models:
        _logger.warning("OpenAI Codex model catalog was empty; retaining existing models")
        return False
    return replace_provider_models(Provider.OPENAI_CODEX, models)
