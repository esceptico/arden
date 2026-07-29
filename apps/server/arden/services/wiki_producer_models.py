"""Lightweight contracts for wiki producer provisioning."""

from dataclasses import dataclass


class WikiProducerProvisionConflictError(ValueError):
    """Existing state belongs to a different producer contract."""


class WikiProducerPartialProvisionError(RuntimeError):
    """The page exists, but the matching automation was not provisioned."""

    def __init__(self, page_id: str, automation_id: str):
        super().__init__(f"wiki page {page_id!r} exists but automation {automation_id!r} was not created")
        self.page_id = page_id
        self.automation_id = automation_id


@dataclass(frozen=True, slots=True)
class WikiProducerRequest:
    page_id: str
    path: str
    title: str
    aliases: tuple[str, ...]
    automation_name: str
    prompt: str
    model: str | None
    trigger_type: str
    at: str | None
    days: str | None
    every: str | None
    start: str | None
    end: str | None
    event_type: str | None
    lead_minutes: int | str | None
    channels: tuple[str, ...] | None
    from_user: str | None
    contains: tuple[str, ...] | None
    source_tool_scope: tuple[str, ...]
    expected_head: str | None


@dataclass(frozen=True, slots=True)
class WikiProducerProvision:
    page_id: str
    path: str
    title: str
    aliases: tuple[str, ...]
    page_version: str
    head: str
    automation_id: str
    automation_name: str
    model: str | None
    auto_approve: bool
    tool_scope: tuple[str, ...]
    channel_id: str
    page_created: bool
    channel_created: bool
    automation_created: bool
