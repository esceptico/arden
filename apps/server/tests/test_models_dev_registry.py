from datetime import date

from arden.llm.models import Provider, _model_from_generated_entry
from scripts.update_models import iter_filtered_models


def _model(
    *,
    tool_call: bool = True,
    structured_output: bool = True,
    context: int = 128_000,
    output: int = 16_384,
    last_updated: str = "2026-01-01",
    input_modalities: list[str] | None = None,
    output_modalities: list[str] | None = None,
) -> dict:
    return {
        "id": "model",
        "name": "Model",
        "tool_call": tool_call,
        "structured_output": structured_output,
        "reasoning": False,
        "last_updated": last_updated,
        "modalities": {
            "input": input_modalities or ["text"],
            "output": output_modalities or ["text"],
        },
        "limit": {"context": context, "output": output},
        "cost": {"input": 0.1, "output": 0.2},
    }


def _api(models: dict[str, dict]) -> dict:
    return {
        "openrouter": {"models": models},
        "openai": {"models": {}},
        "anthropic": {"models": {}},
        "google": {"models": {}},
    }


def test_openrouter_filter_requires_structured_output_and_keeps_free_models():
    data = _api(
        {
            "vendor/good:free": _model(),
            "vendor/no-structured": _model(structured_output=False),
            "~vendor/latest": _model(),
        }
    )

    models = list(iter_filtered_models(data, today=date(2026, 5, 16)))

    assert [m["id"] for m in models] == ["vendor/good:free"]


def test_first_party_filter_does_not_require_structured_output():
    data = {
        "anthropic": {"models": {"claude-test": _model(structured_output=False)}},
        "openai": {"models": {}},
        "google": {"models": {}},
        "openrouter": {"models": {}},
    }

    models = list(iter_filtered_models(data, today=date(2026, 5, 16)))

    assert [m["id"] for m in models] == ["claude-test"]
    assert models[0]["provider"] == "anthropic"


def test_snapshot_entry_converts_to_runtime_model():
    model = _model_from_generated_entry(
        {
            "id": "openrouter/model",
            "provider": "openrouter",
            "context_window": 262_144,
            "max_output_tokens": 65_536,
            "price_in": 0.1,
            "price_out": 0.2,
            "price_cache_read": 0.03,
            "price_cache_write": 0.04,
            "reasoning_efforts": ["low", "high"],
        }
    )

    assert model.id == "openrouter/model"
    assert model.provider == Provider.OPENROUTER
    assert model.max_context_tokens == 262_144
    assert model.max_output_tokens == 65_536
    assert model.pricing.price_in == 0.1
    assert model.pricing.price_out == 0.2
    assert model.pricing.price_cache_read == 0.03
    assert model.pricing.price_cache_write == 0.04
    assert model.reasoning_efforts == ("low", "high")
