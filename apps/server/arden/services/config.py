from collections.abc import Awaitable, Callable
from contextlib import suppress
from copy import deepcopy

from arden.config import PERSIST_KEYS, PROVIDER_KEY_FIELDS, ROLE_NAMES
from arden.llm.models import Model, Provider, add_custom_model, get_models_by_provider, remove_custom_model
from arden.logging import get_logger
from arden.settings import load_user_settings, save_user_settings

_logger = get_logger(__name__)


class ConfigService:
    def __init__(self, on_config_change: Callable[[], Awaitable[None]]):
        self._on_config_change = on_config_change

    async def _with_rollback(self, mutate: Callable[[dict], None]) -> None:
        settings = load_user_settings()
        backup = deepcopy(settings)
        mutate(settings)
        save_user_settings(settings)
        try:
            await self._on_config_change()
        except Exception:
            save_user_settings(backup)
            try:
                await self._on_config_change()
            except Exception:
                _logger.exception("Failed to reload runtime after config rollback")
            raise

    async def update(self, **fields) -> None:
        persist = {k: v for k, v in fields.items() if k in PERSIST_KEYS}
        if not persist:
            return

        def mutate(settings: dict) -> None:
            for key, value in persist.items():
                if value is None and key != "embedding_model":
                    settings.pop(key, None)
                else:
                    settings[key] = value
            if "model_roles" in persist:
                # Roles moved from a scalar per knob into one object each. Config
                # still READS the old keys so an existing install keeps its
                # models; drop them once the new shape lands so the file has a
                # single source of truth.
                for role in ROLE_NAMES:
                    settings.pop(f"{role}_model", None)
                    settings.pop(f"{role}_reasoning_effort", None)

        await self._with_rollback(mutate)

    async def connect_provider(self, provider: str, api_key: str) -> None:
        if provider not in PROVIDER_KEY_FIELDS:
            raise ValueError(f"Unknown provider: {provider}. Available: {', '.join(PROVIDER_KEY_FIELDS)}")

        def mutate(settings: dict) -> None:
            settings.setdefault("provider_keys", {})[provider] = api_key

        await self._with_rollback(mutate)

    async def disconnect_provider(self, provider: str) -> None:
        if provider not in PROVIDER_KEY_FIELDS:
            raise ValueError(f"Unknown provider: {provider}. Available: {', '.join(PROVIDER_KEY_FIELDS)}")

        provider_models = get_models_by_provider(Provider(provider))

        def mutate(settings: dict) -> None:
            provider_keys = settings.get("provider_keys", {})
            provider_keys.pop(provider, None)
            if not provider_keys:
                settings.pop("provider_keys", None)
            else:
                settings["provider_keys"] = provider_keys

            _remove_model_selections(settings, set(provider_models))
            _remove_model_reasoning_settings(settings, set(provider_models))

        await self._with_rollback(mutate)

    async def clear_provider_models(self, provider: Provider) -> None:
        provider_models = get_models_by_provider(provider)

        def mutate(settings: dict) -> None:
            _remove_model_selections(settings, set(provider_models))
            _remove_model_reasoning_settings(settings, set(provider_models))

        await self._with_rollback(mutate)

    def _valid_service_ids(self) -> set[str]:
        from arden.integrations import ALL_INTEGRATIONS

        return {f.key for i in ALL_INTEGRATIONS for f in i.service_fields}

    async def connect_service(self, service_id: str, api_key: str) -> None:
        valid = self._valid_service_ids()
        if service_id not in valid:
            raise ValueError(f"Unknown service: {service_id}. Available: {', '.join(sorted(valid))}")

        def mutate(settings: dict) -> None:
            settings.setdefault("service_keys", {})[service_id] = api_key

        await self._with_rollback(mutate)

    async def disconnect_service(self, service_id: str) -> None:
        valid = self._valid_service_ids()
        if service_id not in valid:
            raise ValueError(f"Unknown service: {service_id}. Available: {', '.join(sorted(valid))}")

        def mutate(settings: dict) -> None:
            service_keys = settings.get("service_keys", {})
            service_keys.pop(service_id, None)
            if not service_keys:
                settings.pop("service_keys", None)
            else:
                settings["service_keys"] = service_keys

        await self._with_rollback(mutate)

    async def add_mcp_server(self, name: str, config: dict) -> None:
        def mutate(settings: dict) -> None:
            settings.setdefault("mcp_servers", {})[name] = config

        await self._with_rollback(mutate)

    async def update_mcp_server(self, name: str, config: dict) -> None:
        settings = load_user_settings()
        if name not in settings.get("mcp_servers", {}):
            raise ValueError(f"MCP server {name!r} not found")

        def mutate(s: dict) -> None:
            s.get("mcp_servers", {})[name] = config

        await self._with_rollback(mutate)

    async def toggle_mcp_server(self, name: str, enabled: bool) -> None:
        settings = load_user_settings()
        if name not in settings.get("mcp_servers", {}):
            raise ValueError(f"MCP server {name!r} not found")

        def mutate(s: dict) -> None:
            if enabled:
                s.get("mcp_servers", {})[name].pop("enabled", None)
            else:
                s.get("mcp_servers", {})[name]["enabled"] = False

        await self._with_rollback(mutate)

    async def remove_mcp_server(self, name: str) -> None:
        def mutate(settings: dict) -> None:
            mcp_servers = settings.get("mcp_servers", {})
            mcp_servers.pop(name, None)
            if not mcp_servers:
                settings.pop("mcp_servers", None)
            else:
                settings["mcp_servers"] = mcp_servers

        await self._with_rollback(mutate)

    async def create_custom_model(
        self,
        *,
        model_id: str,
        base_url: str,
        context_window: int,
        max_output_tokens: int,
        api_key: str | None = None,
    ) -> Model:
        existing_custom = model_id in get_models_by_provider(Provider.CUSTOM)
        try:
            model = add_custom_model(
                model_id=model_id,
                base_url=base_url,
                context_window=context_window,
                max_output_tokens=max_output_tokens,
            )
            if api_key:
                await self._set_custom_model_key(model_id, api_key)
            else:
                await self._on_config_change()
        except Exception:
            if not existing_custom:
                await self._remove_custom_model_after_failed_create(model_id)
            raise
        return model

    async def _set_custom_model_key(self, model_id: str, api_key: str) -> None:
        def mutate(settings: dict) -> None:
            settings.setdefault("custom_model_keys", {})[model_id] = api_key

        await self._with_rollback(mutate)

    async def _remove_custom_model_after_failed_create(self, model_id: str) -> None:
        with suppress(Exception):
            remove_custom_model(model_id)
        try:
            await self._on_config_change()
        except Exception:
            _logger.exception("Failed to reload runtime after reverting custom model creation")

    async def delete_custom_model(self, model_id: str) -> None:
        existing = get_models_by_provider(Provider.CUSTOM).get(model_id)
        if existing is None:
            raise ValueError(f"Not a custom model: {model_id}")

        remove_custom_model(model_id)
        try:
            await self._remove_custom_model_settings(model_id)
        except Exception:
            await self._restore_custom_model_after_failed_delete(existing)
            raise

    async def _remove_custom_model_settings(self, model_id: str) -> None:
        def mutate(settings: dict) -> None:
            custom_keys = settings.get("custom_model_keys", {})
            custom_keys.pop(model_id, None)
            if custom_keys:
                settings["custom_model_keys"] = custom_keys
            else:
                settings.pop("custom_model_keys", None)

            _remove_model_selections(settings, {model_id})
            _remove_model_reasoning_settings(settings, {model_id})

        await self._with_rollback(mutate)

    async def _restore_custom_model_after_failed_delete(self, model: Model) -> None:
        with suppress(Exception):
            add_custom_model(
                model_id=model.id,
                base_url=model.base_url or "",
                context_window=model.max_context_tokens,
                max_output_tokens=model.max_output_tokens,
                api_key_env=model.api_key_env,
            )
        try:
            await self._on_config_change()
        except Exception:
            _logger.exception("Failed to reload runtime after restoring custom model deletion")


def _remove_model_selections(settings: dict, model_ids: set[str]) -> None:
    if settings.get("chat_model") in model_ids:
        settings.pop("chat_model")
    for role in ROLE_NAMES:
        legacy_key = f"{role}_model"
        if settings.get(legacy_key) in model_ids:
            settings.pop(legacy_key)

    roles = settings.get("model_roles")
    if roles is None:
        return
    for role in ROLE_NAMES:
        setup = roles.get(role)
        if setup is not None and setup.get("model") in model_ids:
            roles.pop(role)
    if not roles:
        settings.pop("model_roles")


def _remove_model_reasoning_settings(settings: dict, model_ids: set[str]) -> None:
    per_model = settings.get("model_reasoning_efforts")
    if not isinstance(per_model, dict):
        return
    for model_id in model_ids:
        per_model.pop(model_id, None)
    if per_model:
        settings["model_reasoning_efforts"] = per_model
    else:
        settings.pop("model_reasoning_efforts", None)
