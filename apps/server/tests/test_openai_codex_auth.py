import base64
import json
from unittest.mock import AsyncMock

from arden.llm import openai_codex_auth as auth
from arden.server.routers import providers


def _jwt(payload: dict) -> str:
    raw = json.dumps(payload).encode()
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"header.{encoded}.sig"


def test_extract_account_id_prefers_chatgpt_claim():
    token = _jwt({"chatgpt_account_id": "acct_chatgpt", "organizations": [{"id": "org_1"}]})

    assert auth.extract_account_id({"id_token": token}) == "acct_chatgpt"


def test_extract_account_id_reads_nested_openai_claim():
    token = _jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct_nested"}})

    assert auth.extract_account_id({"access_token": token}) == "acct_nested"


def test_save_and_load_tokens(tmp_path, monkeypatch):
    path = tmp_path / "openai-codex-auth.json"
    monkeypatch.setattr(auth, "TOKEN_PATH", path)
    tokens = auth.OpenAICodexTokens(access="access", refresh="refresh", expires=123, account_id="acct_1")

    auth.save_tokens(tokens)

    assert path.stat().st_mode & 0o777 == 0o600
    assert auth.load_tokens() == tokens


def test_config_defaults_to_codex_models_when_oauth_is_connected(tmp_path, monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(auth, "TOKEN_PATH", tmp_path / "openai-codex-auth.json")
    auth.save_tokens(auth.OpenAICodexTokens(access="access", refresh="refresh", expires=123))

    from arden.config import Config

    config = Config(_env_file=None)

    assert config.chat_model == "openai-codex/gpt-5.5"
    assert config.memory_model == "openai-codex/gpt-5.4-mini"
    assert config.has_providers


async def test_connected_oauth_status_reloads_runtime(monkeypatch):
    runtime = type("Runtime", (), {"reload_config": AsyncMock()})()
    status = {"connected": True, "status": "connected"}
    monkeypatch.setattr(providers, "login_status", lambda: status)

    assert await providers.get_openai_codex_oauth_status(runtime) == status
    runtime.reload_config.assert_awaited_once()
