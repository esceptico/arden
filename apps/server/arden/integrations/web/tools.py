import asyncio
import hashlib
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field

from arden.agent.types.tools import ToolSourceRef, normalize_source_refs
from arden.constants import WEB_SEARCH_MAX_RESULTS
from arden.integrations.web.exceptions import NoSearchResultsException, WebSearchProviderException
from arden.integrations.web.types import WebClient, WebSearchResult
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
_WEB_FETCH_URL_MAX_CHARS = 4_096
_SOURCE_REF_MAX_CHARS = 2_048
_SEARCH_DIRECT_SOURCE_URL_MAX_CHARS = 256
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


def _has_unsafe_url_chars(value: str) -> bool:
    return any(char.isspace() or char in {'"', "\\"} or ord(char) < 32 or ord(char) == 127 for char in value)


def _web_page_source(
    url: str,
    title: str | None,
    *,
    direct_ref_max_chars: int,
    retain_long_url: bool,
) -> ToolSourceRef | None:
    raw_url = url.strip()
    if not raw_url:
        return None
    display_title = _single_line_bytes(title, _SEARCH_TITLE_MAX_BYTES)
    try:
        parsed = urlsplit(raw_url)
        safe_public_url = (
            parsed.scheme.lower() in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and not _has_unsafe_url_chars(raw_url)
        )
    except ValueError:
        safe_public_url = False
    if safe_public_url:
        digest = hashlib.sha256(raw_url.encode()).hexdigest()
        direct_ref = len(raw_url) <= direct_ref_max_chars
        return ToolSourceRef(
            provider="web",
            kind="page",
            ref=raw_url if direct_ref else f"url-sha256:{digest}",
            title=display_title or raw_url,
            url=(raw_url if len(raw_url) <= _WEB_FETCH_URL_MAX_CHARS and (direct_ref or retain_long_url) else None),
        )
    digest = hashlib.sha256(raw_url.encode()).hexdigest()
    return ToolSourceRef(
        provider="web",
        kind="page",
        ref=f"url-sha256:{digest}",
        title=display_title or "Web page",
    )


def _search_page_source(result: WebSearchResult) -> ToolSourceRef | None:
    return _web_page_source(
        result.url,
        result.title,
        direct_ref_max_chars=_SEARCH_DIRECT_SOURCE_URL_MAX_CHARS,
        retain_long_url=True,
    )


def _fetched_page_source(url: str, title: str | None) -> ToolSourceRef | None:
    return _web_page_source(
        url,
        title,
        direct_ref_max_chars=_SOURCE_REF_MAX_CHARS,
        retain_long_url=True,
    )


def _display_url(raw_url: str) -> tuple[str, bool]:
    url = raw_url.strip()
    if not url:
        return "unavailable", False
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or _has_unsafe_url_chars(url)
        ):
            return "omitted (invalid or credential-bearing URL)", False
    except ValueError:
        return "omitted (invalid URL)", False
    public_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    if len(public_url) > _WEB_FETCH_URL_MAX_CHARS:
        return "omitted (URL exceeds the web_fetch input limit)", False
    return public_url, bool(parsed.query or parsed.fragment)


def _format_search_results(
    query: str,
    results: list[WebSearchResult],
    *,
    may_have_more: bool,
) -> str:
    lines = [f"Web results for: {_single_line(query, _SEARCH_QUERY_DISPLAY_MAX_CHARS)}"]
    for index, result in enumerate(results, start=1):
        title = _single_line_bytes(result.title, _SEARCH_TITLE_MAX_BYTES) or "Untitled result"
        display_url, parameters_omitted = _display_url(result.url)
        lines.extend((f"{index}. {title}", f"   URL: {display_url}"))
        if parameters_omitted:
            lines.append("   URL note: Query and fragment parameters omitted for privacy.")
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

        selected = list(results[: args.limit])
        if not selected:
            return ToolResult(content=_empty_search_content(args.query), preview="0 results")

        may_have_more = len(results) >= args.limit
        source_refs = normalize_source_refs(
            source_ref for result in selected if (source_ref := _search_page_source(result)) is not None
        )
        return ToolResult(
            content=_format_search_results(args.query, selected, may_have_more=may_have_more),
            preview=f"{len(selected)} results" + (" (possibly capped)" if may_have_more else ""),
            data={"query": args.query, "count": len(selected), "may_have_more": may_have_more},
            source_refs=source_refs,
        )

    except NoSearchResultsException:
        return ToolResult(content=_empty_search_content(args.query), preview="0 results")
    except WebSearchProviderException as error:
        return _web_provider_failure("Search failed", safe_message=str(error))
    except Exception:
        return _web_provider_failure("Search failed")


class WebFetchInput(BaseModel):
    url: str = Field(min_length=1, max_length=_WEB_FETCH_URL_MAX_CHARS, description="The URL to fetch")


async def web_fetch(execution: ToolExecution, args: WebFetchInput) -> ToolResult:
    if not args.url.startswith(("http://", "https://")):
        return ToolResult.failure(
            code="invalid_ref",
            message="URL must start with http:// or https://.",
            preview="Invalid url",
            recovery_action="Pass a complete HTTP(S) URL returned by web_search.",
        )

    source = execution.ctx.get_client("web", WebClient)
    try:
        results = await asyncio.to_thread(source.get_contents, [args.url])

        if results:
            r = results[0]
            text = r.text or ""
            lines = text.count("\n") + 1
            output = []
            if r.title:
                output.append(f"Title: {r.title}")
            if r.published_date:
                output.append(f"Date: {r.published_date}")
            if r.author:
                output.append(f"Author: {r.author}")
            output.append("")
            if text:
                output.append(text)
            return ToolResult(
                content="\n".join(output),
                preview=f"Fetched {lines} lines",
                source_refs=normalize_source_refs(
                    (source_ref,) if (source_ref := _fetched_page_source(r.url, r.title)) is not None else ()
                ),
            )
        return ToolResult(content="No content fetched. Page may be empty or require JavaScript.", preview="Empty")
    except Exception:
        return _web_provider_failure("Fetch failed")


def _web_provider_failure(preview: str, *, safe_message: str = "The web provider request failed.") -> ToolResult:
    return ToolResult.failure(
        code="provider_error",
        message=safe_message,
        preview=preview,
        retryable=True,
        recovery_action="Retry once; if it repeats, use another provider or report that web access is unavailable.",
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
