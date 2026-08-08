import json

import pytest

from arden.llm import models
from arden.llm.models import (
    DEFAULTS,
    EMBEDDING_DEFAULTS,
    CustomModelInputError,
    CustomModelsError,
    ModelRegistry,
    Provider,
)


@pytest.fixture(autouse=True)
def isolated_models(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "_models_dir", tmp_path)
    monkeypatch.setattr(models, "_registry", ModelRegistry(DEFAULTS, EMBEDDING_DEFAULTS))
    monkeypatch.setattr(models, "_custom_loaded", False)
    return tmp_path


def test_custom_embedding_model_round_trips_through_models_json(tmp_path):
    model = models.add_custom_embedding_model(
        model_id="Qwen3-Embedding-0.6B",
        base_url="http://127.0.0.1:8081/v1",
        dim=1024,
    )

    assert model.provider is Provider.CUSTOM
    assert models.get_embedding_model(model.id) == model
    assert (tmp_path / "models.json").stat().st_mode & 0o777 == 0o600
    assert json.loads((tmp_path / "models.json").read_text()) == {
        "embedding": {
            "Qwen3-Embedding-0.6B": {
                "base_url": "http://127.0.0.1:8081/v1",
                "dim": 1024,
            }
        }
    }

    models.remove_custom_embedding_model(model.id)

    assert model.id not in models.get_embedding_models()
    assert json.loads((tmp_path / "models.json").read_text()) == {}


def test_custom_chat_model_round_trips_every_persisted_field(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "local/test": {
                    "base_url": "http://localhost:11434/v1",
                    "context_window": 32_768,
                    "max_output_tokens": 4096,
                    "api_key_env": "LOCAL_MODEL_KEY",
                    "price_in": 0.25,
                    "price_out": 0.75,
                    "reasoning_efforts": ["low", "high"],
                }
            }
        )
    )
    models.load_custom_models(tmp_path)
    loaded = models.get_model("local/test")
    models.add_custom_model("local/second", "http://localhost/v1", 8192)

    assert loaded.pricing.price_in == 0.25
    assert loaded.pricing.price_out == 0.75
    assert loaded.reasoning_efforts == ("low", "high")
    assert json.loads(path.read_text())["local/test"]["reasoning_efforts"] == ["low", "high"]


@pytest.mark.parametrize(
    "content",
    [
        b"not-json",
        b"[]",
        b'{"broken": {"base_url": "x"}}',
        (
            b'{"same":{"base_url":"http://one","context_window":8192},'
            b'"same":{"base_url":"http://two","context_window":8192}}'
        ),
        b'{"embedding":{},"embedding":{"local/embed":{"base_url":"http://two","dim":1024}}}',
    ],
)
def test_existing_invalid_file_never_becomes_an_empty_file(tmp_path, content):
    path = tmp_path / "models.json"
    path.write_bytes(content)
    before_registry = models.get_models()

    with pytest.raises(CustomModelsError):
        models.add_custom_model("local/test", "http://localhost/v1", 8192)

    assert path.read_bytes() == content
    assert models.get_models() == before_registry


def test_custom_load_is_all_or_nothing_and_retryable(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "valid": {"base_url": "http://localhost/v1", "context_window": 8192},
                "invalid": {"base_url": "http://localhost/v1", "context_window": "8192"},
            }
        )
    )

    with pytest.raises(CustomModelsError):
        models.load_custom_models(tmp_path)

    assert "valid" not in models.get_models()
    assert models._custom_loaded is False

    path.write_text(json.dumps({"valid": {"base_url": "http://localhost/v1", "context_window": 8192}}))
    models.load_custom_models(tmp_path)

    assert models.get_model("valid").provider is Provider.CUSTOM
    assert models._custom_loaded is True


def test_reserved_chat_id_cannot_replace_embedding_models(tmp_path):
    embedding = models.add_custom_embedding_model("local/embed", "http://localhost/v1", 1024)
    path = tmp_path / "models.json"
    before = path.read_bytes()

    with pytest.raises(CustomModelInputError, match="reserved"):
        models.add_custom_model("embedding", "http://localhost/v1", 8192)

    assert path.read_bytes() == before
    assert models.get_embedding_model(embedding.id) == embedding


def test_builtin_id_cannot_be_shadowed(tmp_path):
    path = tmp_path / "models.json"
    path.write_text("{}")
    builtin = DEFAULTS[0]

    with pytest.raises(ValueError, match="already exists"):
        models.add_custom_model(builtin.id, "http://localhost/v1", 8192)

    assert path.read_text() == "{}"
    assert models.get_model(builtin.id) == builtin


def test_failed_atomic_add_preserves_file_and_registry(tmp_path, monkeypatch):
    from arden import atomic_file

    path = tmp_path / "models.json"
    path.write_text("{}")

    def fail_replace(_source, _destination):
        raise OSError("replace failed")

    monkeypatch.setattr(atomic_file.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        models.add_custom_model("local/test", "http://localhost/v1", 8192)

    assert path.read_text() == "{}"
    assert "local/test" not in models.get_models()
    assert not list(tmp_path.glob(".models.json.*.tmp"))


def test_failed_atomic_delete_preserves_file_and_registry(tmp_path, monkeypatch):
    from arden import atomic_file

    model = models.add_custom_embedding_model("local/embed", "http://localhost/v1", 1024)
    path = tmp_path / "models.json"
    before = path.read_bytes()

    def fail_replace(_source, _destination):
        raise OSError("replace failed")

    monkeypatch.setattr(atomic_file.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        models.remove_custom_embedding_model(model.id)

    assert path.read_bytes() == before
    assert models.get_embedding_model(model.id) == model
    assert not list(tmp_path.glob(".models.json.*.tmp"))
