from dataclasses import replace

from ntrp.config import Config
from ntrp.integrations.base import (
    Integration,
    IntegrationConnectionError,
    IntegrationConnectionSpec,
)
from ntrp.integrations.calendar import CALENDAR
from ntrp.integrations.gmail import GMAIL
from ntrp.integrations.registry import IntegrationRegistry
from ntrp.integrations.slack import SLACK


def _connection() -> IntegrationConnectionSpec:
    return IntegrationConnectionSpec(
        connection_id="example",
        capability="Read example records",
        action="settings",
        enabled=lambda config: config.google,
        configured=lambda config: bool(config.openai_api_key),
    )


def _integration(build) -> Integration:
    return Integration(
        id="example",
        label="Example",
        build=build,
        connection=_connection(),
    )


def test_registry_reports_connected_connection():
    registry = IntegrationRegistry([_integration(lambda _config: object())])
    registry.sync(Config(_env_file=None, memory=False, google=True, openai_api_key="configured"))

    descriptor = registry.get_connection("example")

    assert descriptor is not None
    assert descriptor.state == "connected"
    assert descriptor.connection_id == "example"
    assert descriptor.capability == "Read example records"


def test_registry_distinguishes_disabled_and_not_configured_connections():
    integration = _integration(lambda _config: None)
    registry = IntegrationRegistry([integration])

    registry.sync(Config(_env_file=None, memory=False, google=False, openai_api_key="configured"))
    disabled = registry.get_connection("example")
    assert disabled is not None
    assert disabled.state == "disabled"

    registry.sync(Config(_env_file=None, memory=False, google=True, openai_api_key=None))
    unconfigured = registry.get_connection("example")
    assert unconfigured is not None
    assert unconfigured.state == "not_configured"


def test_registry_preserves_typed_connection_build_failure():
    def build(_config):
        raise IntegrationConnectionError(
            integration_id="example",
            reason="auth_required",
            detail="Reconnect Example",
            retry_safe=True,
        )

    registry = IntegrationRegistry([_integration(build)])
    registry.sync(Config(_env_file=None, memory=False, google=True, openai_api_key="configured"))

    descriptor = registry.get_connection("example")

    assert descriptor is not None
    assert descriptor.state == "auth_required"
    assert descriptor.detail == "Reconnect Example"


def test_registry_reports_untyped_build_failure_as_degraded():
    def build(_config):
        raise RuntimeError("provider unavailable")

    registry = IntegrationRegistry([_integration(build)])
    registry.sync(Config(_env_file=None, memory=False, google=True, openai_api_key="configured"))

    descriptor = registry.get_connection("example")

    assert descriptor is not None
    assert descriptor.state == "degraded"
    assert descriptor.detail == "provider unavailable"


def test_registered_native_integrations_declare_connection_capabilities():
    assert GMAIL.connection is not None
    assert GMAIL.connection.connection_id == "google"
    assert GMAIL.connection.action == "oauth"

    assert CALENDAR.connection is not None
    assert CALENDAR.connection.connection_id == "google"
    assert CALENDAR.connection.action == "oauth"

    assert SLACK.connection is not None
    assert SLACK.connection.connection_id == "slack"
    assert SLACK.connection.action == "credentials"


def test_descriptor_lists_registered_tool_names():
    integration = replace(_integration(lambda _config: None), tools={})
    registry = IntegrationRegistry([integration])
    registry.sync(Config(_env_file=None, memory=False, google=True, openai_api_key="configured"))

    descriptor = registry.get_connection("example")

    assert descriptor is not None
    assert descriptor.tool_names == ()
