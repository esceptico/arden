from ntrp.llm.models import (
    FALLBACK_DEFAULTS,
    Model,
    ModelRegistry,
    Pricing,
    Provider,
    _derive_codex_models,
)


def test_codex_models_include_supported_openai_models():
    generated = [
        Model("gpt-5.6-sol", Provider.OPENAI, 272_000),
        Model("gpt-5.6-luna", Provider.OPENAI, 272_000),
        Model("gpt-5.6-terra", Provider.OPENAI, 272_000),
        Model("gpt-5.3-codex-spark", Provider.OPENAI, 272_000),
    ]

    derived = _derive_codex_models(generated)

    assert derived == [
        Model(f"openai-codex/{name}", Provider.OPENAI_CODEX, 272_000, pricing=Pricing(0, 0))
        for name in ("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-terra")
    ]


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


def test_codex_fallback_matches_supported_api_models():
    fallback = {
        model.id: model
        for model in FALLBACK_DEFAULTS
        if model.provider == Provider.OPENAI_CODEX
    }

    assert set(fallback) == {
        "openai-codex/gpt-5.4",
        "openai-codex/gpt-5.4-mini",
        "openai-codex/gpt-5.5",
        "openai-codex/gpt-5.6-luna",
        "openai-codex/gpt-5.6-sol",
        "openai-codex/gpt-5.6-terra",
    }
    assert all(model.native_deferred_tools for model in fallback.values())
