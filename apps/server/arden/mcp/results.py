import json
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import islice
from typing import Any

import mcp_types

from arden.agent.types.tools import ToolSourceRef, ToolSourceRefError, canonical_source_title
from arden.constants import RAW_TOOL_RESULT_INLINE_MAX_BYTES
from arden.core.content import AudioContent, ContentBlock, ImageContent
from arden.core.raw_tool_results import persist_raw_tool_result, preview_text
from arden.tools.core.base import ToolResult

MCP_RESULT_PREVIEW_CHARS = 12_000
MCP_STRUCTURED_SUMMARY_MAX_KEYS = 50
MCP_STRUCTURED_SUMMARY_KEY_MAX_CHARS = 256


@dataclass(frozen=True)
class ContentProjection:
    text: str
    fallback: str
    metadata: tuple[dict[str, Any], ...] = ()
    model_content: tuple[ContentBlock, ...] = ()


def call_tool_result_to_tool_result(
    result: mcp_types.CallToolResult,
    *,
    provider: str,
    tool_name: str,
) -> ToolResult:
    raw_payload = json.dumps(
        result.model_dump(mode="json", by_alias=True), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    oversized = len(raw_payload.encode("utf-8")) > RAW_TOOL_RESULT_INLINE_MAX_BYTES
    projection = _area_content(result.content, include_model_content=not oversized)
    content = _model_content(result, projection)
    data = _bounded_metadata(result, projection, raw_payload) if oversized else _metadata(result, projection)
    return ToolResult(
        content=preview_text(content, limit=MCP_RESULT_PREVIEW_CHARS) if oversized else content,
        preview=content[:100] if content else "Empty result",
        is_error=result.is_error,
        data=data,
        model_content=projection.model_content,
        source_refs=extract_mcp_source_refs(result, provider=provider, tool_name=tool_name),
    )


def mcp_exception_result(error: Exception, *, provider: str, tool_name: str) -> ToolResult:
    retryable = isinstance(error, (TimeoutError, ConnectionError))
    return ToolResult.failure(
        code="mcp_provider_error",
        message=f"MCP provider request failed for {provider}/{tool_name}.",
        preview="MCP error",
        retryable=retryable,
        recovery_action="Check the MCP server connection and retry." if retryable else "Inspect the MCP server logs.",
    )


def mcp_source_envelope_error(error: ToolSourceRefError) -> ToolResult:
    return ToolResult.failure(
        code="invalid_response",
        message="MCP provider returned invalid source metadata.",
        preview="Invalid MCP response",
        recovery_action="Fix the MCP server result schema before retrying.",
        data={"detail": str(error)},
    )


def extract_mcp_source_refs(
    result: mcp_types.CallToolResult,
    *,
    provider: str,
    tool_name: str,
) -> tuple[ToolSourceRef, ...]:
    refs: list[ToolSourceRef] = []
    seen: set[tuple[str, str]] = set()

    def add(candidate: ToolSourceRef) -> None:
        identity = (candidate.provider, candidate.ref)
        if identity in seen:
            return
        if len(refs) >= 50:
            raise ToolSourceRefError("MCP source references exceed 50 items")
        seen.add(identity)
        refs.append(candidate)

    for block in result.content:
        match block:
            case mcp_types.ResourceLink():
                uri = str(block.uri)
                add(
                    ToolSourceRef(
                        provider=provider,
                        kind="resource",
                        ref=uri,
                        title=canonical_source_title(block.title or block.name, fallback=uri),
                        url=_resource_http_url(uri),
                    )
                )
            case mcp_types.EmbeddedResource():
                uri = str(block.resource.uri)
                add(
                    ToolSourceRef(
                        provider=provider,
                        kind="resource",
                        ref=uri,
                        title=canonical_source_title(uri, fallback="MCP resource"),
                        url=_resource_http_url(uri),
                    )
                )

    structured = result.structured_content
    if tool_name == "search" and isinstance(structured, Mapping):
        results = structured.get("results")
        if not isinstance(results, list):
            raise ToolSourceRefError("MCP search results must be an array")
        for item in results:
            if not isinstance(item, Mapping):
                raise ToolSourceRefError("MCP search results must be objects")
            source_id = _required_mcp_source_text(item, "id")
            title = canonical_source_title(_required_mcp_source_text(item, "title"), fallback=source_id)
            url = item.get("url")
            if url is not None and not isinstance(url, str):
                raise ToolSourceRefError("MCP source url must be a string")
            add(
                ToolSourceRef(
                    provider=provider,
                    kind="search_result",
                    ref=source_id,
                    title=title,
                    url=url,
                )
            )
    elif tool_name == "fetch" and isinstance(structured, Mapping):
        text = structured.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ToolSourceRefError("MCP fetch text must be a non-blank string")
        source_id = _required_mcp_source_text(structured, "id")
        title = canonical_source_title(_required_mcp_source_text(structured, "title"), fallback=source_id)
        url = structured.get("url")
        if url is not None and not isinstance(url, str):
            raise ToolSourceRefError("MCP source url must be a string")
        add(
            ToolSourceRef(
                provider=provider,
                kind="document",
                ref=source_id,
                title=title,
                url=url,
            )
        )

    return tuple(refs)


def _required_mcp_source_text(value: Mapping[str, object], field: str) -> str:
    field_value = value.get(field)
    if not isinstance(field_value, str):
        raise ToolSourceRefError(f"MCP source {field} must be a string")
    if not field_value.strip():
        raise ToolSourceRefError(f"MCP source {field} must be non-blank")
    return field_value


def _resource_http_url(uri: str) -> str | None:
    return uri if uri.startswith(("http://", "https://")) else None


def _model_content(result: mcp_types.CallToolResult, projection: ContentProjection) -> str:
    if projection.text:
        return projection.text
    if projection.fallback:
        return projection.fallback
    if result.structured_content is not None:
        return json.dumps(result.structured_content, ensure_ascii=False, sort_keys=True)
    return ""


def _area_content(blocks: list[mcp_types.ContentBlock], *, include_model_content: bool = True) -> ContentProjection:
    text: list[str] = []
    fallback: list[str] = []
    metadata: list[dict[str, Any]] = []
    model_content: list[ContentBlock] = []

    for block in blocks:
        match block:
            case mcp_types.TextContent():
                text.append(block.text)
            case mcp_types.EmbeddedResource(resource=mcp_types.TextResourceContents() as resource):
                text.append(resource.text)
                metadata.append(_resource_metadata(resource))
            case mcp_types.ImageContent():
                fallback.append("[image content]")
                metadata.append(_media_metadata("image", block.mime_type, block.data))
                if include_model_content:
                    model_content.append(ImageContent(media_type=block.mime_type, data=block.data))
            case mcp_types.AudioContent():
                fallback.append("[audio content]")
                metadata.append(_media_metadata("audio", block.mime_type, block.data))
                if include_model_content:
                    model_content.append(AudioContent(media_type=block.mime_type, data=block.data))
            case mcp_types.ResourceLink():
                fallback.append(f"[resource: {block.uri}]")
                metadata.append(_resource_link_metadata(block))
            case mcp_types.EmbeddedResource():
                fallback.append(f"[resource: {block.resource.uri}]")
                metadata.append(_resource_metadata(block.resource))

    return ContentProjection(
        text="\n".join(part for part in text if part),
        fallback="\n".join(part for part in fallback if part),
        metadata=tuple(metadata),
        model_content=tuple(model_content),
    )


def _bounded_metadata(
    result: mcp_types.CallToolResult,
    projection: ContentProjection,
    raw_payload: str,
) -> dict[str, Any]:
    blob = persist_raw_tool_result(raw_payload)
    data: dict[str, Any] = {
        "truncated": True,
        "raw_ref": blob.blob_ref,
        "raw_bytes": blob.content_bytes,
        **blob.to_internal_data(),
    }
    structured = result.structured_content
    if isinstance(structured, Mapping):
        summary_items = tuple(islice(structured.items(), MCP_STRUCTURED_SUMMARY_MAX_KEYS))
        data["structured_summary"] = {
            "keys": sorted(str(key)[:MCP_STRUCTURED_SUMMARY_KEY_MAX_CHARS] for key, _ in summary_items),
            "counts": {
                str(key)[:MCP_STRUCTURED_SUMMARY_KEY_MAX_CHARS]: len(value)
                for key, value in summary_items
                if isinstance(value, (list, dict, tuple))
            },
        }
        next_cursor = structured.get("next_cursor")
        if isinstance(next_cursor, (str, int, float, bool)) or next_cursor is None:
            if "next_cursor" in structured:
                data["next_cursor"] = next_cursor
    if projection.metadata:
        data["content"] = list(projection.metadata)
    return data


def _metadata(result: mcp_types.CallToolResult, projection: ContentProjection) -> dict | None:
    data = {}
    if result.structured_content is not None:
        data["structuredContent"] = result.structured_content
    if result.meta is not None:
        data["_meta"] = result.meta
    if projection.metadata:
        data["content"] = list(projection.metadata)
    return data or None


def _media_metadata(kind: str, mime_type: str, data: str) -> dict[str, Any]:
    return {
        "type": kind,
        "mimeType": mime_type,
        "base64Length": len(data),
    }


def _resource_link_metadata(block: mcp_types.ResourceLink) -> dict[str, Any]:
    data: dict[str, Any] = {
        "type": "resource_link",
        "uri": str(block.uri),
        "name": block.name,
    }
    if block.title:
        data["title"] = block.title
    if block.mime_type:
        data["mimeType"] = block.mime_type
    if block.size is not None:
        data["size"] = block.size
    return data


def _resource_metadata(resource: mcp_types.TextResourceContents | mcp_types.BlobResourceContents) -> dict[str, Any]:
    data: dict[str, Any] = {
        "type": "resource",
        "uri": str(resource.uri),
    }
    if resource.mime_type:
        data["mimeType"] = resource.mime_type
    if isinstance(resource, mcp_types.BlobResourceContents):
        data["base64Length"] = len(resource.blob)
    return data
