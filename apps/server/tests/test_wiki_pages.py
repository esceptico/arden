"""Contract tests for the common visible wiki-page codec."""

from __future__ import annotations

import pytest

from arden.wiki.pages import (
    PageValidationError,
    create_page,
    parse_page,
    update_page_metadata,
    update_page_title,
)


def _raw(frontmatter: str, body: bytes = b"# Notes\n") -> bytes:
    return b"---\n" + frontmatter.encode() + b"---\n" + body


def test_create_and_parse_page_preserves_identity_and_body_bytes() -> None:
    page = create_page(title="Bicycle", aliases=["Bike"], body=b"hello\n")

    parsed = parse_page(page.to_bytes(), expected_page_id=page.page_id)

    assert parsed.page_id == page.page_id
    assert parsed.title == "Bicycle"
    assert parsed.aliases == ("Bike",)
    assert parsed.lifecycle == "active"
    assert parsed.body == b"hello\n"


def test_metadata_and_title_updates_are_immutable_and_preserve_exact_body_bytes() -> None:
    original = _raw(
        "page_id: stable-1\ntitle: Before\naliases: []\nlifecycle: active\ncustom:\n  nested: [1, true]\n",
        b"\n<!-- generated -->\ntext\n<!-- /generated -->\n\n## User\n\xff",
    )

    with pytest.raises(PageValidationError, match="UTF-8"):
        parse_page(original)

    original = original.replace(b"\xff", "é".encode())
    changed = update_page_metadata(original, expected_page_id="stable-1", updates={"source": "user"})
    changed = update_page_title(changed, expected_page_id="stable-1", title="After")
    parsed = parse_page(changed)

    assert parsed.title == "After"
    assert parsed.metadata == {"custom": {"nested": [1, True]}, "source": "user"}
    assert parsed.body == parse_page(original).body


@pytest.mark.parametrize(
    ("frontmatter", "message"),
    [
        ("page_id: x\ntitle: X\naliases: [x]\nlifecycle: active\n", "duplicate"),
        ("page_id: x\ntitle: X\naliases: []\nlifecycle: archived\n", "lifecycle"),
        ("page_id: x\ntitle: X\naliases: []\nlifecycle: redirect\n", "redirect"),
        ("page_id: x\ntitle: X\naliases: []\nlifecycle: active\nredirect_to: y\n", "redirect_to"),
        ("page_id: x\ntitle: X\naliases: []\nlifecycle: active\nrevision: abc\n", "identity"),
        ("page_id: x\ntitle: X\ntitle: Y\naliases: []\nlifecycle: active\n", "invalid YAML"),
        ("page_id: x\ntitle: X\naliases: []\nlifecycle: active\nwhen: 2026-07-28\n", "JSON-like"),
    ],
)
def test_rejects_invalid_visible_contract(frontmatter: str, message: str) -> None:
    with pytest.raises(PageValidationError, match=message):
        parse_page(_raw(frontmatter))


@pytest.mark.parametrize(
    "body",
    [
        b"<!-- generated -->\nmissing close\n",
        b"<!-- /generated -->\n",
        b"<!-- /generated -->\n<!-- generated -->\n",
        b"text <!-- generated -->\n<!-- /generated -->\n",
        b"<!-- generated -->\n<!-- generated -->\n<!-- /generated -->\n",
    ],
)
def test_rejects_malformed_generated_boundaries(body: bytes) -> None:
    with pytest.raises(PageValidationError, match="generated"):
        parse_page(_raw("page_id: x\ntitle: X\naliases: []\nlifecycle: active\n", body))


def test_rejects_expected_identity_mismatch_and_reserved_metadata_update() -> None:
    content = _raw("page_id: actual\ntitle: X\naliases: []\nlifecycle: active\n")

    with pytest.raises(PageValidationError, match="expected"):
        parse_page(content, expected_page_id="other")
    with pytest.raises(PageValidationError, match="dedicated"):
        update_page_metadata(content, expected_page_id="actual", updates={"page_id": "other"})
