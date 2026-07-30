import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from arden.config import PROVIDER_KEY_FIELDS
from arden.llm.models import (
    Provider,
    get_embedding_models_by_provider,
    get_models_by_provider,
    provider_label,
)
from arden.llm.openai_codex_auth import clear_tokens, load_tokens, login_status, start_browser_login
from arden.server.deps import require_config_service
from arden.server.runtime import Runtime, get_runtime
from arden.server.schemas import ConnectProviderRequest, ConnectServiceRequest
from arden.services.config import ConfigService
from arden.settings import mask_api_key

router = APIRouter(tags=["providers"])


PROVIDER_META = {
    "anthropic": {
        "name": provider_label(Provider.ANTHROPIC),
        "env_var": "ANTHROPIC_API_KEY",
        "provider": Provider.ANTHROPIC,
    },
    "openai": {
        "name": provider_label(Provider.OPENAI),
        "env_var": "OPENAI_API_KEY",
        "provider": Provider.OPENAI,
    },
    "openai-codex": {
        "name": provider_label(Provider.OPENAI_CODEX),
        "provider": Provider.OPENAI_CODEX,
        "auth_type": "oauth",
    },
    "google": {
        "name": provider_label(Provider.GOOGLE),
        "env_var": "GEMINI_API_KEY",
        "provider": Provider.GOOGLE,
    },
    "openrouter": {
        "name": provider_label(Provider.OPENROUTER),
        "env_var": "OPENROUTER_API_KEY",
        "provider": Provider.OPENROUTER,
    },
}


def connected_providers(config) -> set[Provider]:
    """Providers the user can actually run models through right now."""
    connected: set[Provider] = set()
    for pid, meta in PROVIDER_META.items():
        if meta.get("auth_type") == "oauth":
            if load_tokens():
                connected.add(meta["provider"])
        elif getattr(config, PROVIDER_KEY_FIELDS[pid], None):
            connected.add(meta["provider"])
    if get_models_by_provider(Provider.CUSTOM) or get_embedding_models_by_provider(Provider.CUSTOM):
        connected.add(Provider.CUSTOM)
    return connected


@router.get("/providers")
async def get_providers(runtime: Runtime = Depends(get_runtime)):
    config = runtime.config
    providers = []

    for pid, meta in PROVIDER_META.items():
        auth_type = meta.get("auth_type", "api_key")
        if auth_type == "oauth":
            tokens = load_tokens()
            key = tokens.account_id if tokens else None
            connected = bool(tokens)
            key_hint = f"acct {mask_api_key(key)}" if key else ("signed in" if connected else None)
            from_env = False
        else:
            field = PROVIDER_KEY_FIELDS[pid]
            key = getattr(config, field, None)
            connected = bool(key)
            key_hint = mask_api_key(key)
            from_env = bool(os.environ.get(meta["env_var"]))

        models = get_models_by_provider(meta["provider"])
        embedding_models = get_embedding_models_by_provider(meta["provider"])

        providers.append(
            {
                "id": pid,
                "name": meta["name"],
                "connected": connected,
                "key_hint": key_hint,
                "from_env": from_env,
                "auth_type": auth_type,
                "models": list(models.keys()),
                "embedding_models": list(embedding_models.keys()),
            }
        )

    custom_models = get_models_by_provider(Provider.CUSTOM)
    custom_embedding_models = get_embedding_models_by_provider(Provider.CUSTOM)
    providers.append(
        {
            "id": "custom",
            "name": "Custom (OpenAI-compatible)",
            "connected": bool(custom_models or custom_embedding_models),
            "key_hint": None,
            "from_env": False,
            "model_count": len(custom_models),
            "models": [
                {"id": mid, "base_url": m.base_url, "context_window": m.max_context_tokens}
                for mid, m in custom_models.items()
            ],
            "embedding_models": list(custom_embedding_models.keys()),
        }
    )

    return {"providers": providers}


@router.post("/providers/{provider_id}/connect")
async def connect_provider(
    provider_id: str,
    req: ConnectProviderRequest,
    cfg_svc: ConfigService = Depends(require_config_service),
):
    if provider_id == "openai-codex":
        raise HTTPException(status_code=400, detail="OpenAI Codex uses browser sign-in, not an API key")
    try:
        await cfg_svc.connect_provider(provider_id, req.api_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if req.chat_model:
        try:
            await cfg_svc.update(chat_model=req.chat_model)
        except (ValueError, ValidationError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    return {"status": "connected", "provider": provider_id}


@router.delete("/providers/{provider_id}")
async def disconnect_provider(
    provider_id: str,
    cfg_svc: ConfigService = Depends(require_config_service),
):
    if provider_id == "openai-codex":
        clear_tokens()
        await cfg_svc.clear_provider_models(Provider.OPENAI_CODEX)
        return {"status": "disconnected", "provider": provider_id}
    try:
        await cfg_svc.disconnect_provider(provider_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "disconnected", "provider": provider_id}


@router.post("/providers/openai-codex/oauth/browser/start")
async def start_openai_codex_oauth():
    try:
        return start_browser_login()
    except OSError as e:
        raise HTTPException(
            status_code=409,
            detail=f"OpenAI Codex sign-in callback could not bind localhost:1455: {e}",
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/providers/openai-codex/oauth/status")
async def get_openai_codex_oauth_status(runtime: Runtime = Depends(get_runtime)):
    status = login_status()
    if status.get("connected") is True:
        await runtime.reload_config()
    return status


@router.get("/services")
async def get_services(runtime: Runtime = Depends(get_runtime)):
    config = runtime.config
    services = []
    for integration in runtime.integrations.integrations.values():
        for f in integration.service_fields:
            key = getattr(config, f.key, None)
            from_env = bool(f.env_var and os.environ.get(f.env_var))
            services.append(
                {
                    "id": f.key,
                    "name": f.label,
                    "connected": bool(key),
                    "key_hint": mask_api_key(key),
                    "from_env": from_env,
                }
            )
    return {"services": services}


@router.post("/services/{service_id}/connect")
async def connect_service(
    service_id: str,
    req: ConnectServiceRequest,
    cfg_svc: ConfigService = Depends(require_config_service),
):
    try:
        await cfg_svc.connect_service(service_id, req.api_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "connected", "service": service_id}


@router.delete("/services/{service_id}")
async def disconnect_service(
    service_id: str,
    cfg_svc: ConfigService = Depends(require_config_service),
):
    try:
        await cfg_svc.disconnect_service(service_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "disconnected", "service": service_id}


@router.get("/tool-providers")
async def list_tool_providers(runtime: Runtime = Depends(get_runtime)):
    """Unified list of tool providers: native integrations plus MCP servers."""
    native = runtime.integrations.list_providers()
    mcp = runtime.mcp_manager.list_providers() if runtime.mcp_manager else []
    return {
        "providers": [
            {
                "id": p.id,
                "label": p.label,
                "kind": p.kind,
                "status": p.health.status,
                "detail": p.health.detail,
                "tool_count": p.tool_count,
            }
            for p in [*native, *mcp]
        ]
    }
