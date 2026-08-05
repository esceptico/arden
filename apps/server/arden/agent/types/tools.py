import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from arden.agent.types.tool_outcomes import (
    ToolEffect,
    ToolError,
    ToolOutcome,
    ToolOutcomePayloadError,
    ToolOutcomeStatus,
    ToolVerification,
    tool_result_content_for_model,
)
from arden.core.content import (
    AudioContent,
    ContentBlock,
    ContentBlockError,
    ContextContent,
    ImageContent,
    TextContent,
    parse_block,
)

__all__ = [
    "ToolEffect",
    "ToolError",
    "ToolMeta",
    "ToolOutcome",
    "ToolOutcomePayloadError",
    "ToolOutcomeStatus",
    "ToolResult",
    "ToolResultPayloadError",
    "ToolSourceRef",
    "ToolSourceRefError",
    "ToolVerification",
    "canonical_source_title",
    "decode_source_refs",
    "normalize_source_refs",
    "tool_result_content_for_model",
    "validate_source_provider",
]

_PROVIDER_MAX_CHARS = 64
_KIND_MAX_CHARS = 64
_TOOL_RESULT_ENVELOPE_KEY = "_arden_envelope"
_TOOL_RESULT_ENVELOPE_VERSION = "tool_result.v1"
_REF_MAX_CHARS = 2048
_TITLE_MAX_CHARS = 256
_URL_MAX_CHARS = 4096
_SOURCE_REFS_MAX = 50
_JSON_MAX_DEPTH = 64
_HTTP_URL_TOKEN = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_CONTENT_BLOCK_TYPES = (TextContent, ImageContent, AudioContent, ContextContent)


class ToolResultPayloadError(ValueError):
    """A marked durable ToolResult envelope was malformed."""


class ToolSourceRefError(ValueError):
    """A source reference violated the provider-neutral wire contract."""


@dataclass(frozen=True, slots=True)
class ToolSourceRef:
    provider: str
    kind: str
    ref: str
    title: str
    url: str | None = None

    def __post_init__(self) -> None:
        _validate_source_ref_text("provider", self.provider, _PROVIDER_MAX_CHARS)
        _validate_source_ref_text("kind", self.kind, _KIND_MAX_CHARS)
        _validate_source_ref_text("ref", self.ref, _REF_MAX_CHARS)
        _validate_source_ref_text("title", self.title, _TITLE_MAX_CHARS)
        if _contains_http_credentials(self.ref):
            raise ToolSourceRefError("ref must not contain HTTP credentials")
        if _contains_http_credentials(self.title):
            raise ToolSourceRefError("title must not contain HTTP credentials")
        if self.url is not None:
            _validate_source_ref_text("url", self.url, _URL_MAX_CHARS)
            _validate_source_ref_url(self.url)

    @classmethod
    def decode(cls, value: object) -> "ToolSourceRef":
        """Decode one untrusted wire/persistence mapping without coercion."""
        if not isinstance(value, Mapping):
            raise ToolSourceRefError("source reference must be an object")
        if not all(isinstance(key, str) for key in value):
            raise ToolSourceRefError("source reference field names must be strings")
        keys = {key for key in value if isinstance(key, str)}
        required = {"provider", "kind", "ref", "title"}
        allowed = required | {"url"}
        missing = required - keys
        extra = keys - allowed
        if missing:
            raise ToolSourceRefError(f"source reference is missing: {', '.join(sorted(missing))}")
        if extra:
            raise ToolSourceRefError(f"source reference has unknown fields: {', '.join(sorted(extra))}")
        url = value.get("url")
        if url is not None and not isinstance(url, str):
            raise ToolSourceRefError("url must be a string")
        return cls(
            provider=_source_ref_string(value, "provider"),
            kind=_source_ref_string(value, "kind"),
            ref=_source_ref_string(value, "ref"),
            title=_source_ref_string(value, "title"),
            url=url,
        )

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


def normalize_source_refs(refs: Iterable[ToolSourceRef]) -> tuple[ToolSourceRef, ...]:
    """Deduplicate a typed source stream while preserving first-seen order."""
    if not isinstance(refs, Iterable) or isinstance(refs, (str, bytes, Mapping)):
        raise ToolSourceRefError("source references must be an iterable of ToolSourceRef values")
    normalized: list[ToolSourceRef] = []
    seen: set[tuple[str, str]] = set()
    for index, source in enumerate(refs):
        if index >= _SOURCE_REFS_MAX:
            raise ToolSourceRefError(f"source references exceed {_SOURCE_REFS_MAX} items")
        if not isinstance(source, ToolSourceRef):
            raise ToolSourceRefError("source references must contain only ToolSourceRef values")
        identity = (source.provider, source.ref)
        if identity in seen:
            continue
        seen.add(identity)
        normalized.append(source)
    return tuple(normalized)


def decode_source_refs(value: object, *, max_items: int | None = _SOURCE_REFS_MAX) -> tuple[ToolSourceRef, ...]:
    """Strictly decode a JSON source-reference array, bounded per tool by default."""
    if not isinstance(value, list):
        raise ToolSourceRefError("source references must be a JSON array")
    if max_items is not None and len(value) > max_items:
        raise ToolSourceRefError(f"source references exceed {max_items} items")
    decoded = tuple(ToolSourceRef.decode(item) for item in value)
    if max_items is None:
        return decoded
    return normalize_source_refs(decoded)


def canonical_source_title(value: str | None, *, fallback: str) -> str:
    """Project an unbounded display label into the source-reference contract."""
    if value is not None and not isinstance(value, str):
        raise ToolSourceRefError("source title must be a string")
    if not isinstance(fallback, str):
        raise ToolSourceRefError("source title fallback must be a string")

    def project(candidate: str) -> str:
        single_line = " ".join(candidate.split())
        redacted = _HTTP_URL_TOKEN.sub(
            lambda match: "[redacted URL]" if _url_has_credentials(match.group(0)) else match.group(0),
            single_line,
        )
        return redacted[:_TITLE_MAX_CHARS].rstrip()

    return project(value or "") or project(fallback) or "source"


def validate_source_provider(value: str) -> str:
    _validate_source_ref_text("provider", value, _PROVIDER_MAX_CHARS)
    return value


def _source_ref_string(value: Mapping[str, object], field: str) -> str:
    field_value = value[field]
    if not isinstance(field_value, str):
        raise ToolSourceRefError(f"{field} must be a string")
    return field_value


def _validate_source_ref_text(field: str, value: object, max_chars: int) -> None:
    if not isinstance(value, str):
        raise ToolSourceRefError(f"{field} must be a string")
    if not value:
        raise ToolSourceRefError(f"{field} must not be empty")
    if value != value.strip():
        raise ToolSourceRefError(f"{field} must not have surrounding whitespace")
    if any(
        (character.isspace() and character != " ") or ord(character) < 32 or ord(character) == 127
        for character in value
    ):
        raise ToolSourceRefError(f"{field} must be one line without control whitespace")
    if len(value) > max_chars:
        raise ToolSourceRefError(f"{field} exceeds {max_chars} characters")


def _validate_source_ref_url(url: str) -> None:
    if any(character.isspace() for character in url) or "\\" in url:
        raise ToolSourceRefError("url must be a valid HTTP(S) URL")
    try:
        parsed = urlsplit(url)
        _port = parsed.port
    except ValueError as exc:
        raise ToolSourceRefError("url must be a valid HTTP(S) URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ToolSourceRefError("url must be an HTTP(S) URL without credentials")


def _url_has_credentials(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return parsed.scheme.lower() in {"http", "https"} and (
            parsed.username is not None or parsed.password is not None
        )
    except ValueError:
        return False


def _contains_http_credentials(value: str) -> bool:
    return any(_url_has_credentials(match.group(0)) for match in _HTTP_URL_TOKEN.finditer(value))


@dataclass(frozen=True)
class ToolResult:
    content: str
    preview: str
    is_error: bool = False
    data: dict[str, object] | None = None
    model_content: tuple[ContentBlock, ...] = ()
    source_refs: tuple[ToolSourceRef, ...] = ()
    outcome: ToolOutcome | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise TypeError("tool result content must be a string")
        if not isinstance(self.preview, str):
            raise TypeError("tool result preview must be a string")
        if not isinstance(self.is_error, bool):
            raise TypeError("tool result is_error must be a boolean")
        if self.data is not None:
            if not isinstance(self.data, dict):
                raise TypeError("tool result data must be an object")
            _validate_json_value(self.data, path="data")
        for block in self.model_content:
            if not isinstance(block, _CONTENT_BLOCK_TYPES):
                raise TypeError("tool result model_content must contain typed content blocks")
        object.__setattr__(self, "source_refs", normalize_source_refs(self.source_refs))
        if self.outcome is None:
            return
        if not isinstance(self.outcome, ToolOutcome):
            raise TypeError("tool result outcome must be a ToolOutcome")
        succeeded = self.outcome.status == ToolOutcomeStatus.SUCCEEDED
        if succeeded and self.is_error:
            raise ValueError("ToolResult cannot pair is_error=True with a successful outcome")
        if not succeeded and not self.is_error:
            raise ValueError("ToolResult cannot pair is_error=False with a failed outcome")

    def with_default_outcome(self) -> "ToolResult":
        if self.outcome is not None:
            return self
        if self.is_error:
            outcome = ToolOutcome(
                status=ToolOutcomeStatus.FAILED,
                error=ToolError(code="tool_error"),
            )
        else:
            outcome = ToolOutcome(status=ToolOutcomeStatus.SUCCEEDED)
        return replace(self, outcome=outcome)

    def serialized_payload(self) -> str:
        payload = {
            _TOOL_RESULT_ENVELOPE_KEY: _TOOL_RESULT_ENVELOPE_VERSION,
            "content": self.content,
            "preview": self.preview,
            "is_error": self.is_error,
            "data": self.data,
            "model_content": [block.model_dump(mode="json") for block in self.model_content],
            "source_refs": [ref.to_dict() for ref in self.source_refs],
            "outcome": self.outcome.to_dict() if self.outcome else None,
        }
        try:
            return json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ToolResultPayloadError(f"tool result is not JSON-safe: {exc}") from exc

    @classmethod
    def from_serialized_payload(cls, value: str) -> "ToolResult | None":
        """Decode the exact durable envelope emitted by ``serialized_payload``.

        Ordinary result text, including envelope-shaped JSON, deliberately
        returns ``None`` unless it carries Arden's explicit version marker.
        """
        if not isinstance(value, str):
            return None
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return None
        except RecursionError as exc:
            raise ToolResultPayloadError("tool result envelope exceeds maximum nesting depth") from exc
        if not isinstance(payload, dict) or _TOOL_RESULT_ENVELOPE_KEY not in payload:
            return None
        if payload[_TOOL_RESULT_ENVELOPE_KEY] != _TOOL_RESULT_ENVELOPE_VERSION:
            raise ToolResultPayloadError("unsupported tool result envelope version")
        required = {
            _TOOL_RESULT_ENVELOPE_KEY,
            "content",
            "preview",
            "is_error",
            "data",
            "model_content",
            "source_refs",
            "outcome",
        }
        missing = required - set(payload)
        unknown = set(payload) - required
        if missing:
            raise ToolResultPayloadError(f"tool result envelope is missing: {', '.join(sorted(missing))}")
        if unknown:
            raise ToolResultPayloadError(f"tool result envelope has unknown fields: {', '.join(sorted(unknown))}")
        if not isinstance(payload["content"], str):
            raise ToolResultPayloadError("tool result content must be a string")
        if not isinstance(payload["preview"], str):
            raise ToolResultPayloadError("tool result preview must be a string")
        if not isinstance(payload["is_error"], bool):
            raise ToolResultPayloadError("tool result is_error must be a boolean")
        raw_data = payload["data"]
        if raw_data is not None and not isinstance(raw_data, dict):
            raise ToolResultPayloadError("tool result data must be an object or null")
        raw_model_content = payload["model_content"]
        raw_source_refs = payload["source_refs"]
        raw_outcome = payload["outcome"]
        if not isinstance(raw_model_content, list):
            raise ToolResultPayloadError("tool result model_content must be an array")
        if not isinstance(raw_source_refs, list):
            raise ToolResultPayloadError("tool result source_refs must be an array")
        if raw_outcome is not None and not isinstance(raw_outcome, dict):
            raise ToolResultPayloadError("tool result outcome must be an object or null")
        try:
            model_content = tuple(parse_block(block) for block in raw_model_content)
            outcome = ToolOutcome.decode(raw_outcome) if raw_outcome is not None else None
            return cls(
                content=payload["content"],
                preview=payload["preview"],
                is_error=payload["is_error"],
                data=raw_data,
                model_content=model_content,
                source_refs=decode_source_refs(raw_source_refs),
                outcome=outcome,
            )
        except (ContentBlockError, ToolOutcomePayloadError, ToolSourceRefError, TypeError, ValueError) as exc:
            raise ToolResultPayloadError(f"invalid tool result envelope: {exc}") from exc

    @staticmethod
    def failure(
        *,
        code: str,
        message: str,
        preview: str,
        status: ToolOutcomeStatus = ToolOutcomeStatus.FAILED,
        retryable: bool = False,
        recovery_action: str | None = None,
        diagnostic_ref: str | None = None,
        data: dict[str, object] | None = None,
    ) -> "ToolResult":
        return ToolResult(
            content=message,
            preview=preview,
            is_error=True,
            data=data,
            outcome=ToolOutcome(
                status=status,
                error=ToolError(
                    code=code,
                    retryable=retryable,
                    recovery_action=recovery_action,
                    diagnostic_ref=diagnostic_ref,
                ),
            ),
        )


def _validate_json_value(
    value: object,
    *,
    path: str,
    depth: int = 0,
    active_containers: set[int] | None = None,
) -> None:
    if depth > _JSON_MAX_DEPTH:
        raise ValueError(f"{path} exceeds maximum nesting depth")
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if type(value) is list:
        active_containers = active_containers if active_containers is not None else set()
        container_id = id(value)
        if container_id in active_containers:
            raise ValueError(f"{path} contains a cycle")
        active_containers.add(container_id)
        try:
            for index, item in enumerate(value):
                _validate_json_value(
                    item,
                    path=f"{path}[{index}]",
                    depth=depth + 1,
                    active_containers=active_containers,
                )
        finally:
            active_containers.remove(container_id)
        return
    if type(value) is dict:
        active_containers = active_containers if active_containers is not None else set()
        container_id = id(value)
        if container_id in active_containers:
            raise ValueError(f"{path} contains a cycle")
        active_containers.add(container_id)
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError(f"{path} contains a non-string key")
                _validate_json_value(
                    item,
                    path=f"{path}.{key}",
                    depth=depth + 1,
                    active_containers=active_containers,
                )
        finally:
            active_containers.remove(container_id)
        return
    raise TypeError(f"{path} contains unsupported {type(value).__name__}")


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
    changes_state: bool = False
    concurrency_group: str | None = None
    ends_turn: bool = False
