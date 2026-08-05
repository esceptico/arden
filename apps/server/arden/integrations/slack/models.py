from dataclasses import dataclass
from datetime import datetime

from arden.core.content import ImageContent


@dataclass(frozen=True)
class SlackThreadResult:
    text: str
    model_content: tuple[ImageContent, ...] = ()


@dataclass(frozen=True)
class SlackMessage:
    ref: str
    channel_ref: str
    channel_name: str | None
    author_ref: str | None
    author_name: str
    text: str
    created_at: datetime
    thread_ref: str | None = None
    reply_count: int | None = None
    permalink: str | None = None


@dataclass(frozen=True)
class SlackChannel:
    ref: str
    name: str


@dataclass(frozen=True)
class SlackUser:
    ref: str
    name: str
    username: str
    email: str | None
    title: str | None


@dataclass(frozen=True)
class SlackDirectMessage:
    channel_ref: str
    user_ref: str
    peer_name: str


@dataclass(frozen=True)
class SlackUserProfile:
    ref: str
    name: str
    username: str
    email: str | None
    title: str | None
    status_text: str | None
    status_emoji: str | None
    timezone: str | None


@dataclass(frozen=True)
class SlackPostReceipt:
    channel_ref: str
    channel_name: str
    message_ts: str
    thread_ts: str


@dataclass(frozen=True)
class SlackIdentity:
    user_ref: str
    user_name: str


@dataclass(frozen=True)
class SlackAuthResult:
    team_name: str | None
    team_ref: str | None
    user_ref: str
    user_name: str
    bot_ref: str | None


@dataclass(frozen=True)
class SlackHistoryMessage:
    timestamp: str
    text: str
    user_ref: str | None
    thread_timestamp: str | None
    subtype: str | None
    bot_ref: str | None


@dataclass(frozen=True)
class SlackImageFile:
    ref: str
    title: str
    mime_type: str
    size_bytes: int | None
    download_url: str


@dataclass(frozen=True)
class SlackThreadMessage:
    timestamp: str
    text: str
    author_ref: str | None
    author_name: str
    images: tuple[SlackImageFile, ...]
