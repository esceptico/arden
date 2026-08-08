from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from arden.config import ROLE_NAMES
from arden.constants import COMPRESSION_TOKEN_HEADROOM
from arden.llm.models import (
    Provider,
    get_model,
    list_embedding_models,
    provider_label,
)
from arden.llm.models import (
    get_embedding_models as get_embedding_models_fn,
)
from arden.llm.models import (
    get_models as get_models_fn,
)
from arden.server.deps import require_config_service
from arden.server.routers.providers import connected_providers
from arden.server.runtime import Runtime, get_runtime
from arden.server.schemas import (
    AddCustomEmbeddingModelRequest,
    AddCustomModelRequest,
    UpdateConfigRequest,
    UpdateEmbeddingRequest,
)
from arden.services.config import ConfigService

router = APIRouter(tags=["settings"])


def _config_response(rt: Runtime) -> dict:
    config = rt.config
    memory_connected = rt.fact_service is not None
    web_client = rt.integrations.get_client("web")
    web_provider = getattr(web_client, "provider", "unknown") if web_client else "none"
    chat_model = get_model(config.chat_model) if config.chat_model else None
    reasoning_efforts = list(chat_model.reasoning_efforts) if chat_model else []
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

    chat_model_max_context = chat_model.max_context_tokens if chat_model else 0
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
        "max_space_gb": config.max_space_gb,
        "storage_backup_retention_days": config.storage_backup_retention_days,
        "storage_allow_archived_cleanup": config.storage_allow_archived_cleanup,
        "storage_allow_current_cleanup": config.storage_allow_current_cleanup,
        "storage_allow_delete_cold_chats": config.storage_allow_delete_cold_chats,
        "storage_current_inactive_days": config.storage_current_inactive_days,
        "storage_current_minimum": config.storage_current_minimum,
        "memory_enabled": memory_connected,
        "integrations": integrations,
        "tool_overrides": {name: decision.value for name, decision in config.tool_overrides.items()},
        "model_roles": {role: config.role_setup(role).model_dump() for role in ROLE_NAMES},
    }


# --- Config ---


def _reasoning_efforts(model_id: str | None) -> tuple[str, ...]:
    return get_model(model_id).reasoning_efforts if model_id else ()


def _merge_role_patch(fields: dict, config) -> None:
    """Fold a role patch onto stored setup so touching one role — or one knob
    within it — leaves the rest alone, and check each role's effort against the
    model THAT role runs on rather than the chat model."""
    patch = fields.pop("model_roles", None)
    if not patch:
        return
    merged = {role: config.role_setup(role).model_dump() for role in ROLE_NAMES}
    for role, changes in patch.items():
        if role not in merged:
            raise HTTPException(status_code=400, detail=f"Unknown model role {role!r}; expected one of {ROLE_NAMES}")
        merged[role].update(changes)
        effort = merged[role].get("reasoning_effort")
        if effort is None:
            continue
        model_id = merged[role].get("model")
        efforts = _reasoning_efforts(model_id)
        if effort not in efforts:
            available = ", ".join(efforts) or "none"
            raise HTTPException(
                status_code=400,
                detail=f"reasoning_effort {effort!r} is not supported by {model_id!r}; available: {available}",
            )
    fields["model_roles"] = merged


def _validate_reasoning_patch(fields: dict, config) -> None:
    _merge_role_patch(fields, config)
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
    config = runtime.config
    all_models = get_models_fn()
    connected = connected_providers(config)
    active = {
        config.chat_model,
        config.research_model,
        config.workflow_model,
        config.memory_model,
        config.auxiliary_model,
    }
    # Pickers list connected providers only; an actively configured model stays
    # visible even if its provider was disconnected, so the selection keeps its
    # label until the user picks a reachable one.
    visible = {mid: m for mid, m in all_models.items() if m.provider in connected or mid in active}
    groups: dict[str, list[str]] = {}
    for mid, m in visible.items():
        provider_key = m.provider.value
        groups.setdefault(provider_key, []).append(mid)

    return {
        "models": list(visible),
        "groups": [
            {
                "provider": provider_key,
                "label": provider_label(Provider(provider_key)),
                "models": model_ids,
            }
            for provider_key, model_ids in groups.items()
        ],
        "reasoning_efforts": {
            mid: list(model.reasoning_efforts) for mid, model in visible.items() if model.reasoning_efforts
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
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "created", "model_id": model.id}


@router.post("/models/custom/embedding")
async def create_custom_embedding_model(
    req: AddCustomEmbeddingModelRequest,
    cfg_svc: ConfigService = Depends(require_config_service),
):
    try:
        model = await cfg_svc.create_custom_embedding_model(
            model_id=req.model_id,
            base_url=req.base_url,
            dim=req.dimensions,
            api_key=req.api_key,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"status": "created", "model_id": model.id}


@router.delete("/models/custom/embedding/{model_id:path}")
async def delete_custom_embedding_model(
    model_id: str,
    cfg_svc: ConfigService = Depends(require_config_service),
):
    try:
        await cfg_svc.delete_custom_embedding_model(model_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"status": "deleted", "model_id": model_id}


@router.delete("/models/custom/{model_id:path}")
async def delete_custom_model(
    model_id: str,
    cfg_svc: ConfigService = Depends(require_config_service),
):
    try:
        await cfg_svc.delete_custom_model(model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "deleted", "model_id": model_id}


# --- Embedding ---


@router.get("/models/embedding")
async def get_embedding_models(runtime: Runtime = Depends(get_runtime)):
    all_models = get_embedding_models_fn()
    groups: dict[str, list[str]] = {}
    for mid, m in all_models.items():
        if mid != runtime.config.embedding_model and not runtime.embedding_model_available(mid):
            continue
        groups.setdefault(m.provider.value, []).append(mid)
    return {
        "models": [mid for mid in list_embedding_models() if any(mid in values for values in groups.values())],
        "groups": [
            {"provider": provider, "label": provider_label(Provider(provider)), "models": models}
            for provider, models in groups.items()
        ],
        "current": runtime.config.embedding_model,
    }


@router.post("/config/embedding")
async def update_embedding_model(
    req: UpdateEmbeddingRequest,
    runtime: Runtime = Depends(get_runtime),
):
    try:
        await runtime.update_embedding_model(req.embedding_model, req.confirmed)
    except (ValueError, ValidationError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return await runtime.get_index_status()
