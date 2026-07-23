from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from arden.constants import COMPRESSION_TOKEN_HEADROOM
from arden.llm.models import (
    get_embedding_models as get_embedding_models_fn,
)
from arden.llm.models import (
    get_model,
    list_embedding_models,
    list_models,
)
from arden.llm.models import (
    get_models as get_models_fn,
)
from arden.server.deps import require_config_service
from arden.server.runtime import Runtime, get_runtime
from arden.server.schemas import (
    AddCustomModelRequest,
    UpdateConfigRequest,
    UpdateEmbeddingRequest,
)
from arden.services.config import ConfigService

router = APIRouter(tags=["settings"])


def _config_response(rt: Runtime) -> dict:
    config = rt.config
    memory_connected = rt.memory_records is not None
    web_client = rt.integrations.get_client("web")
    web_provider = getattr(web_client, "provider", "unknown") if web_client else "none"
    reasoning_efforts = list(get_model(config.chat_model).reasoning_efforts) if config.chat_model else []
    reasoning_effort = config.reasoning_effort_for(config.chat_model)
    known_models = get_models_fn()
    model_reasoning_efforts = {
        model_id: effort
        for model_id, effort in config.model_reasoning_efforts.items()
        if model_id in known_models and effort in known_models[model_id].reasoning_efforts
    }

    integrations: dict[str, dict] = {}
    for integration in rt.integrations.integrations.values():
        if integration.id.startswith("_") or integration.build is None:
            continue  # core builtins / notifier-only
        entry: dict = {
            "connected": integration.id in rt.integrations.clients,
            "enabled": config.integration_enabled(integration.id),
        }
        if integration.id in rt.integrations.errors:
            entry["error"] = rt.integrations.errors[integration.id]
        integrations[integration.id] = entry

    # Integration-specific extras the UI needs
    integrations.setdefault("web", {}).update(
        {
            "mode": config.web_search,
            "provider": web_provider,
        }
    )
    integrations.setdefault("slack", {}).update(
        {
            "has_user_token": bool(config.slack_user_token),
            "has_bot_token": bool(config.slack_bot_token),
        }
    )

    # Umbrella + memory (not direct integrations)
    integrations["google"] = {
        "enabled": any(config.integration_enabled(service) for service in ("gmail", "calendar", "google_drive")),
        "connected": any(
            integration_id in rt.integrations.clients for integration_id in ("gmail", "calendar", "google_drive")
        ),
        **(
            {
                "error": "; ".join(
                    e
                    for e in (
                        rt.integrations.errors.get("gmail"),
                        rt.integrations.errors.get("calendar"),
                        rt.integrations.errors.get("google_drive"),
                    )
                    if e
                )
            }
            if any(
                rt.integrations.errors.get(integration_id) for integration_id in ("gmail", "calendar", "google_drive")
            )
            else {}
        ),
    }
    integrations["memory"] = {
        "enabled": config.memory,
        "connected": memory_connected,
    }

    # Resolve the chat model's hard token ceiling so the desktop can render
    # the budget dial against absolute numbers (no second round-trip).
    try:
        chat_model_max_context = get_model(config.chat_model).max_context_tokens
    except Exception:
        chat_model_max_context = 0
    compaction_token_limit = int(chat_model_max_context * config.compression_threshold) if chat_model_max_context else 0
    compaction_token_trigger = int(compaction_token_limit * COMPRESSION_TOKEN_HEADROOM) if compaction_token_limit else 0

    return {
        **rt.config_status(),
        "chat_model": config.chat_model,
        "chat_model_max_context": chat_model_max_context,
        "compaction_token_limit": compaction_token_limit,
        "compaction_token_trigger": compaction_token_trigger,
        "research_model": config.research_model,
        "workflow_model": config.workflow_model,
        "memory_model": config.memory_model,
        "embedding_model": config.embedding_model,
        "web_search": config.web_search,
        "web_search_provider": web_provider,
        "max_depth": config.max_depth,
        "reasoning_effort": reasoning_effort,
        "reasoning_efforts": reasoning_efforts,
        "model_reasoning_efforts": model_reasoning_efforts,
        "compression_threshold": config.compression_threshold,
        "max_messages": config.max_messages,
        "compression_keep_ratio": config.compression_keep_ratio,
        "summary_max_tokens": config.summary_max_tokens,
        "consolidation_interval": config.consolidation_interval,
        "memory_enabled": memory_connected,
        "integrations": integrations,
        "tool_overrides": {name: decision.value for name, decision in config.tool_overrides.items()},
    }


# --- Config ---


def _reasoning_efforts(model_id: str | None) -> tuple[str, ...]:
    return get_model(model_id).reasoning_efforts if model_id else ()


def _validate_reasoning_patch(fields: dict, config) -> None:
    target_model = fields.pop("reasoning_model", None) or fields.get("chat_model", config.chat_model)
    efforts = _reasoning_efforts(target_model)

    if "reasoning_effort" in fields:
        effort = fields.pop("reasoning_effort")
        if effort is not None and effort not in efforts:
            available = ", ".join(efforts) or "none"
            raise HTTPException(
                status_code=400,
                detail=f"reasoning_effort {effort!r} is not supported by {target_model!r}; available: {available}",
            )
        per_model = dict(config.model_reasoning_efforts)
        if target_model:
            if effort is None:
                per_model.pop(target_model, None)
            else:
                per_model[target_model] = effort
        fields["model_reasoning_efforts"] = per_model
        return


@router.get("/config")
async def get_config(runtime: Runtime = Depends(get_runtime)):
    return _config_response(runtime)


@router.get("/models")
async def get_models(runtime: Runtime = Depends(get_runtime)):
    all_models = get_models_fn()
    groups: dict[str, list[str]] = {}
    for mid, m in all_models.items():
        provider_key = m.provider.value
        groups.setdefault(provider_key, []).append(mid)

    config = runtime.config
    return {
        "models": list_models(),
        "groups": [{"provider": p, "models": ms} for p, ms in groups.items()],
        "reasoning_efforts": {
            mid: list(model.reasoning_efforts) for mid, model in all_models.items() if model.reasoning_efforts
        },
        "chat_model": config.chat_model,
        "research_model": config.research_model,
        "workflow_model": config.workflow_model,
        "memory_model": config.memory_model,
    }


@router.patch("/config")
async def update_config(
    req: UpdateConfigRequest,
    runtime: Runtime = Depends(get_runtime),
    cfg_svc: ConfigService = Depends(require_config_service),
):
    fields = req.model_dump(exclude_unset=True, mode="json")
    if toggles := fields.pop("integrations", None):
        memory = toggles.pop("memory", None)
        states = dict(runtime.config.integration_states)
        for integration_id, enabled in toggles.items():
            if enabled is not None:
                states[integration_id] = enabled
        if toggles:
            fields["integration_states"] = states
        if memory is not None:
            fields["memory"] = memory
    try:
        _validate_reasoning_patch(fields, runtime.config)
        await cfg_svc.update(**fields)
    except (ValueError, ValidationError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _config_response(runtime)


@router.post("/reload")
async def reload_runtime(runtime: Runtime = Depends(get_runtime)):
    """Re-read config from disk and rebuild integrations, memory, MCP, etc.

    Use after editing .env or settings.json directly outside the UI.
    """
    await runtime.reload_config()
    return {"status": "reloaded", **runtime.config_status()}


# --- Custom models ---


@router.post("/models/custom")
async def create_custom_model(
    req: AddCustomModelRequest,
    cfg_svc: ConfigService = Depends(require_config_service),
):
    try:
        model = await cfg_svc.create_custom_model(
            model_id=req.model_id,
            base_url=req.base_url,
            context_window=req.context_window,
            max_output_tokens=req.max_output_tokens,
            api_key=req.api_key,
        )
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "created", "model_id": model.id}


@router.delete("/models/custom/{model_id:path}")
async def delete_custom_model(
    model_id: str,
    runtime: Runtime = Depends(get_runtime),
    cfg_svc: ConfigService = Depends(require_config_service),
):
    config = runtime.config
    try:
        await cfg_svc.delete_custom_model(
            model_id,
            active_models={
                "chat_model": config.chat_model,
                "research_model": config.research_model,
                "workflow_model": config.workflow_model,
                "memory_model": config.memory_model,
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "deleted", "model_id": model_id}


# --- Embedding ---


@router.get("/models/embedding")
async def get_embedding_models(runtime: Runtime = Depends(get_runtime)):
    all_models = get_embedding_models_fn()
    groups: dict[str, list[str]] = {}
    for mid, m in all_models.items():
        groups.setdefault(m.provider.value, []).append(mid)
    return {
        "models": list_embedding_models(),
        "groups": [{"provider": p, "models": ms} for p, ms in groups.items()],
        "current": runtime.config.embedding_model,
    }


@router.post("/config/embedding")
async def update_embedding_model(
    req: UpdateEmbeddingRequest,
    runtime: Runtime = Depends(get_runtime),
    cfg_svc: ConfigService = Depends(require_config_service),
):
    old_model = runtime.config.embedding_model

    try:
        await cfg_svc.update(embedding_model=req.embedding_model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if runtime.config.embedding_model == old_model:
        return {"status": "unchanged", "embedding_model": old_model}

    return {
        "status": "reindexing",
        "embedding_model": req.embedding_model,
        "embedding_dim": runtime.config.embedding.dim if runtime.config.embedding else None,
    }
