import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class TextContent(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageContent(BaseModel):
    type: Literal["image"] = "image"
    media_type: str
    data: str


class ContextContent(BaseModel):
    type: Literal["context"] = "context"
    content_type: str
    content: str | None = None
    metadata: dict[str, str] | None = None


class ContextManifestEntry(BaseModel):
    context_id: str
    content_type: str
    source: str
    ref: str
    freshness: str
    selection_reason: str
    size_bytes: int


def context_manifest_entry(
    *,
    content_type: str,
    content: str,
    source: str,
    ref: str,
    freshness: str,
    selection_reason: str,
) -> ContextManifestEntry:
    identity = f"{content_type}\0{source}\0{ref}\0{content}".encode()
    return ContextManifestEntry(
        context_id=f"ctx_{hashlib.sha256(identity).hexdigest()[:16]}",
        content_type=content_type,
        source=source,
        ref=ref,
        freshness=freshness,
        selection_reason=selection_reason,
        size_bytes=len(content.encode()),
    )


ContentBlock = Annotated[
    TextContent | ImageContent | ContextContent,
    Field(discriminator="type"),
]

MessageContent = str | list[ContentBlock]

_BLOCK_MAP: dict[str, type[BaseModel]] = {
    "text": TextContent,
    "image": ImageContent,
    "context": ContextContent,
}


def parse_block(raw: dict) -> ContentBlock:
    cls = _BLOCK_MAP.get(raw.get("type", ""))
    if cls:
        return cls.model_validate(raw)
    return TextContent(text=str(raw))


def render_context(ctx: ContextContent | dict) -> str:
    if isinstance(ctx, dict):
        ctx = ContextContent.model_validate(ctx)
    tag = ctx.content_type
    attrs = ""
    if ctx.metadata:
        attrs = " " + " ".join(f'{k}="{v}"' for k, v in ctx.metadata.items())
    if ctx.content:
        return f"<{tag}{attrs}>\n{ctx.content}\n</{tag}>"
    return f"<{tag}{attrs} />"


def blocks_to_text(content: str | list | None) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            block = parse_block(block)
        match block:
            case TextContent():
                parts.append(block.text)
            case ContextContent():
                parts.append(render_context(block))
    return "\n\n".join(parts)
