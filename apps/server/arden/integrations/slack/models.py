from dataclasses import dataclass
from datetime import datetime

from arden.core.content import ImageContent


@dataclass(frozen=True, slots=True)
class SlackThreadResult:
    text: str
    model_content: tuple[ImageContent, ...] = ()
    has_more: bool = False
    next_after_ref: str | None = None


@dataclass(frozen=True, slots=True)
class SlackFileResult:
    text: str
    model_content: tuple[ImageContent, ...]


@dataclass(frozen=True, slots=True)
class SlackMessage:
    ref: str
    channel_ref: str
    channel_name: str | None
    author_ref: str
    author_name: str
    text: str
    created_at: datetime
    thread_ref: str | None = None
    reply_count: int | None = None
    permalink: str | None = None


@dataclass(frozen=True, slots=True)
class SlackMessagePage:
    messages: tuple[SlackMessage, ...]
    next_page_ref: str | None


@dataclass(frozen=True, slots=True)
class SlackChannel:
    ref: str
    name: str


@dataclass(frozen=True, slots=True)
class SlackUser:
    ref: str
    name: str
    username: str | None
    email: str | None
    title: str | None


@dataclass(frozen=True, slots=True)
class SlackDirectMessage:
    channel_ref: str
    user_ref: str
    peer_name: str


@dataclass(frozen=True, slots=True)
class SlackUserProfile:
    ref: str
    name: str
    username: str
    email: str | None
    title: str | None
    status_text: str | None
    status_emoji: str | None
    timezone: str | None
    deleted: bool | None
    is_bot: bool | None
    is_stranger: bool | None


@dataclass(frozen=True, slots=True)
class SlackPostReceipt:
    channel_ref: str
    channel_name: str
    message_ref: str
    thread_ref: str


@dataclass(frozen=True, slots=True)
class SlackIdentity:
    user_ref: str
    user_name: str


@dataclass(frozen=True, slots=True)
class SlackAuthResult:
    team_name: str | None
    team_ref: str | None
    user_ref: str
    user_name: str
    bot_ref: str | None


@dataclass(frozen=True, slots=True)
class SlackHistoryMessage:
    timestamp: str
    text: str
    user_ref: str | None
    thread_timestamp: str | None
    subtype: str | None
    bot_ref: str | None


@dataclass(frozen=True, slots=True)
class SlackHistoryPage:
    messages: tuple[SlackHistoryMessage, ...]
    has_more: bool
    next_latest: str | None


@dataclass(frozen=True, slots=True)
class SlackImageFile:
    ref: str
    title: str
    mime_type: str
    size_bytes: int | None
    download_url: str


@dataclass(frozen=True, slots=True)
class SlackFileInfoRequired:
    """Slack Connect placeholder that must not be downloaded as a file."""

    ref: str


@dataclass(frozen=True, slots=True)
class SlackOtherFile:
    ref: str
    title: str
    mime_type: str
    size_bytes: int | None


type SlackThreadAttachment = SlackImageFile | SlackFileInfoRequired | SlackOtherFile


@dataclass(frozen=True, slots=True)
class SlackThreadMessage:
    timestamp: str
    text: str
    author_ref: str
    author_name: str
    attachments: tuple[SlackThreadAttachment, ...]
