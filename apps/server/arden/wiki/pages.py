"""Validated common Markdown page format for the wiki.

This module deliberately knows no page categories or producers.  It only owns
the small, visible page contract and returns new values for every edit.
"""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Self
from uuid import uuid4

import yaml


class PageValidationError(ValueError):
    """A Markdown page does not satisfy the common wiki-page contract."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe loader that refuses YAML's otherwise silent duplicate keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise PageValidationError("frontmatter keys must be strings")
        if key in result:
            raise PageValidationError(f"duplicate frontmatter key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)

_OPEN = b"---\n"
_CLOSE = b"\n---\n"
_GENERATED_TOKEN = re.compile(r"<!--\s*/?\s*generated\s*-->")
_GENERATED_OPEN = b"<!-- generated -->"
_GENERATED_CLOSE = b"<!-- /generated -->"
_RESERVED = frozenset({"page_id", "title", "aliases", "lifecycle", "redirect_to"})
_OWN_REVISION_FIELDS = frozenset(
    {
        "content_hash",
        "content_sha256",
        "content_revision",
        "current_hash",
        "current_revision",
        "hash",
        "page_hash",
        "page_revision",
        "revision",
        "revision_id",
        "sha256",
    }
)


def _json_value(value: object, seen: set[int] | None = None) -> None:
    """Reject YAML values which cannot be faithfully represented as JSON."""

    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise PageValidationError("frontmatter floats must be finite")

    seen = seen if seen is not None else set()
    marker = id(value)
    if marker in seen:
        raise PageValidationError("frontmatter must not contain YAML aliases or cycles")
    if isinstance(value, (list, tuple)):
        seen.add(marker)
        try:
            for item in value:
                _json_value(item, seen)
        finally:
            seen.remove(marker)
        return
    if isinstance(value, Mapping):
        seen.add(marker)
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise PageValidationError("frontmatter keys must be strings")
                _json_value(item, seen)
        finally:
            seen.remove(marker)
        return
    raise PageValidationError(f"frontmatter value is not JSON-like: {type(value).__name__}")


def _freeze_json(value: object) -> object:
    """Recursively detach and freeze an already validated JSON-like value."""

    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: object) -> object:
    """Return plain dict/list values suitable for safe YAML serialization."""

    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _validate_generated_boundaries(body: bytes) -> None:
    text = body.decode("utf-8", errors="strict")
    starts: list[int] = []
    ends: list[int] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not _GENERATED_TOKEN.search(line):
            continue
        if line == "<!-- generated -->":
            starts.append(line_number)
        elif line == "<!-- /generated -->":
            ends.append(line_number)
        else:
            raise PageValidationError("generated boundaries must occupy their own canonical line")
    if not starts and not ends:
        return
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise PageValidationError("generated boundaries must contain one ordered opening and closing marker")


def _generated_region_bounds(body: bytes) -> tuple[int, int] | None:
    """Return the byte range between a validated generated marker pair."""

    opening_end: int | None = None
    closing_start: int | None = None
    offset = 0
    for line in body.splitlines(keepends=True):
        marker = line.rstrip(b"\r\n")
        if marker == _GENERATED_OPEN:
            opening_end = offset + len(line)
        elif marker == _GENERATED_CLOSE:
            closing_start = offset
        offset += len(line)
    if opening_end is None and closing_start is None:
        return None
    if opening_end is None or closing_start is None or opening_end > closing_start:
        raise PageValidationError("generated boundaries must contain one ordered opening and closing marker")
    return opening_end, closing_start


def _validate_generated_content(generated: bytes) -> None:
    if not isinstance(generated, bytes):
        raise TypeError("generated must be bytes")
    try:
        text = generated.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PageValidationError("generated content must be valid UTF-8") from exc
    if _GENERATED_TOKEN.search(text):
        raise PageValidationError("generated content must not contain generated markers")
    if generated and not generated.endswith(b"\n"):
        raise PageValidationError("generated content must be empty or newline-terminated")


def _required_string(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PageValidationError(f"{key} must be a nonempty string")
    return value


def _aliases(data: Mapping[str, object], title: str) -> tuple[str, ...]:
    value = data.get("aliases")
    if not isinstance(value, list):
        raise PageValidationError("aliases must be a list of nonempty strings")
    aliases: list[str] = []
    seen = {title.strip().casefold()}
    for alias in value:
        if not isinstance(alias, str) or not alias.strip():
            raise PageValidationError("aliases must be a list of nonempty strings")
        normalized = alias.strip().casefold()
        if normalized in seen:
            raise PageValidationError("title and aliases must not duplicate one another")
        seen.add(normalized)
        aliases.append(alias)
    return tuple(aliases)


@dataclass(frozen=True, slots=True)
class WikiPage:
    """An immutable parsed wiki page. ``body`` is preserved byte-for-byte."""

    page_id: str
    title: str
    aliases: tuple[str, ...]
    lifecycle: str
    redirect_to: str | None
    metadata: Mapping[str, object]
    body: bytes

    def with_title(self, title: str) -> Self:
        """Return a copy with a new title, without changing the body or identity."""

        return _validated_page(
            page_id=self.page_id,
            title=title,
            aliases=self.aliases,
            lifecycle=self.lifecycle,
            redirect_to=self.redirect_to,
            metadata=self.metadata,
            body=self.body,
        )

    def with_metadata(self, updates: Mapping[str, object]) -> Self:
        """Return a copy with unknown frontmatter fields merged immutably."""

        replacement = dict(self.metadata)
        for key, value in updates.items():
            if key in _RESERVED:
                raise PageValidationError(f"{key} has a dedicated page field")
            replacement[key] = value
        return _validated_page(
            page_id=self.page_id,
            title=self.title,
            aliases=self.aliases,
            lifecycle=self.lifecycle,
            redirect_to=self.redirect_to,
            metadata=replacement,
            body=self.body,
        )

    def with_body(self, body: bytes) -> Self:
        """Return a copy with validated replacement body bytes."""

        if not isinstance(body, bytes):
            raise TypeError("body must be bytes")
        return _validated_page(
            page_id=self.page_id,
            title=self.title,
            aliases=self.aliases,
            lifecycle=self.lifecycle,
            redirect_to=self.redirect_to,
            metadata=self.metadata,
            body=body,
        )

    def to_bytes(self) -> bytes:
        """Emit canonical frontmatter while retaining the exact existing body bytes."""

        data: dict[str, object] = {
            "page_id": self.page_id,
            "title": self.title,
            "aliases": list(self.aliases),
            "lifecycle": self.lifecycle,
        }
        if self.redirect_to is not None:
            data["redirect_to"] = self.redirect_to
        data.update({key: _thaw_json(value) for key, value in self.metadata.items()})
        encoded = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode("utf-8")
        return _OPEN + encoded + b"---\n" + self.body


def _validated_page(
    *,
    page_id: object,
    title: object,
    aliases: Sequence[object],
    lifecycle: object,
    redirect_to: object,
    metadata: Mapping[str, object],
    body: bytes,
) -> WikiPage:
    data: dict[str, object] = {
        "page_id": page_id,
        "title": title,
        "aliases": list(aliases),
        "lifecycle": lifecycle,
    }
    if redirect_to is not None:
        data["redirect_to"] = redirect_to
    if _RESERVED.intersection(metadata):
        duplicate = sorted(_RESERVED.intersection(metadata))[0]
        raise PageValidationError(f"{duplicate} has a dedicated page field")
    data.update(metadata)
    return _page_from_data(data, body)


def _page_from_data(data: Mapping[str, object], body: bytes, *, expected_page_id: str | None = None) -> WikiPage:
    _json_value(dict(data))
    forbidden = _OWN_REVISION_FIELDS.intersection(data)
    if forbidden:
        raise PageValidationError(f"page content identity belongs in revision history: {sorted(forbidden)[0]}")

    page_id = _required_string(data, "page_id")
    if expected_page_id is not None and page_id != expected_page_id:
        raise PageValidationError("page_id does not match the expected resource identity")
    title = _required_string(data, "title")
    aliases = _aliases(data, title)
    lifecycle = data.get("lifecycle")
    if lifecycle not in {"active", "redirect"}:
        raise PageValidationError("visible page lifecycle must be active or redirect")
    redirect_to = data.get("redirect_to")
    if lifecycle == "redirect":
        if not isinstance(redirect_to, str) or not redirect_to.strip():
            raise PageValidationError("redirect pages require a nonempty redirect_to")
    elif redirect_to is not None:
        raise PageValidationError("active pages must not declare redirect_to")

    _validate_generated_boundaries(body)
    metadata = {key: _freeze_json(value) for key, value in data.items() if key not in _RESERVED}
    return WikiPage(
        page_id=page_id,
        title=title,
        aliases=aliases,
        lifecycle=lifecycle,
        redirect_to=redirect_to if isinstance(redirect_to, str) else None,
        metadata=MappingProxyType(metadata),
        body=body,
    )


_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def page_excerpt(body: bytes, limit: int = 240) -> str:
    """First prose block of a page body, for list rows and link previews.

    Generated pages open with `<!-- generated -->` glued to the title heading,
    so comments are stripped before a block is judged; otherwise the first
    block reads as neither empty nor a heading and the excerpt is blank.
    """

    text = body.decode("utf-8", errors="replace")
    for raw in re.split(r"\n\s*\n", text):
        block = _COMMENT_RE.sub("", raw).strip()
        if not block or block.startswith("#"):
            continue
        flat = " ".join(block.split())
        return flat if len(flat) <= limit else f"{flat[:limit].rstrip()}…"
    return ""


def parse_page(content: bytes, *, expected_page_id: str | None = None) -> WikiPage:
    """Parse and validate a visible wiki Markdown page."""

    if not isinstance(content, bytes):
        raise TypeError("content must be bytes")
    if not content.startswith(_OPEN):
        raise PageValidationError("page must begin with a YAML frontmatter boundary")
    close = content.find(_CLOSE, len(_OPEN))
    if close < 0:
        raise PageValidationError("page frontmatter is missing its closing boundary")
    frontmatter = content[len(_OPEN) : close]
    body = content[close + len(_CLOSE) :]
    try:
        decoded = frontmatter.decode("utf-8", errors="strict")
        loaded = yaml.load(decoded, Loader=_UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError, PageValidationError) as exc:
        raise PageValidationError("invalid YAML frontmatter") from exc
    if not isinstance(loaded, dict):
        raise PageValidationError("frontmatter must be a mapping")
    try:
        body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PageValidationError("page body must be valid UTF-8") from exc
    return _page_from_data(loaded, body, expected_page_id=expected_page_id)


def create_page(
    *,
    title: str,
    body: bytes = b"",
    page_id: str | None = None,
    aliases: Sequence[str] = (),
    lifecycle: str = "active",
    redirect_to: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> WikiPage:
    """Create a validated page with a generated stable identity when omitted."""

    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    return _validated_page(
        page_id=page_id or str(uuid4()),
        title=title,
        aliases=aliases,
        lifecycle=lifecycle,
        redirect_to=redirect_to,
        metadata=metadata or {},
        body=body,
    )


def update_page_title(content: bytes, *, expected_page_id: str, title: str) -> bytes:
    """Validate an expected identity and return a new page byte stream with a new title."""

    return parse_page(content, expected_page_id=expected_page_id).with_title(title).to_bytes()


def update_page_metadata(content: bytes, *, expected_page_id: str, updates: Mapping[str, object]) -> bytes:
    """Validate an expected identity and return a new page byte stream with merged metadata."""

    return parse_page(content, expected_page_id=expected_page_id).with_metadata(updates).to_bytes()


def extract_generated_region(content: bytes, *, expected_page_id: str | None = None) -> bytes | None:
    """Return generated body bytes, or ``None`` when the page has no generated region."""

    page = parse_page(content, expected_page_id=expected_page_id)
    bounds = _generated_region_bounds(page.body)
    if bounds is None:
        return None
    start, end = bounds
    return page.body[start:end]


def extract_user_body(content: bytes, *, expected_page_id: str | None = None) -> bytes:
    """Return body bytes outside the one validated generated block."""

    page = parse_page(content, expected_page_id=expected_page_id)
    bounds = _generated_region_bounds(page.body)
    if bounds is None:
        return page.body
    generated_start, closing_start = bounds
    opening_start = page.body.rfind(_GENERATED_OPEN, 0, generated_start)
    if opening_start < 0:
        raise PageValidationError("generated opening marker is missing")
    closing_end = closing_start + len(_GENERATED_CLOSE)
    if page.body[closing_end : closing_end + 2] == b"\r\n":
        closing_end += 2
    elif page.body[closing_end : closing_end + 1] in {b"\n", b"\r"}:
        closing_end += 1
    return page.body[:opening_start] + page.body[closing_end:]


def update_generated_region(
    content: bytes,
    *,
    expected_page_id: str,
    generated: bytes,
    metadata: Mapping[str, object] | None = None,
) -> bytes:
    """Replace or prepend the generated region, preserving all other body bytes."""

    page = parse_page(content, expected_page_id=expected_page_id)
    _validate_generated_content(generated)
    bounds = _generated_region_bounds(page.body)
    if bounds is None:
        body = _GENERATED_OPEN + b"\n" + generated + _GENERATED_CLOSE + b"\n" + page.body
    else:
        start, end = bounds
        body = page.body[:start] + generated + page.body[end:]

    updated = page.with_body(body)
    if metadata is not None:
        return updated.with_metadata(metadata).to_bytes()

    body_offset = content.find(_CLOSE, len(_OPEN)) + len(_CLOSE)
    return content[:body_offset] + updated.body
