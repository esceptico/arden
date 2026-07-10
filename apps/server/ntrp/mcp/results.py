import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mcp import types as mcp_types

from ntrp.agent.types.tools import ToolSourceRef, normalize_source_refs
from ntrp.tools.core.base import ToolResult


@dataclass(frozen=True)
class ContentProjection:
    text: str
    fallback: str
    metadata: tuple[dict[str, Any], ...] = ()


def call_tool_result_to_tool_result(
    result: mcp_types.CallToolResult,
    *,
    provider: str,
    tool_name: str,
) -> ToolResult:
    projection = _project_content(result.content)
    content = _model_content(result, projection)
    return ToolResult(
        content=content,
        preview=content[:100] if content else "Empty result",
        is_error=bool(result.isError),
        data=_metadata(result, projection),
        source_refs=extract_mcp_source_refs(result, provider=provider, tool_name=tool_name),
    )


def extract_mcp_source_refs(
    result: mcp_types.CallToolResult,
    *,
    provider: str,
    tool_name: str,
) -> tuple[ToolSourceRef, ...]:
    refs: list[ToolSourceRef] = []
    seen: set[tuple[str, str]] = set()

    def add(candidate: ToolSourceRef) -> bool:
        normalized = normalize_source_refs((candidate,))
        if not normalized:
            return False
        source = normalized[0]
        identity = (source.provider, source.ref)
        if identity in seen:
            return False
        seen.add(identity)
        refs.append(source)
        return len(refs) == 50

    for block in result.content:
        match block:
            case mcp_types.ResourceLink():
                uri = str(block.uri)
                if add(
                    ToolSourceRef(
                        provider=provider,
                        kind="resource",
                        ref=uri,
                        title=_first_nonempty(block.title, block.name, uri),
                        url=uri,
                    )
                ):
                    return tuple(refs)
            case mcp_types.EmbeddedResource():
                uri = str(block.resource.uri)
                if add(
                    ToolSourceRef(
                        provider=provider,
                        kind="resource",
                        ref=uri,
                        title=uri,
                        url=uri,
                    )
                ):
                    return tuple(refs)

    structured = result.structuredContent
    if tool_name == "search" and isinstance(structured, Mapping):
        results = structured.get("results")
        if isinstance(results, list):
            for item in results:
                if not isinstance(item, Mapping):
                    continue
                source_id = item.get("id")
                title = item.get("title")
                if not isinstance(source_id, str) or not isinstance(title, str):
                    continue
                url = item.get("url")
                if add(
                    ToolSourceRef(
                        provider=provider,
                        kind="search_result",
                        ref=source_id,
                        title=_first_nonempty(title, source_id),
                        url=url if isinstance(url, str) else None,
                    )
                ):
                    return tuple(refs)
    elif tool_name == "fetch" and isinstance(structured, Mapping):
        source_id = structured.get("id")
        title = structured.get("title")
        text = structured.get("text")
        if isinstance(source_id, str) and isinstance(title, str) and isinstance(text, str) and text.strip():
            url = structured.get("url")
            add(
                ToolSourceRef(
                    provider=provider,
                    kind="document",
                    ref=source_id,
                    title=_first_nonempty(title, source_id),
                    url=url if isinstance(url, str) else None,
                )
            )

    return tuple(refs)


def _first_nonempty(*values: str | None) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return "resource"


def _model_content(result: mcp_types.CallToolResult, projection: ContentProjection) -> str:
    if projection.text:
        return projection.text
    if projection.fallback:
        return projection.fallback
    if result.structuredContent is not None:
        return json.dumps(result.structuredContent, ensure_ascii=False, sort_keys=True)
    return ""


def _project_content(blocks: list[mcp_types.ContentBlock]) -> ContentProjection:
    text: list[str] = []
    fallback: list[str] = []
    metadata: list[dict[str, Any]] = []

    for block in blocks:
        match block:
            case mcp_types.TextContent():
                text.append(block.text)
            case mcp_types.EmbeddedResource(resource=mcp_types.TextResourceContents() as resource):
                text.append(resource.text)
                metadata.append(_resource_metadata(resource))
            case mcp_types.ImageContent():
                fallback.append("[image content]")
                metadata.append(_media_metadata("image", block.mimeType, block.data))
            case mcp_types.AudioContent():
                fallback.append("[audio content]")
                metadata.append(_media_metadata("audio", block.mimeType, block.data))
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
    )


def _metadata(result: mcp_types.CallToolResult, projection: ContentProjection) -> dict | None:
    data = {}
    if result.structuredContent is not None:
        data["structuredContent"] = result.structuredContent
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
    if block.mimeType:
        data["mimeType"] = block.mimeType
    if block.size is not None:
        data["size"] = block.size
    return data


def _resource_metadata(resource: mcp_types.TextResourceContents | mcp_types.BlobResourceContents) -> dict[str, Any]:
    data: dict[str, Any] = {
        "type": "resource",
        "uri": str(resource.uri),
    }
    if resource.mimeType:
        data["mimeType"] = resource.mimeType
    if isinstance(resource, mcp_types.BlobResourceContents):
        data["base64Length"] = len(resource.blob)
    return data
