import asyncio
import hashlib
from enum import StrEnum

from pydantic import BaseModel, Field

from arden.agent.types.tools import ToolSourceRef, normalize_source_refs
from arden.constants import WEB_SEARCH_MAX_RESULTS
from arden.integrations.tool_errors import operation_error_result
from arden.integrations.web.exceptions import WebFetchProviderException, WebSearchProviderException
from arden.integrations.web.types import (
    PUBLIC_WEB_URL_MAX_CHARS,
    InvalidPublicWebUrl,
    PublicWebUrl,
    WebClient,
    WebSearchResult,
)
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope
from arden.utils import truncate

WEB_SEARCH_DESCRIPTION = "Search the web for information. Returns titles, URLs, and content snippets."

WEB_FETCH_DESCRIPTION = "Fetch content from a URL. Returns the page text in readable format."


class WebSearchCategory(StrEnum):
    company = "company"
    research_paper = "research paper"
    news = "news"
    pdf = "pdf"
    github = "github"
    tweet = "tweet"


_DEFAULT_SEARCH_RESULTS = 5
_SEARCH_TITLE_MAX_BYTES = 180
_SEARCH_DATE_MAX_CHARS = 80
_SEARCH_SUMMARY_MAX_CHARS = 500
_SEARCH_HIGHLIGHT_MAX_CHARS = 240
_SOURCE_REF_MAX_CHARS = 2_048
_SEARCH_QUERY_DISPLAY_MAX_CHARS = 240


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    marker = "..."
    return encoded[: max_bytes - len(marker)].decode("utf-8", errors="ignore").rstrip() + marker


def _single_line(value: str | None, max_chars: int) -> str:
    if not value:
        return ""
    without_controls = "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in value)
    return truncate(" ".join(without_controls.split()), max_chars)


def _single_line_bytes(value: str | None, max_bytes: int) -> str:
    return _truncate_utf8(_single_line(value, max_bytes), max_bytes)


def _web_page_source(
    url: PublicWebUrl,
    title: str | None,
) -> ToolSourceRef:
    raw_url = str(url)
    display_title = _single_line_bytes(title, _SEARCH_TITLE_MAX_BYTES)
    digest = hashlib.sha256(raw_url.encode()).hexdigest()
    return ToolSourceRef(
        provider="web",
        kind="page",
        ref=raw_url if len(raw_url) <= _SOURCE_REF_MAX_CHARS else f"url-sha256:{digest}",
        title=display_title or raw_url,
        url=raw_url,
    )


def _format_search_results(
    query: str,
    results: list[tuple[WebSearchResult, PublicWebUrl]],
    *,
    may_have_more: bool,
) -> str:
    lines = [f"Web results for: {_single_line(query, _SEARCH_QUERY_DISPLAY_MAX_CHARS)}"]
    for index, (result, url) in enumerate(results, start=1):
        title = _single_line_bytes(result.title, _SEARCH_TITLE_MAX_BYTES) or "Untitled result"
        lines.extend((f"{index}. {title}", f"   URL: {url}"))
        if published := _single_line(result.published_date, _SEARCH_DATE_MAX_CHARS):
            lines.append(f"   Published: {published}")
        if summary := _single_line(result.summary, _SEARCH_SUMMARY_MAX_CHARS):
            lines.append(f"   Summary: {summary}")
        for highlight_index, highlight in enumerate(result.highlights or (), start=1):
            if highlight_index > 3:
                break
            if text := _single_line(highlight, _SEARCH_HIGHLIGHT_MAX_CHARS):
                lines.append(f"   Highlight {highlight_index}: {text}")
    if may_have_more:
        recovery = (
            f"Raise limit (max {WEB_SEARCH_MAX_RESULTS}) or narrow the query"
            if len(results) < WEB_SEARCH_MAX_RESULTS
            else "Narrow the query"
        )
        lines.append(f"Showing {len(results)} results; more may exist. {recovery} to continue.")
    return "\n".join(lines)


def _empty_search_content(query: str) -> str:
    display_query = _single_line(query, _SEARCH_QUERY_DISPLAY_MAX_CHARS)
    return (
        f'No results for "{display_query}". '
        "Try again with a simpler or broader query: remove quotes/operators, use fewer words, "
        "or search 1-3 core keywords."
    )


class WebSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=2_000, description="The search query")
    limit: int = Field(
        default=_DEFAULT_SEARCH_RESULTS,
        ge=1,
        le=WEB_SEARCH_MAX_RESULTS,
        description=f"Number of results (default: {_DEFAULT_SEARCH_RESULTS}, max: {WEB_SEARCH_MAX_RESULTS})",
    )
    category: WebSearchCategory | None = Field(
        default=None,
        description="Filter by category: company, research paper, news, pdf, github, tweet",
    )


async def web_search(execution: ToolExecution, args: WebSearchInput) -> ToolResult:
    source = execution.ctx.get_client("web", WebClient)
    try:
        results = await asyncio.to_thread(
            source.search_with_details,
            query=args.query,
            limit=args.limit,
            category=args.category,
        )
    except WebSearchProviderException as error:
        return operation_error_result(error, preview="Search failed")

    selected = list(results[: args.limit])
    if not selected:
        return ToolResult(content=_empty_search_content(args.query), preview="0 results")
    try:
        public_results = [(result, PublicWebUrl.parse(result.url)) for result in selected]
    except InvalidPublicWebUrl as error:
        return ToolResult.failure(
            code="provider_error",
            message=f"Web provider returned an invalid result URL: {error}",
            preview="Search failed",
        )

    may_have_more = len(results) >= args.limit
    return ToolResult(
        content=_format_search_results(args.query, public_results, may_have_more=may_have_more),
        preview=f"{len(selected)} results" + (" (possibly capped)" if may_have_more else ""),
        data={"query": args.query, "count": len(selected), "may_have_more": may_have_more},
        source_refs=normalize_source_refs(_web_page_source(url, result.title) for result, url in public_results),
    )


class WebFetchInput(BaseModel):
    url: str = Field(min_length=1, max_length=PUBLIC_WEB_URL_MAX_CHARS, description="The URL to fetch")


async def web_fetch(execution: ToolExecution, args: WebFetchInput) -> ToolResult:
    try:
        requested_url = PublicWebUrl.parse(args.url)
    except InvalidPublicWebUrl as error:
        return ToolResult.failure(
            code="invalid_ref",
            message=str(error),
            preview="Invalid url",
            recovery_action="Pass a complete HTTP(S) URL returned by web_search.",
        )

    source = execution.ctx.get_client("web", WebClient)
    try:
        results = await asyncio.to_thread(source.get_contents, [str(requested_url)])
    except WebFetchProviderException as error:
        return operation_error_result(error, preview="Fetch failed")
    if not results:
        return ToolResult.failure(
            code="not_found",
            message="The web provider returned no page for this URL.",
            preview="Fetch failed",
            recovery_action="Run web_search again and use a current result URL.",
        )

    result = results[0]
    try:
        result_url = PublicWebUrl.parse(result.url)
    except InvalidPublicWebUrl as error:
        return ToolResult.failure(
            code="provider_error",
            message=f"Web provider returned an invalid page URL: {error}",
            preview="Fetch failed",
        )
    text = result.text
    if not text or not text.strip():
        return ToolResult.failure(
            code="provider_error",
            message="Web provider returned a page without readable content.",
            preview="Fetch failed",
        )
    lines = text.count("\n") + 1
    output = []
    if result.title:
        output.append(f"Title: {result.title}")
    if result.published_date:
        output.append(f"Date: {result.published_date}")
    if result.author:
        output.append(f"Author: {result.author}")
    output.append("")
    if text:
        output.append(text)
    return ToolResult(
        content="\n".join(output),
        preview=f"Fetched {lines} lines",
        source_refs=normalize_source_refs((_web_page_source(result_url, result.title),)),
    )


web_search_tool = tool(
    display_name="WebSearch",
    display_description="Search public web sources.",
    description=WEB_SEARCH_DESCRIPTION,
    input_model=WebSearchInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"web"})),
    execute=web_search,
)

web_fetch_tool = tool(
    display_name="WebFetch",
    display_description="Fetch a web page as readable text.",
    description=WEB_FETCH_DESCRIPTION,
    input_model=WebFetchInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"web"})),
    execute=web_fetch,
)
