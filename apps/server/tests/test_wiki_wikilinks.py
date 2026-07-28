from collections.abc import Iterator, Mapping
from dataclasses import replace

import pytest

from arden.wiki.wikilinks import (
    StaleWikilinkError,
    UnsafeWikilinkTargetError,
    WikilinkNode,
    parse_wikilinks,
    rewrite_page_targets,
)


def test_parse_preserves_embed_fragment_alias_whitespace_and_unicode_byte_spans() -> None:
    source = "![[  Проект  # Section | Display ]]"

    (node,) = parse_wikilinks(source)

    assert node.embed is True
    assert node.raw == source
    assert node.page == "Проект"
    assert node.fragment == " Section "
    assert node.alias == " Display "
    assert node.start == 0
    assert node.end == len(source.encode("utf-8"))
    assert node.page_start == len(b"![[  ")
    assert node.page_end == len("![[  Проект".encode())


def test_rewrite_changes_only_page_spans_and_preserves_crlf_and_unrelated_bytes() -> None:
    source = "First ![[  Old Name #Heading | shown ]]\r\nSecond [[Old Name]]\r\n[[unrelated]]\r\n"
    first, second, unrelated = parse_wikilinks(source)

    rewritten = rewrite_page_targets(source, {first: "New Name", second: "New Name"})

    assert rewritten == "First ![[  New Name #Heading | shown ]]\r\nSecond [[New Name]]\r\n[[unrelated]]\r\n"
    assert unrelated.page == "unrelated"


def test_parser_accepts_visible_text_but_excludes_markdown_non_text_contexts() -> None:
    source = """visible [[One]]
`[[Inline code]]`
```md
[[Fenced code]]
```
\\[[Escaped]]
<!-- [[Comment]] -->
<span>[[HTML text]]</span>
<a href="[[HTML attribute]]">text</a>
[destination]([[Markdown destination]])
<https://example.test/[[AutolinkDestination]]>
[label [[Visible label]]](https://example.test)
"""

    assert [node.page for node in parse_wikilinks(source)] == ["One", "Visible label"]


def test_unclosed_angle_placeholders_do_not_hide_later_visible_links() -> None:
    source = "Use `<ACT>` and a _Changed <date/time>._ marker.\n\nSee [[Visible]].\n"

    assert [node.page for node in parse_wikilinks(source)] == ["Visible"]
    assert [node.page for node in parse_wikilinks("<ACT> [[Visible]]")] == ["Visible"]


def test_unclosed_standard_html_still_hides_contained_wikilinks() -> None:
    assert parse_wikilinks('<a href="/elsewhere">[[Hidden]]') == ()


@pytest.mark.parametrize("tag", ["x-foo", "font", "big", "tt", "strike", "output"])
def test_unclosed_raw_inline_html_hides_contained_wikilinks(tag: str) -> None:
    assert parse_wikilinks(f"<{tag}>[[Hidden]]") == ()


def test_html_tracking_uses_markdown_tokens_instead_of_code_or_escaped_text() -> None:
    assert [node.page for node in parse_wikilinks("```\n<a>\n```\n[[Visible]]")] == ["Visible"]
    assert parse_wikilinks("<a> `</a>` [[Hidden]]") == ()
    assert [node.page for node in parse_wikilinks("\\<a> [[Visible]]")] == ["Visible"]
    assert [node.page for node in parse_wikilinks("<a !> [[Visible]]")] == ["Visible"]


def test_raw_html_state_spans_markdown_blocks_without_hiding_prose_placeholders() -> None:
    assert parse_wikilinks("<a>\n\n[[Hidden]]") == ()
    assert parse_wikilinks("<div>\n\n[[Hidden]]\n\n</div>") == ()
    assert [node.page for node in parse_wikilinks("<div>\n\nHidden\n\n</div>\n\n[[Visible]]")] == ["Visible"]
    assert [node.page for node in parse_wikilinks("<ACT>\n\n[[Visible]]")] == ["Visible"]


def test_parser_excludes_only_complete_leading_yaml_frontmatter() -> None:
    source = "---\r\nname: [[Hidden]]\r\n---\r\n[[Visible]]\r\n"

    assert [node.page for node in parse_wikilinks(source)] == ["Visible"]
    assert [node.page for node in parse_wikilinks("---\n[[Not frontmatter]]")] == ["Not frontmatter"]


def test_parser_handles_fragment_only_links_and_escaped_delimiters_without_normalizing() -> None:
    source = "[[#heading]] [[Page\\|not an alias]] [[Page\\#not a fragment]] [[Page|Alias\\|kept]]"

    fragment_only, escaped_alias, escaped_fragment, aliased = parse_wikilinks(source)

    assert fragment_only.page is None
    assert fragment_only.fragment == "heading"
    assert escaped_alias.page == "Page\\|not an alias"
    assert escaped_alias.alias is None
    assert escaped_fragment.page == "Page\\#not a fragment"
    assert escaped_fragment.fragment is None
    assert aliased.page == "Page"
    assert aliased.alias == "Alias\\|kept"


def test_malformed_opening_does_not_consume_a_later_complete_wikilink() -> None:
    source = "[[unterminated [[Later]]"

    assert [node.page for node in parse_wikilinks(source)] == ["Later"]


def test_rewrite_rejects_stale_source_and_forged_spans() -> None:
    source = "[[Old]]"
    (node,) = parse_wikilinks(source)

    with pytest.raises(StaleWikilinkError, match="different source"):
        rewrite_page_targets("[[Old!]]", {node: "New"})
    with pytest.raises(StaleWikilinkError, match="not parsed"):
        rewrite_page_targets(source, {replace(node, page_end=node.end + 1): "New"})


def test_rewrite_rejects_overlapping_nodes_even_if_they_have_matching_fingerprint() -> None:
    source = "[[Old]]"
    (node,) = parse_wikilinks(source)

    class DuplicateNodeMapping(Mapping[WikilinkNode, str]):
        def __getitem__(self, key: WikilinkNode) -> str:
            return "New"

        def __iter__(self) -> Iterator[WikilinkNode]:
            return iter((node, node))

        def __len__(self) -> int:
            return 2

        def items(self) -> Iterator[tuple[WikilinkNode, str]]:
            return iter(((node, "New"), (node, "Newer")))

    with pytest.raises(StaleWikilinkError, match="overlap"):
        rewrite_page_targets(source, DuplicateNodeMapping())


@pytest.mark.parametrize(
    "replacement", ["", " New", "New ", "New#Heading", "New|Alias", "New[[x]]", "New\\x", "New\nName"]
)
def test_rewrite_rejects_unsafe_page_targets(replacement: str) -> None:
    source = "[[Old]]"
    (node,) = parse_wikilinks(source)

    with pytest.raises(UnsafeWikilinkTargetError):
        rewrite_page_targets(source, {node: replacement})


def test_rewrite_rejects_fragment_only_and_non_node_keys() -> None:
    source = "[[#heading]]"
    (node,) = parse_wikilinks(source)

    with pytest.raises(StaleWikilinkError, match="no page target"):
        rewrite_page_targets(source, {node: "Page"})
    with pytest.raises(TypeError, match="WikilinkNode"):
        rewrite_page_targets("[[Old]]", {"not a node": "New"})  # type: ignore[dict-item]


def test_rewrite_requires_mapping_and_preserves_empty_plan_identity() -> None:
    source = "привет [[Page]]\r\n"

    assert rewrite_page_targets(source, {}) == source
    with pytest.raises(TypeError, match="must map"):
        rewrite_page_targets(source, [])  # type: ignore[arg-type]
