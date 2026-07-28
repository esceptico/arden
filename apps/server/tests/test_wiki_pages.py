"""Contract tests for the common visible wiki-page codec."""

from __future__ import annotations

import pytest

from arden.wiki.pages import (
    PageValidationError,
    create_page,
    extract_generated_region,
    parse_page,
    update_generated_region,
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
    assert parsed.metadata == {"custom": {"nested": (1, True)}, "source": "user"}
    assert parsed.body == parse_page(original).body


def test_nested_metadata_is_detached_frozen_and_round_trips_through_yaml() -> None:
    source = {"nested": {"items": [{"value": 1}]}}
    page = create_page(title="One", page_id="one", metadata=source)
    source["nested"]["items"][0]["value"] = 9
    source["nested"]["items"].append({"value": 2})

    nested = page.metadata["nested"]
    assert nested["items"][0]["value"] == 1
    assert len(nested["items"]) == 1
    with pytest.raises(TypeError):
        nested["extra"] = True
    with pytest.raises(TypeError):
        nested["items"][0]["value"] = 2
    with pytest.raises(AttributeError):
        nested["items"].append({"value": 3})

    parsed = parse_page(page.to_bytes(), expected_page_id="one")
    renamed = parsed.with_title("Renamed")
    updated = renamed.with_metadata({"other": {"values": [2, 3]}})
    assert parsed.metadata == page.metadata
    assert renamed.metadata == page.metadata
    assert updated.metadata["other"]["values"] == (2, 3)
    assert parse_page(updated.to_bytes()).metadata == updated.metadata


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


def test_generated_region_replaces_only_inner_bytes_and_supports_crlf() -> None:
    prefix = b"User text\r\n<!-- generated -->\r\n"
    suffix = b"<!-- /generated -->\r\nStill user text\r\n"
    content = _raw("page_id: stable\ntitle: X\naliases: []\nlifecycle: active\n", prefix + b"old\r\n" + suffix)

    updated = update_generated_region(content, expected_page_id="stable", generated=b"new\n")

    assert updated.startswith(content[: content.index(prefix) + len(prefix)])
    assert parse_page(updated).body == prefix + b"new\n" + suffix
    assert extract_generated_region(updated, expected_page_id="stable") == b"new\n"


def test_generated_region_is_prepended_without_changing_original_body_suffix() -> None:
    body = b"## User\r\nKeep this exact.\r\n"
    content = _raw("page_id: stable\ntitle: X\naliases: []\nlifecycle: active\n", body)

    updated = update_generated_region(
        content,
        expected_page_id="stable",
        generated=b"## Generated\n",
        metadata={"generated_from_revision": "abc"},
    )

    parsed = parse_page(updated, expected_page_id="stable")
    assert parsed.body == b"<!-- generated -->\n## Generated\n<!-- /generated -->\n" + body
    assert parsed.body.endswith(body)
    assert parsed.metadata["generated_from_revision"] == "abc"
    assert extract_generated_region(updated) == b"## Generated\n"


def test_generated_region_extracts_empty_content() -> None:
    content = _raw(
        "page_id: stable\ntitle: X\naliases: []\nlifecycle: active\n",
        b"<!-- generated -->\n<!-- /generated -->\n",
    )

    assert extract_generated_region(content) == b""
    assert update_generated_region(content, expected_page_id="stable", generated=b"") == content


@pytest.mark.parametrize(
    ("generated", "message"),
    [
        (b"not terminated", "newline-terminated"),
        (b"\xff\n", "UTF-8"),
        (b"<!-- generated -->\n", "markers"),
        (b"<!-- /generated -->\n", "markers"),
    ],
)
def test_generated_region_rejects_invalid_generated_content(generated: bytes, message: str) -> None:
    content = _raw("page_id: stable\ntitle: X\naliases: []\nlifecycle: active\n")

    with pytest.raises(PageValidationError, match=message):
        update_generated_region(content, expected_page_id="stable", generated=generated)


def test_with_body_validates_generated_boundaries() -> None:
    page = create_page(title="X")

    with pytest.raises(PageValidationError, match="generated"):
        page.with_body(b"<!-- generated -->\n")
