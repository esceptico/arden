import json

import pytest

from arden.llm import models
from arden.llm.models import ModelCatalogError, Provider


def _entry(model_id: str, provider: str) -> dict:
    return {
        "id": model_id,
        "provider": provider,
        "context_window": 128_000,
        "max_output_tokens": 8192,
        "price_in": 1.0,
        "price_out": 2.0,
        "price_cache_read": 0.25,
        "price_cache_write": 0.5,
        "reasoning_efforts": ["low", "high"],
        "native_deferred_tools": True,
    }


def _complete_catalog() -> list[dict]:
    providers = ("anthropic", "openai", "openai-codex", "google", "openrouter")
    return [_entry(f"{provider}/test", provider) for provider in providers]


def _use_catalog(path, monkeypatch) -> None:
    monkeypatch.setattr(models, "_generated_models_path", lambda: path)


def test_packaged_catalog_decodes_complete_typed_models(tmp_path, monkeypatch):
    path = tmp_path / "generated_models.json"
    path.write_text(json.dumps(_complete_catalog()))
    _use_catalog(path, monkeypatch)

    loaded = models._load_generated_models()

    assert {model.provider for model in loaded} == {
        Provider.ANTHROPIC,
        Provider.OPENAI,
        Provider.OPENAI_CODEX,
        Provider.GOOGLE,
        Provider.OPENROUTER,
    }
    assert all(model.max_context_tokens == 128_000 for model in loaded)
    assert all(model.reasoning_efforts == ("low", "high") for model in loaded)


@pytest.mark.parametrize("content", [None, "not-json", "{}", "[]"])
def test_packaged_catalog_never_falls_back_when_missing_or_malformed(tmp_path, monkeypatch, content):
    path = tmp_path / "generated_models.json"
    if content is not None:
        path.write_text(content)
    _use_catalog(path, monkeypatch)

    with pytest.raises(ModelCatalogError):
        models._load_generated_models()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rows: rows.__setitem__(1, {**rows[1], "context_window": "128000"}),
        lambda rows: rows.__setitem__(1, {**rows[1], "price_in": float("nan")}),
        lambda rows: rows.__setitem__(1, {**rows[1], "max_output_tokens": 0}),
        lambda rows: rows.__setitem__(1, {**rows[1], "reasoning_efforts": ["low", 2]}),
        lambda rows: rows[1].pop("price_out"),
        lambda rows: rows[1].update({"supports_deferred_tools": rows[1].pop("native_deferred_tools")}),
    ],
)
def test_packaged_catalog_rejects_invalid_entry_fields(tmp_path, monkeypatch, mutate):
    path = tmp_path / "generated_models.json"
    rows = _complete_catalog()
    mutate(rows)
    path.write_text(json.dumps(rows))
    _use_catalog(path, monkeypatch)

    with pytest.raises(ModelCatalogError):
        models._load_generated_models()


def test_packaged_catalog_rejects_duplicate_ids(tmp_path, monkeypatch):
    path = tmp_path / "generated_models.json"
    rows = _complete_catalog()
    rows[1]["id"] = rows[0]["id"]
    path.write_text(json.dumps(rows))
    _use_catalog(path, monkeypatch)

    with pytest.raises(ModelCatalogError, match="duplicate IDs"):
        models._load_generated_models()


def test_packaged_catalog_requires_every_provider(tmp_path, monkeypatch):
    path = tmp_path / "generated_models.json"
    path.write_text(json.dumps(_complete_catalog()[:-1]))
    _use_catalog(path, monkeypatch)

    with pytest.raises(ModelCatalogError, match="missing providers: openrouter"):
        models._load_generated_models()
