from arden.config import Config
from arden.integrations.base import (
    Integration,
    IntegrationConnectionDescriptor,
    IntegrationConnectionError,
    IntegrationHealth,
    ToolProviderStatus,
)


class IntegrationRegistry:
    def __init__(self, integrations: list[Integration]):
        self._integrations: dict[str, Integration] = {i.id: i for i in integrations}
        self._clients: dict[str, object] = {}
        self._errors: dict[str, str] = {}
        self._connection_errors: dict[str, IntegrationConnectionError] = {}
        self._config: Config | None = None

    def sync(self, config: Config) -> None:
        clients: dict[str, object] = {}
        errors: dict[str, str] = {}
        connection_errors: dict[str, IntegrationConnectionError] = {}
        for id, integration in self._integrations.items():
            if integration.build is None:
                continue
            try:
                client = integration.build(config)
            except IntegrationConnectionError as error:
                errors[id] = error.detail
                connection_errors[id] = error
                continue
            if client is not None:
                clients[id] = client

        self._config = config
        self._clients = clients
        self._errors = errors
        self._connection_errors = connection_errors

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
