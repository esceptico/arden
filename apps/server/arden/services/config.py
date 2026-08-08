import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy

from arden.config import PERSIST_KEYS, PROVIDER_KEY_FIELDS, ROLE_NAMES
from arden.llm.models import (
    EmbeddingModel,
    Model,
    Provider,
    add_custom_embedding_model,
    add_custom_model,
    get_embedding_models_by_provider,
    get_models_by_provider,
    remove_custom_embedding_model,
    remove_custom_model,
)
from arden.settings import load_user_settings, restore_user_settings, save_user_settings


class ConfigService:
    def __init__(self, on_config_change: Callable[[], Awaitable[None]]):
        self._on_config_change = on_config_change
        self._mutation_lock = asyncio.Lock()

    async def _with_rollback(self, mutate: Callable[[dict], None]) -> None:
        async with self._mutation_lock:
            await self._mutate_settings_locked(mutate)

    async def _mutate_settings_locked(self, mutate: Callable[[dict], None]) -> dict:
        settings = load_user_settings()
        backup = deepcopy(settings)
        mutate(settings)
        save_user_settings(settings)
        try:
            await self._on_config_change()
        except BaseException as primary_error:
            await _raise_after_recovery(
                primary_error,
                self._restore_config(backup),
                "Config reload and rollback both failed",
            )
        return backup

    async def _restore_config(self, settings: dict) -> None:
        restore_user_settings(settings)
        await self._on_config_change()

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
        async with self._mutation_lock:
            settings = self._settings_with_custom_key(model_id, api_key)
            model = add_custom_model(
                model_id=model_id,
                base_url=base_url,
                context_window=context_window,
                max_output_tokens=max_output_tokens,
            )
            try:
                if settings is not None:
                    save_user_settings(settings[0])
                await self._on_config_change()
            except BaseException as primary_error:
                await _raise_after_recovery(
                    primary_error,
                    self._revert_custom_create(
                        lambda: remove_custom_model(model_id),
                        settings[1] if settings is not None else None,
                    ),
                    "Custom model creation and rollback both failed",
                )
            return model

    async def create_custom_embedding_model(
        self,
        *,
        model_id: str,
        base_url: str,
        dim: int,
        api_key: str | None = None,
    ) -> EmbeddingModel:
        async with self._mutation_lock:
            settings = self._settings_with_custom_key(model_id, api_key)
            model = add_custom_embedding_model(model_id=model_id, base_url=base_url, dim=dim)
            try:
                if settings is not None:
                    save_user_settings(settings[0])
                await self._on_config_change()
            except BaseException as primary_error:
                await _raise_after_recovery(
                    primary_error,
                    self._revert_custom_create(
                        lambda: remove_custom_embedding_model(model_id),
                        settings[1] if settings is not None else None,
                    ),
                    "Custom embedding model creation and rollback both failed",
                )
            return model

    @staticmethod
    def _settings_with_custom_key(model_id: str, api_key: str | None) -> tuple[dict, dict] | None:
        if not api_key:
            return None
        settings = load_user_settings()
        backup = deepcopy(settings)
        settings.setdefault("custom_model_keys", {})[model_id] = api_key
        return settings, backup

    async def delete_custom_model(self, model_id: str) -> None:
        async with self._mutation_lock:
            if model_id not in get_models_by_provider(Provider.CUSTOM):
                raise ValueError(f"Not a custom model: {model_id}")
            backup = await self._mutate_settings_locked(
                lambda settings: _remove_custom_model_settings(settings, model_id)
            )
            try:
                remove_custom_model(model_id)
            except BaseException as primary_error:
                await _raise_after_recovery(
                    primary_error,
                    self._restore_config(backup),
                    "Custom model deletion and rollback both failed",
                )

    async def delete_custom_embedding_model(self, model_id: str) -> None:
        async with self._mutation_lock:
            if model_id not in get_embedding_models_by_provider(Provider.CUSTOM):
                raise ValueError(f"Not a custom embedding model: {model_id}")
            backup = await self._mutate_settings_locked(
                lambda settings: _remove_custom_embedding_model_settings(settings, model_id)
            )
            try:
                remove_custom_embedding_model(model_id)
            except BaseException as primary_error:
                await _raise_after_recovery(
                    primary_error,
                    self._restore_config(backup),
                    "Custom embedding model deletion and rollback both failed",
                )

    async def _revert_custom_create(
        self,
        remove_model: Callable[[], None],
        settings_backup: dict | None,
    ) -> None:
        errors: list[BaseException] = []
        try:
            remove_model()
        except BaseException as error:
            errors.append(error)
        if settings_backup is not None:
            try:
                restore_user_settings(settings_backup)
            except BaseException as error:
                errors.append(error)
        try:
            await self._on_config_change()
        except BaseException as error:
            errors.append(error)
        if errors:
            raise BaseExceptionGroup("Custom model rollback failed", errors)


def _remove_model_selections(settings: dict, model_ids: set[str]) -> None:
    if settings.get("chat_model") in model_ids:
        settings.pop("chat_model")

    roles = settings.get("model_roles")
    if roles is None:
        return
    for role in ROLE_NAMES:
        setup = roles.get(role)
        if setup is not None and setup.get("model") in model_ids:
            roles.pop(role)
    if not roles:
        settings.pop("model_roles")


def _remove_custom_key(settings: dict, model_id: str) -> None:
    custom_keys = settings.get("custom_model_keys", {})
    custom_keys.pop(model_id, None)
    if custom_keys:
        settings["custom_model_keys"] = custom_keys
    else:
        settings.pop("custom_model_keys", None)


def _remove_custom_model_settings(settings: dict, model_id: str) -> None:
    _remove_custom_key(settings, model_id)
    _remove_model_selections(settings, {model_id})
    _remove_model_reasoning_settings(settings, {model_id})


def _remove_custom_embedding_model_settings(settings: dict, model_id: str) -> None:
    _remove_custom_key(settings, model_id)
    if settings.get("embedding_model") == model_id:
        settings["embedding_model"] = None


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


async def _finish_recovery(recovery: Awaitable[None]) -> asyncio.CancelledError | None:
    task = asyncio.create_task(recovery)
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            cancellation = error
        except BaseException:
            break
    task.result()
    return cancellation


async def _raise_after_recovery(
    primary_error: BaseException,
    recovery: Awaitable[None],
    message: str,
) -> None:
    try:
        cancellation = await _finish_recovery(recovery)
    except BaseException as rollback_error:
        raise BaseExceptionGroup(message, [primary_error, rollback_error]) from None
    if cancellation is not None:
        raise cancellation from primary_error
    raise primary_error
