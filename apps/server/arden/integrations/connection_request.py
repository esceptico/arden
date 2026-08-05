from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from arden.integrations.base import (
    ConnectionAction,
    ConnectionState,
    IntegrationConnectionDescriptor,
)


class IntegrationConnectionRequestEnvelope(BaseModel):
    """Canonical durable payload for one suspended integration connection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: Literal["connection_request"] = "connection_request"
    scope: Literal["external"] = "external"
    integration_id: str = Field(min_length=1)
    connection_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    reason: ConnectionState
    detail: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    action: ConnectionAction
    settings_tab: str = Field(min_length=1)
    required_scopes: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()
    source: Literal["recovery", "suggestion"]
    account_ref: str | None = Field(default=None, min_length=1, max_length=320)

    @classmethod
    def from_descriptor(
        cls,
        descriptor: IntegrationConnectionDescriptor,
        *,
        source: Literal["recovery", "suggestion"],
        detail: str,
    ) -> "IntegrationConnectionRequestEnvelope":
        return cls(
            integration_id=descriptor.integration_id,
            connection_id=descriptor.connection_id,
            label=descriptor.label,
            reason=descriptor.state,
            detail=detail,
            capability=descriptor.capability,
            action=descriptor.action,
            settings_tab=descriptor.settings_tab,
            required_scopes=descriptor.required_scopes,
            tool_names=descriptor.tool_names,
            source=source,
            account_ref=descriptor.account_ref,
        )

    def to_descriptor(self) -> IntegrationConnectionDescriptor:
        return IntegrationConnectionDescriptor(
            integration_id=self.integration_id,
            connection_id=self.connection_id,
            label=self.label,
            capability=self.capability,
            action=self.action,
            settings_tab=self.settings_tab,
            state=self.reason,
            detail=self.detail,
            required_scopes=self.required_scopes,
            tool_names=self.tool_names,
            account_ref=self.account_ref,
        )

    def snapshot_fields(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"tool_name", "scope", "tool_names"},
            exclude_none=True,
        )
