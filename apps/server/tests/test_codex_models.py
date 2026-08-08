from arden.llm.models import (
    DEFAULTS,
    Model,
    ModelRegistry,
    Provider,
)


def test_registry_replaces_only_target_provider():
    custom = Model("custom/model", Provider.CUSTOM, 128_000)
    standard = Model("gpt-standard", Provider.OPENAI, 128_000)
    previous = Model("openai-codex/old", Provider.OPENAI_CODEX, 128_000)
    current = Model("openai-codex/current", Provider.OPENAI_CODEX, 256_000)
    registry = ModelRegistry([standard, previous, custom])

    assert registry.replace_provider_models(Provider.OPENAI_CODEX, [current]) is True
    assert registry.get_models_by_provider(Provider.OPENAI_CODEX) == {current.id: current}
    assert registry.get_model(custom.id) == custom
    assert registry.get_model(standard.id) == standard
    assert list(registry.get_models()) == [standard.id, current.id, custom.id]
    assert registry.replace_provider_models(Provider.OPENAI_CODEX, [current]) is False


def test_registry_reads_explicit_deferred_tool_capability():
    model = Model(
        "not-name-derived",
        Provider.OPENAI,
        128_000,
        native_deferred_tools=True,
    )
    registry = ModelRegistry([model])

    assert registry.supports_native_deferred_tools(model.id) is True
    assert registry.supports_native_deferred_tools("missing") is False


def test_packaged_catalog_includes_supported_codex_models():
    codex = {model.id: model for model in DEFAULTS if model.provider == Provider.OPENAI_CODEX}

    assert set(codex) == {
        "openai-codex/gpt-5.4",
        "openai-codex/gpt-5.4-mini",
        "openai-codex/gpt-5.5",
        "openai-codex/gpt-5.6-luna",
        "openai-codex/gpt-5.6-sol",
        "openai-codex/gpt-5.6-terra",
    }
    assert all(model.native_deferred_tools for model in codex.values())
    assert {model.max_context_tokens for model in codex.values()} == {272_000}
    assert codex["openai-codex/gpt-5.6-sol"].reasoning_efforts == (
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    )
