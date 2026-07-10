from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from ntrp.core.content import ContentBlock

_PROVIDER_MAX_CHARS = 64
_KIND_MAX_CHARS = 64
_REF_MAX_CHARS = 2048
_TITLE_MAX_CHARS = 256
_URL_MAX_CHARS = 4096
_SOURCE_REFS_MAX = 50


@dataclass(frozen=True, slots=True)
class ToolSourceRef:
    provider: str
    kind: str
    ref: str
    title: str
    url: str | None = None

    def to_dict(self) -> dict[str, str]:
        data = {
            "provider": self.provider,
            "kind": self.kind,
            "ref": self.ref,
            "title": self.title,
        }
        if self.url is not None:
            data["url"] = self.url
        return data


def normalize_source_refs(
    refs: Iterable[ToolSourceRef | Mapping[str, object]] | None,
) -> tuple[ToolSourceRef, ...]:
    if refs is None or isinstance(refs, (str, bytes, Mapping)):
        return ()

    normalized: list[ToolSourceRef] = []
    seen: set[tuple[str, str]] = set()
    for raw in refs:
        if isinstance(raw, ToolSourceRef):
            values: Mapping[str, object] = raw.to_dict()
        elif isinstance(raw, Mapping):
            values = raw
        else:
            continue

        provider = _trimmed_string(values.get("provider"))
        kind = _trimmed_string(values.get("kind"))
        ref = _trimmed_string(values.get("ref"))
        title = _trimmed_string(values.get("title"))
        if (
            not provider
            or len(provider) > _PROVIDER_MAX_CHARS
            or not kind
            or len(kind) > _KIND_MAX_CHARS
            or not ref
            or len(ref) > _REF_MAX_CHARS
            or not title
        ):
            continue
        if _has_http_credentials(ref):
            continue
        if _has_http_credentials(title):
            title = ref

        identity = (provider, ref)
        if identity in seen:
            continue

        url = _safe_url(values.get("url"))
        normalized.append(
            ToolSourceRef(
                provider=provider,
                kind=kind,
                ref=ref,
                title=title[:_TITLE_MAX_CHARS],
                url=url,
            )
        )
        seen.add(identity)
        if len(normalized) == _SOURCE_REFS_MAX:
            break
    return tuple(normalized)


def _trimmed_string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) else None


def _safe_url(value: object) -> str | None:
    url = _trimmed_string(value)
    if not url or len(url) > _URL_MAX_CHARS:
        return None
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
    except ValueError:
        return None
    return url


def _has_http_credentials(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return parsed.scheme.lower() in {"http", "https"} and (
            parsed.username is not None or parsed.password is not None
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class ToolResult:
    content: str
    preview: str
    is_error: bool = False
    data: dict | None = None
    model_content: tuple[ContentBlock, ...] = ()
    source_refs: tuple[ToolSourceRef, ...] = ()

    @staticmethod
    def error(message: str) -> "ToolResult":
        return ToolResult(content=message, preview="Error", is_error=True)


@dataclass(frozen=True)
class ToolMeta:
    name: str
    display_name: str
    kind: str = "tool"
    # Additive UI rendering hints — semantic, library-agnostic. The desktop app
    # maps `icon` to a glyph and pluralizes grouped runs with `noun`; `source`
    # is the integration category. All optional; absent for uncategorized tools.
    icon: str | None = None
    noun: str | None = None
    source: str | None = None
