import asyncio
import hashlib
import json
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from ntrp.agent.types.tools import ToolSourceRef, normalize_source_refs
from ntrp.constants import WEB_SEARCH_MAX_RESULTS
from ntrp.integrations.web.exceptions import NoSearchResultsException, WebSearchProviderException
from ntrp.integrations.web.types import WebClient
from ntrp.tools.core import ToolResult, tool
from ntrp.tools.core.context import ToolExecution
from ntrp.tools.core.types import ToolAction, ToolPolicy, ToolScope

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


def _web_page_source(url: str, title: str | None) -> ToolSourceRef | None:
    raw_url = url.strip()
    if not raw_url:
        return None
    display_title = (title or "").strip()
    try:
        parsed = urlsplit(raw_url)
        public_url = (
            parsed.scheme.lower() in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        public_url = False
    if public_url:
        return ToolSourceRef(
            provider="web",
            kind="page",
            ref=raw_url,
            title=display_title or raw_url,
            url=raw_url,
        )
    digest = hashlib.sha256(raw_url.encode()).hexdigest()
    return ToolSourceRef(
        provider="web",
        kind="page",
        ref=f"url-sha256:{digest}",
        title=display_title or "Web page",
    )


def _empty_search_content(query: str) -> str:
    return (
        f'No results for "{query}". '
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

        formatted = []
        for r in results:
            item: dict[str, Any] = {
                "title": r.title,
                "url": r.url,
            }
            if r.published_date:
                item["date"] = r.published_date
            if r.summary:
                item["summary"] = r.summary
            if r.highlights:
                item["highlights"] = r.highlights[:3]
            formatted.append(item)

        if not formatted:
            return ToolResult(content=_empty_search_content(args.query), preview="0 results")

        content = json.dumps({"query": args.query, "results": formatted}, indent=2, ensure_ascii=False)
        return ToolResult(
            content=content,
            preview=f"{len(formatted)} results",
            source_refs=normalize_source_refs(
                source_ref
                for result in results
                if (source_ref := _web_page_source(result.url, result.title)) is not None
            ),
        )

    except NoSearchResultsException:
        return ToolResult(content=_empty_search_content(args.query), preview="0 results")
    except WebSearchProviderException as error:
        return _web_provider_failure("Search failed", safe_message=str(error))
    except Exception:
        return _web_provider_failure("Search failed")


class WebFetchInput(BaseModel):
    url: str = Field(min_length=1, max_length=4_096, description="The URL to fetch")


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
                    (source_ref,) if (source_ref := _web_page_source(r.url, r.title)) is not None else ()
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
    description=WEB_SEARCH_DESCRIPTION,
    input_model=WebSearchInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"web"})),
    execute=web_search,
)

web_fetch_tool = tool(
    display_name="WebFetch",
    description=WEB_FETCH_DESCRIPTION,
    input_model=WebFetchInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.EXTERNAL, permissions=frozenset({"web"})),
    execute=web_fetch,
)
