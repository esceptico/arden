from mcp.types import (
    AudioContent,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    TextResourceContents,
)

from ntrp.core.raw_tool_results import RAW_TOOL_RESULT_DATA_KEY, read_raw_tool_result
from ntrp.mcp import results as mcp_results


def _adapt(result: CallToolResult):
    return mcp_results.call_tool_result_to_tool_result(result, provider="demo", tool_name="other")


def test_text_only_result_uses_text_content():
    result = _adapt(CallToolResult(content=[TextContent(type="text", text="## Results\n\n- `Note.md`")]))

    assert result.content == "## Results\n\n- `Note.md`"
    assert result.data is None
    assert result.is_error is False


def test_text_and_structured_content_keeps_text_primary():
    result = _adapt(
        CallToolResult(
            content=[TextContent(type="text", text="Found 1 note")],
            structuredContent={"hits": [{"path": "Note.md"}], "warnings": []},
        )
    )

    assert result.content == "Found 1 note"
    assert result.data == {"structuredContent": {"hits": [{"path": "Note.md"}], "warnings": []}}


def test_structured_content_only_falls_back_to_json():
    result = _adapt(CallToolResult(content=[], structuredContent={"hits": [{"path": "Note.md"}], "warnings": []}))

    assert result.content == '{"hits": [{"path": "Note.md"}], "warnings": []}'
    assert result.data == {"structuredContent": {"hits": [{"path": "Note.md"}], "warnings": []}}


def test_error_result_uses_text_content_as_error_message():
    result = _adapt(CallToolResult(content=[TextContent(type="text", text="Permission denied")], isError=True))

    assert result.content == "Permission denied"
    assert result.preview == "Permission denied"
    assert result.is_error is True


def test_multiple_text_blocks_are_joined():
    result = _adapt(
        CallToolResult(
            content=[
                TextContent(type="text", text="First block"),
                TextContent(type="text", text="Second block"),
            ]
        )
    )

    assert result.content == "First block\nSecond block"


def test_non_text_blocks_are_model_safe_placeholders():
    result = _adapt(CallToolResult(content=[ImageContent(type="image", data="base64", mimeType="image/png")]))

    assert result.content == "[image content]"
    assert result.data == {
        "content": [
            {
                "type": "image",
                "mimeType": "image/png",
                "base64Length": 6,
            }
        ]
    }
    assert [block.model_dump() for block in result.model_content] == [
        {"type": "image", "media_type": "image/png", "data": "base64"}
    ]


def test_audio_blocks_are_forwarded_as_model_content():
    result = _adapt(CallToolResult(content=[AudioContent(type="audio", data="base64", mimeType="audio/mpeg")]))

    assert result.content == "[audio content]"
    assert [block.model_dump() for block in result.model_content] == [
        {"type": "audio", "media_type": "audio/mpeg", "data": "base64"}
    ]


def test_large_structured_payload_is_bounded_and_durably_retrievable():
    result = _adapt(
        CallToolResult(
            content=[TextContent(type="text", text="Found records")],
            structuredContent={"rows": [{"body": "x" * 100_000}], "next_cursor": "page-2"},
        )
    )

    assert result.data["truncated"] is True
    assert result.data["raw_ref"].startswith("sha256:")
    assert result.data["next_cursor"] == "page-2"
    assert len(str(result.data)) < 5_000
    blob = result.data[RAW_TOOL_RESULT_DATA_KEY]
    raw = read_raw_tool_result(blob["blob_path"], compression=blob["compression"])
    assert '"next_cursor":"page-2"' in raw
    assert "x" * 1_000 in raw


def test_large_media_payload_is_bounded_and_not_inlined_to_model():
    result = _adapt(
        CallToolResult(content=[ImageContent(type="image", data="x" * 100_000, mimeType="image/png")])
    )

    assert result.data["truncated"] is True
    assert result.model_content == ()
    assert len(str(result.data)) < 5_000


def test_text_blocks_are_not_polluted_by_non_text_placeholders():
    result = _adapt(
        CallToolResult(
            content=[
                TextContent(type="text", text="Visible result"),
                ImageContent(type="image", data="base64", mimeType="image/png"),
            ]
        )
    )

    assert result.content == "Visible result"
    assert result.data == {
        "content": [
            {
                "type": "image",
                "mimeType": "image/png",
                "base64Length": 6,
            }
        ]
    }


def test_embedded_text_resource_is_model_visible_text():
    result = _adapt(
        CallToolResult(
            content=[
                EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri="file:///tmp/note.md",
                        mimeType="text/markdown",
                        text="# Note",
                    ),
                )
            ]
        )
    )

    assert result.content == "# Note"
    assert result.data == {
        "content": [
            {
                "type": "resource",
                "uri": "file:///tmp/note.md",
                "mimeType": "text/markdown",
            }
        ]
    }


def test_result_meta_is_preserved_outside_model_content():
    result = _adapt(
        CallToolResult(
            content=[TextContent(type="text", text="Visible")],
            _meta={"trace_id": "abc"},
        )
    )

    assert result.content == "Visible"
    assert result.data == {"_meta": {"trace_id": "abc"}}


def test_mcp_resource_content_emits_stable_deduplicated_refs():
    result = CallToolResult(
        content=[
            ResourceLink(
                type="resource_link",
                name="guide",
                title="Setup guide",
                uri="https://docs.example.test/guide",
            ),
            EmbeddedResource(
                type="resource",
                resource=TextResourceContents(
                    uri="https://docs.example.test/guide",
                    text="duplicate",
                ),
            ),
            EmbeddedResource(
                type="resource",
                resource=TextResourceContents(
                    uri="file:///tmp/note.md",
                    mimeType="text/markdown",
                    text="# Note",
                ),
            ),
        ]
    )

    refs = mcp_results.extract_mcp_source_refs(result, provider="demo", tool_name="read")

    assert [ref.to_dict() for ref in refs] == [
        {
            "provider": "demo",
            "kind": "resource",
            "ref": "https://docs.example.test/guide",
            "title": "Setup guide",
            "url": "https://docs.example.test/guide",
        },
        {
            "provider": "demo",
            "kind": "resource",
            "ref": "file:///tmp/note.md",
            "title": "file:///tmp/note.md",
        },
    ]


def test_canonical_mcp_search_extracts_only_top_level_results():
    result = CallToolResult(
        content=[],
        structuredContent={
            "results": [
                {"id": "doc-1", "title": "First", "url": "https://example.test/first"},
                {"id": "doc-2", "title": "Second"},
                {"id": "doc-1", "title": "Duplicate", "url": "https://example.test/duplicate"},
                {"id": "missing-title", "url": "https://example.test/invalid"},
            ]
        },
    )

    refs = mcp_results.extract_mcp_source_refs(result, provider="demo", tool_name="search")

    assert [ref.to_dict() for ref in refs] == [
        {
            "provider": "demo",
            "kind": "search_result",
            "ref": "doc-1",
            "title": "First",
            "url": "https://example.test/first",
        },
        {
            "provider": "demo",
            "kind": "search_result",
            "ref": "doc-2",
            "title": "Second",
        },
    ]


def test_canonical_mcp_fetch_extracts_document_with_optional_url():
    result = CallToolResult(
        content=[],
        structuredContent={
            "id": "doc-1",
            "title": "First",
            "text": "Document body",
            "url": "https://example.test/first",
        },
    )

    refs = mcp_results.extract_mcp_source_refs(result, provider="demo", tool_name="fetch")

    assert [ref.to_dict() for ref in refs] == [
        {
            "provider": "demo",
            "kind": "document",
            "ref": "doc-1",
            "title": "First",
            "url": "https://example.test/first",
        }
    ]


def test_canonical_mcp_sources_use_ids_when_titles_are_blank():
    search = CallToolResult(
        content=[],
        structuredContent={"results": [{"id": "search-1", "title": "   "}]},
    )
    fetch = CallToolResult(
        content=[],
        structuredContent={"id": "document-1", "title": "   ", "text": "Document body"},
    )

    search_refs = mcp_results.extract_mcp_source_refs(search, provider="demo", tool_name="search")
    fetch_refs = mcp_results.extract_mcp_source_refs(fetch, provider="demo", tool_name="fetch")

    assert [ref.title for ref in search_refs] == ["search-1"]
    assert [ref.title for ref in fetch_refs] == ["document-1"]


def test_mcp_extraction_ignores_nested_or_inexact_contracts():
    nested = CallToolResult(
        content=[],
        structuredContent={
            "wrapper": {"results": [{"id": "x", "title": "Hidden", "url": "https://ignored.test"}]}
        },
    )
    inexact_name = CallToolResult(
        content=[],
        structuredContent={"results": [{"id": "x", "title": "Hidden", "url": "https://ignored.test"}]},
    )
    incomplete_fetch = CallToolResult(
        content=[],
        structuredContent={"id": "x", "title": "Hidden", "url": "https://ignored.test"},
    )
    empty_fetch = CallToolResult(
        content=[],
        structuredContent={"id": "x", "title": "Hidden", "text": "   ", "url": "https://ignored.test"},
    )

    assert mcp_results.extract_mcp_source_refs(nested, provider="demo", tool_name="search") == ()
    assert mcp_results.extract_mcp_source_refs(inexact_name, provider="demo", tool_name="search_notes") == ()
    assert mcp_results.extract_mcp_source_refs(incomplete_fetch, provider="demo", tool_name="fetch") == ()
    assert mcp_results.extract_mcp_source_refs(empty_fetch, provider="demo", tool_name="fetch") == ()


def test_mcp_resource_uri_with_credentials_is_not_a_source():
    result = CallToolResult(
        content=[
            ResourceLink(
                type="resource_link",
                name="private",
                title="https://user:secret@example.test/private",
                uri="https://user:secret@example.test/private",
            )
        ]
    )

    assert mcp_results.extract_mcp_source_refs(result, provider="demo", tool_name="read") == ()


def test_mcp_search_stops_iterating_after_50_normalized_unique_refs():
    class BoundedResults(list):
        def __iter__(self):
            for index, item in enumerate(super().__iter__()):
                if index >= 50:
                    raise AssertionError("extractor iterated past the per-call source bound")
                yield item

    resource_refs = [
        ResourceLink(
            type="resource_link",
            name=f"resource-{index}",
            title=f"Resource {index}",
            uri=f"https://example.test/resource-{index}",
        )
        for index in range(2)
    ]
    results = BoundedResults(
        [
            {"id": "", "title": "Invalid"},
            {"id": "https://example.test/resource-0", "title": "Duplicate"},
            *({"id": f"doc-{index}", "title": f"Document {index}"} for index in range(48)),
            {"id": "overflow", "title": "Must not be read"},
        ]
    )
    result = CallToolResult(content=resource_refs, structuredContent={"results": results})

    refs = mcp_results.extract_mcp_source_refs(result, provider="demo", tool_name="search")

    assert len(refs) == 50
    assert refs[-1].ref == "doc-47"
