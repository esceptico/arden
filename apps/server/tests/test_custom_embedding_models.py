import json

from arden.llm import models
from arden.llm.models import DEFAULTS, EMBEDDING_DEFAULTS, ModelRegistry, Provider


def test_custom_embedding_model_round_trips_through_models_json(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "_models_dir", tmp_path)
    monkeypatch.setattr(models, "_registry", ModelRegistry(DEFAULTS, EMBEDDING_DEFAULTS))

    model = models.add_custom_embedding_model(
        model_id="Qwen3-Embedding-0.6B",
        base_url="http://127.0.0.1:8081/v1",
        dim=1024,
    )

    assert model.provider is Provider.CUSTOM
    assert models.get_embedding_model(model.id) == model
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
