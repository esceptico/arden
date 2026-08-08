import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from arden.llm.models import Provider, _GeneratedModelSpec, _model_from_generated_spec
from arden.llm.openai_codex_auth import CODEX_CLIENT_VERSION
from scripts.update_models import codex_model_entries, iter_filtered_models, load_codex_cache, runtime_model_entry


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
    assert runtime_model_entry(models[0])["native_deferred_tools"] is True


def test_codex_cache_converts_to_packaged_runtime_entry():
    entries = codex_model_entries(
        {
            "models": [
                {
                    "slug": "gpt-test",
                    "visibility": "list",
                    "supported_in_api": True,
                    "context_window": 272_000,
                    "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
                    "supports_search_tool": True,
                }
            ]
        }
    )

    assert entries == [
        {
            "id": "openai-codex/gpt-test",
            "provider": "openai-codex",
            "context_window": 272_000,
            "max_output_tokens": 128_000,
            "price_in": 0.0,
            "price_out": 0.0,
            "price_cache_read": 0.0,
            "price_cache_write": 0.0,
            "reasoning_efforts": ["low", "high"],
            "native_deferred_tools": True,
        }
    ]


def _write_codex_cache(path: Path, *, client_version: str, fetched_at: datetime) -> None:
    path.write_text(
        json.dumps(
            {
                "client_version": client_version,
                "fetched_at": fetched_at.isoformat().replace("+00:00", "Z"),
                "models": [],
            }
        )
    )


def test_codex_cache_must_match_runtime_client_version(tmp_path: Path):
    now = datetime(2026, 8, 8, tzinfo=UTC)
    path = tmp_path / "models_cache.json"
    _write_codex_cache(path, client_version="older", fetched_at=now)

    with pytest.raises(RuntimeError, match="does not match client"):
        load_codex_cache(path, now=now)


def test_codex_cache_must_be_recent(tmp_path: Path):
    now = datetime(2026, 8, 8, tzinfo=UTC)
    path = tmp_path / "models_cache.json"
    _write_codex_cache(path, client_version=CODEX_CLIENT_VERSION, fetched_at=now - timedelta(days=2))

    with pytest.raises(RuntimeError, match="is stale"):
        load_codex_cache(path, now=now)


def test_snapshot_entry_converts_to_runtime_model():
    model = _model_from_generated_spec(
        _GeneratedModelSpec.model_validate(
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
                "native_deferred_tools": False,
            }
        )
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
