"""Lossless parsing and exact rewriting for visible Obsidian wikilinks.

This module deliberately knows only a Markdown string.  It never resolves page
names or touches the filesystem.  A small lexer records exact UTF-8 byte spans;
``markdown-it`` then proves that each candidate is visible Markdown text rather
than code, raw HTML, a comment, or a Markdown link destination.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from itertools import pairwise
from typing import Any

from markdown_it import MarkdownIt

_MARKDOWN = MarkdownIt("commonmark", {"html": True})
_VOID_HTML_ELEMENTS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)


class WikilinkError(ValueError):
    """Base error for exact wikilink operations."""


class StaleWikilinkError(WikilinkError):
    """A node was not parsed from the exact source being rewritten."""


class UnsafeWikilinkTargetError(WikilinkError):
    """A replacement is not safe as the page part of a wikilink."""


@dataclass(frozen=True, slots=True)
class WikilinkNode:
    """One visible wikilink, with all offsets expressed as UTF-8 byte offsets.

    ``page_start`` and ``page_end`` select only the page part, excluding
    preserved whitespace, a fragment, and an alias.  They are ``None`` for a
    fragment-only link such as ``[[#heading]]``.  ``source_sha256`` binds the
    node to one exact source value, making stale rewrites fail closed.
    """

    source_sha256: str
    source_size: int
    start: int
    end: int
    page_start: int | None
    page_end: int | None
    page: str | None
    fragment: str | None
    alias: str | None
    embed: bool
    raw: str


@dataclass(frozen=True, slots=True)
class _Candidate:
    start: int
    end: int
    page_start: int | None
    page_end: int | None
    page: str | None
    fragment: str | None
    alias: str | None
    embed: bool


def parse_wikilinks(source: str) -> tuple[WikilinkNode, ...]:
    """Return visible Obsidian wikilinks in source order.

    The input is never normalized: Unicode and line endings are retained, and
    offsets in the returned nodes address the original UTF-8 byte sequence.
    """

    source_bytes = _encode_source(source)
    candidates = _lex_candidates(source)
    if not candidates:
        return ()

    visible = _visible_candidate_indexes(source, candidates)
    if not visible:
        return ()

    byte_offsets = _byte_offsets(source)
    source_sha256 = sha256(source_bytes).hexdigest()
    nodes: list[WikilinkNode] = []
    for index, candidate in enumerate(candidates):
        if index not in visible:
            continue
        nodes.append(
            WikilinkNode(
                source_sha256=source_sha256,
                source_size=len(source_bytes),
                start=byte_offsets[candidate.start],
                end=byte_offsets[candidate.end],
                page_start=(byte_offsets[candidate.page_start] if candidate.page_start is not None else None),
                page_end=(byte_offsets[candidate.page_end] if candidate.page_end is not None else None),
                page=candidate.page,
                fragment=candidate.fragment,
                alias=candidate.alias,
                embed=candidate.embed,
                raw=source[candidate.start : candidate.end],
            )
        )
    return tuple(nodes)


def rewrite_page_targets(source: str, replacements: Mapping[WikilinkNode, str]) -> str:
    """Apply exact page-part replacements from nodes parsed from ``source``.

    The caller chooses the nodes to change; this function never performs a
    global string substitution.  Every node must be from the exact source, its
    original page bytes must still match, replacements must be safe page parts,
    and spans must not overlap.
    """

    if not isinstance(replacements, Mapping):
        raise TypeError("replacements must map WikilinkNode values to page targets")
    if not replacements:
        return source

    source_bytes = _encode_source(source)
    fingerprint = sha256(source_bytes).hexdigest()
    parsed_nodes = frozenset(parse_wikilinks(source))
    edits: list[tuple[int, int, bytes]] = []
    for node, replacement in replacements.items():
        _validate_rewrite_node(node, source_bytes, fingerprint, parsed_nodes)
        replacement_bytes = _validate_replacement(replacement)
        assert node.page_start is not None
        assert node.page_end is not None
        edits.append((node.page_start, node.page_end, replacement_bytes))

    edits.sort(key=lambda edit: (edit[0], edit[1]))
    for previous, current in pairwise(edits):
        if current[0] < previous[1]:
            raise StaleWikilinkError("wikilink page spans overlap")

    rewritten = bytearray()
    cursor = 0
    for start, end, replacement in edits:
        rewritten.extend(source_bytes[cursor:start])
        rewritten.extend(replacement)
        cursor = end
    rewritten.extend(source_bytes[cursor:])
    return bytes(rewritten).decode("utf-8")


def _lex_candidates(source: str) -> tuple[_Candidate, ...]:
    candidates: list[_Candidate] = []
    index = 0
    source_length = len(source)
    while index + 1 < source_length:
        if source[index : index + 2] != "[[" or _is_escaped(source, index):
            index += 1
            continue
        close = _closing_brackets(source, index + 2)
        if close is None:
            index += 2
            continue
        candidate = _candidate_from_bounds(source, index, close)
        if candidate is not None:
            candidates.append(candidate)
        index = close + 2
    return tuple(candidates)


def _candidate_from_bounds(source: str, opening: int, closing: int) -> _Candidate | None:
    content_start = opening + 2
    content_end = closing
    alias_separator = _first_unescaped(source, "|", content_start, content_end)
    target_end = alias_separator if alias_separator is not None else content_end
    fragment_separator = _first_unescaped(source, "#", content_start, target_end)
    page_limit = fragment_separator if fragment_separator is not None else target_end
    page_start, page_end = _trim_span(source, content_start, page_limit)

    page: str | None
    if page_start == page_end:
        page = None
        page_start = None
        page_end = None
    else:
        page = source[page_start:page_end]

    fragment = None
    if fragment_separator is not None:
        fragment = source[fragment_separator + 1 : target_end]
    alias = None
    if alias_separator is not None:
        alias = source[alias_separator + 1 : content_end]

    embed_start = opening
    embed = opening > 0 and source[opening - 1] == "!" and not _is_escaped(source, opening - 1)
    if embed:
        embed_start -= 1
    return _Candidate(
        start=embed_start,
        end=closing + 2,
        page_start=page_start,
        page_end=page_end,
        page=page,
        fragment=fragment,
        alias=alias,
        embed=embed,
    )


def _visible_candidate_indexes(source: str, candidates: Sequence[_Candidate]) -> frozenset[int]:
    """Use Markdown token types to accept only candidates visible as text."""

    markers = tuple(_marker(index, source) for index in range(len(candidates)))
    marked_source = _insert_markers(source, candidates, markers)
    visible_markers = _text_markers(_MARKDOWN.parse(marked_source), markers)
    html_spans = _html_spans(source)
    autolink_spans = _autolink_spans(source)
    frontmatter_span = _leading_frontmatter_span(source)
    return frozenset(
        index
        for index, (candidate, marker) in enumerate(zip(candidates, markers, strict=True))
        if marker in visible_markers
        and not _is_within_any_span(candidate.start, candidate.end, html_spans)
        and not _is_within_any_span(candidate.start, candidate.end, autolink_spans)
        and not (frontmatter_span and _is_within_any_span(candidate.start, candidate.end, (frontmatter_span,)))
    )


def _marker(index: int, source: str) -> str:
    # Uppercase ASCII is plain text in all contexts relevant here.  Include the
    # source digest so normal text cannot accidentally impersonate a marker.
    return f"ARDENWIKILINKTOKEN{index}X{sha256(_encode_source(source)).hexdigest()}"


def _insert_markers(source: str, candidates: Sequence[_Candidate], markers: Sequence[str]) -> str:
    edits: list[tuple[int, int, str]] = []
    for candidate, marker in zip(candidates, markers, strict=True):
        if candidate.page_start is None or candidate.page_end is None:
            # Fragment-only links still need a marker in their content so
            # markdown-it validates their context.  It is never rewritable.
            content_start = candidate.start + (3 if candidate.embed else 2)
            edits.append((content_start, content_start, marker))
        else:
            edits.append((candidate.page_start, candidate.page_end, marker))
    marked = source
    for start, end, marker in reversed(edits):
        marked = f"{marked[:start]}{marker}{marked[end:]}"
    return marked


def _text_markers(tokens: Sequence[Any], markers: Sequence[str]) -> frozenset[str]:
    found: set[str] = set()

    def visit(token: Any) -> None:
        if token.type == "text":
            found.update(marker for marker in markers if marker in token.content)
        if token.children:
            for child in token.children:
                visit(child)

    for token in tokens:
        visit(token)
    return frozenset(found)


def _html_spans(source: str) -> tuple[tuple[int, int], ...]:
    """Return raw HTML/comment ranges, including contents of paired elements."""

    spans: list[tuple[int, int]] = []
    open_elements: list[tuple[str, int]] = []
    index = 0
    while index < len(source):
        if source.startswith("<!--", index):
            closing = source.find("-->", index + 4)
            end = len(source) if closing == -1 else closing + 3
            spans.append((index, end))
            index = end
            continue
        parsed = _html_tag_at(source, index)
        if parsed is None:
            index += 1
            continue
        name, closing, self_closing, end = parsed
        spans.append((index, end))
        if closing:
            for position in range(len(open_elements) - 1, -1, -1):
                open_name, open_start = open_elements[position]
                if open_name == name:
                    spans.append((open_start, end))
                    del open_elements[position:]
                    break
        elif not self_closing and name not in _VOID_HTML_ELEMENTS:
            open_elements.append((name, index))
        index = end
    spans.extend((start, len(source)) for _name, start in open_elements)
    return tuple(spans)


def _autolink_spans(source: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(source):
        if source[index] != "<":
            index += 1
            continue
        end = source.find(">", index + 1)
        if end == -1:
            break
        if _is_uri_autolink(source[index + 1 : end]):
            spans.append((index, end + 1))
        index = end + 1
    return tuple(spans)


def _is_uri_autolink(value: str) -> bool:
    colon = value.find(":")
    if colon < 1 or colon > 32 or colon == len(value) - 1:
        return False
    scheme = value[:colon]
    if not scheme[0].isalpha() or not all(character.isalnum() or character in "+.-" for character in scheme[1:]):
        return False
    return not any(character.isspace() or character in "<>" for character in value)


def _leading_frontmatter_span(source: str) -> tuple[int, int] | None:
    """Find only a complete YAML frontmatter block at byte-zero source start."""

    first_end = _line_end(source, 0)
    if _line_body(source, 0, first_end).strip() != "---":
        return None
    line_start = first_end
    while line_start < len(source):
        line_end = _line_end(source, line_start)
        if _line_body(source, line_start, line_end).strip() in {"---", "..."}:
            return 0, line_end
        line_start = line_end
    return None


def _line_end(source: str, start: int) -> int:
    newline = source.find("\n", start)
    return len(source) if newline == -1 else newline + 1


def _line_body(source: str, start: int, end: int) -> str:
    body = source[start:end]
    if body.endswith("\n"):
        body = body[:-1]
    if body.endswith("\r"):
        body = body[:-1]
    return body


def _html_tag_at(source: str, start: int) -> tuple[str, bool, bool, int] | None:
    if source[start] != "<":
        return None
    index = start + 1
    closing = False
    if index < len(source) and source[index] == "/":
        closing = True
        index += 1
    if index >= len(source) or not source[index].isalpha():
        return None
    name_start = index
    index += 1
    while index < len(source) and (source[index].isalnum() or source[index] == "-"):
        index += 1
    name = source[name_start:index].lower()
    if index < len(source) and not (source[index].isspace() or source[index] in "/>"):
        return None

    quote: str | None = None
    while index < len(source):
        character = source[index]
        if quote is not None:
            if character == quote:
                quote = None
        elif character in "\"'":
            quote = character
        elif character == ">":
            before_end = index - 1
            while before_end >= start and source[before_end].isspace():
                before_end -= 1
            return name, closing, source[before_end] == "/", index + 1
        index += 1
    return None


def _is_within_any_span(start: int, end: int, spans: Sequence[tuple[int, int]]) -> bool:
    return any(start < span_end and end > span_start for span_start, span_end in spans)


def _closing_brackets(source: str, start: int) -> int | None:
    index = start
    while index + 1 < len(source):
        if source[index : index + 2] == "]]" and not _is_escaped(source, index):
            return index
        if source[index : index + 2] == "[[" and not _is_escaped(source, index):
            return None
        index += 1
    return None


def _first_unescaped(source: str, needle: str, start: int, end: int) -> int | None:
    for index in range(start, end):
        if source[index] == needle and not _is_escaped(source, index):
            return index
    return None


def _is_escaped(source: str, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and source[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def _trim_span(source: str, start: int, end: int) -> tuple[int, int]:
    while start < end and source[start].isspace():
        start += 1
    while end > start and source[end - 1].isspace():
        end -= 1
    return start, end


def _byte_offsets(source: str) -> tuple[int, ...]:
    offsets = [0]
    total = 0
    for character in source:
        total += len(character.encode("utf-8"))
        offsets.append(total)
    return tuple(offsets)


def _encode_source(source: str) -> bytes:
    if not isinstance(source, str):
        raise TypeError("source must be str")
    try:
        return source.encode("utf-8")
    except UnicodeEncodeError as error:
        raise WikilinkError("source must be valid UTF-8 text") from error


def _validate_rewrite_node(
    node: object,
    source: bytes,
    fingerprint: str,
    parsed_nodes: frozenset[WikilinkNode],
) -> None:
    if not isinstance(node, WikilinkNode):
        raise TypeError("replacement keys must be WikilinkNode values")
    if node.source_sha256 != fingerprint or node.source_size != len(source):
        raise StaleWikilinkError("wikilink node was parsed from different source")
    if node not in parsed_nodes:
        raise StaleWikilinkError("wikilink node was not parsed from this source")
    if node.page_start is None or node.page_end is None or node.page is None:
        raise StaleWikilinkError("wikilink has no page target to rewrite")
    if not (0 <= node.start <= node.page_start <= node.page_end <= node.end <= len(source)):
        raise StaleWikilinkError("wikilink node has invalid byte spans")
    try:
        page_bytes = node.page.encode("utf-8")
        raw_bytes = node.raw.encode("utf-8")
    except UnicodeEncodeError as error:
        raise StaleWikilinkError("wikilink node contains invalid UTF-8 text") from error
    if source[node.page_start : node.page_end] != page_bytes:
        raise StaleWikilinkError("wikilink page bytes no longer match")
    if source[node.start : node.end] != raw_bytes:
        raise StaleWikilinkError("wikilink bytes no longer match")


def _validate_replacement(replacement: object) -> bytes:
    if not isinstance(replacement, str):
        raise UnsafeWikilinkTargetError("replacement page target must be str")
    if not replacement or replacement != replacement.strip():
        raise UnsafeWikilinkTargetError("replacement page target must be non-empty and trimmed")
    if any(character in "[]|#\\\r\n\x00" or ord(character) < 32 for character in replacement):
        raise UnsafeWikilinkTargetError("replacement page target contains wikilink syntax or control characters")
    try:
        return replacement.encode("utf-8")
    except UnicodeEncodeError as error:
        raise UnsafeWikilinkTargetError("replacement page target must be valid UTF-8") from error
