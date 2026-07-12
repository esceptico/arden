from ntrp.llm.models import Model, Pricing, Provider, _derive_codex_models


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
