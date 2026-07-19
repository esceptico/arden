from ntrp.config import Config
from ntrp.integrations.base import (
    Integration,
    IntegrationConnectionDescriptor,
    IntegrationConnectionError,
    IntegrationHealth,
    ToolProviderStatus,
)
from ntrp.logging import get_logger
from ntrp.tools.core import Tool

_logger = get_logger(__name__)


class IntegrationRegistry:
    def __init__(self, integrations: list[Integration]):
        self._integrations: dict[str, Integration] = {i.id: i for i in integrations}
        self._clients: dict[str, object] = {}
        self._errors: dict[str, str] = {}
        self._connection_errors: dict[str, IntegrationConnectionError] = {}
        self._config: Config | None = None

    def sync(self, config: Config) -> None:
        self._config = config
        for id, integration in self._integrations.items():
            if integration.build is None:
                continue
            try:
                client = integration.build(config)
            except Exception as e:
                _logger.exception("Integration %r build failed", id)
                self._clients.pop(id, None)
                self._errors[id] = str(e)
                if isinstance(e, IntegrationConnectionError):
                    self._connection_errors[id] = e
                else:
                    self._connection_errors.pop(id, None)
                continue
            self._errors.pop(id, None)
            self._connection_errors.pop(id, None)
            if client is None:
                self._clients.pop(id, None)
            else:
                self._clients[id] = client

    @property
    def integrations(self) -> dict[str, Integration]:
        return dict(self._integrations)

    @property
    def clients(self) -> dict[str, object]:
        return dict(self._clients)

    @property
    def errors(self) -> dict[str, str]:
        return dict(self._errors)

    def get_client(self, id: str) -> object | None:
        return self._clients.get(id)

    def get_integration(self, id: str) -> Integration | None:
        return self._integrations.get(id)

    def get_connection(self, id: str) -> IntegrationConnectionDescriptor | None:
        integration = self._integrations.get(id)
        spec = integration.connection if integration else None
        if integration is None or spec is None:
            return None

        detail: str | None = None
        required_scopes = spec.required_scopes
        if id in self._clients:
            state = "connected"
        elif error := self._connection_errors.get(id):
            state = error.reason
            detail = error.detail
            required_scopes = error.required_scopes or required_scopes
        elif id in self._errors:
            state = "degraded"
            detail = self._errors[id]
        elif self._config is not None and spec.configured is not None and not spec.configured(self._config):
            state = "not_configured"
        elif self._config is not None and spec.enabled is not None and not spec.enabled(self._config):
            state = "disabled"
        else:
            state = "not_configured" if spec.configured is None else "degraded"

        return IntegrationConnectionDescriptor(
            integration_id=id,
            connection_id=spec.connection_id,
            label=integration.label,
            capability=spec.capability,
            action="enable" if state == "disabled" else spec.action,
            settings_tab=spec.settings_tab,
            state=state,
            detail=detail,
            required_scopes=required_scopes,
            tool_names=tuple(sorted(integration.tools)),
        )

    def list_connections(self) -> list[IntegrationConnectionDescriptor]:
        return [
            descriptor
            for id in self._integrations
            if not id.startswith("_") and (descriptor := self.get_connection(id)) is not None
        ]

    def active_tools(self) -> list[Tool]:
        """Tools from integrations whose client built successfully, or which have no build."""
        out: list[Tool] = []
        for id, integration in self._integrations.items():
            if integration.build is None or id in self._clients:
                out.extend(integration.tools.values())
        return out

    def notifier_classes(self) -> dict[str, type]:
        return {i.id: i.notifier_class for i in self._integrations.values() if i.notifier_class is not None}

    def service_fields(self) -> dict[str, list]:
        return {i.id: list(i.service_fields) for i in self._integrations.values() if i.service_fields}

    def list_providers(self) -> list[ToolProviderStatus]:
        out: list[ToolProviderStatus] = []
        for id, integration in self._integrations.items():
            if id.startswith("_") or integration.build is None:
                continue
            if id in self._clients:
                health = IntegrationHealth(status="connected")
            elif id in self._errors:
                health = IntegrationHealth(status="error", detail=self._errors[id])
            else:
                health = IntegrationHealth(status="not_configured")
            out.append(
                ToolProviderStatus(
                    id=id,
                    label=integration.label,
                    kind="native",
                    health=health,
                    tool_count=len(integration.tools),
                )
            )
        return out
