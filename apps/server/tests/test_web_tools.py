from datetime import UTC, datetime

import pytest
from ddgs.exceptions import DDGSException

from ntrp.context.models import SessionState
from ntrp.integrations.web import ddgs as ddgs_module
from ntrp.integrations.web.ddgs import DDGSWebSource
from ntrp.integrations.web.exceptions import NoSearchResultsException, WebSearchProviderException
from ntrp.integrations.web.tools import WebFetchInput, WebSearchInput, web_fetch, web_search
from ntrp.integrations.web.types import WebContentResult, WebSearchResult
from ntrp.tools.core.context import IOBridge, RunContext, ToolContext, ToolExecution
from ntrp.tools.core.registry import ToolRegistry


class FakeWebSource:
    name = "web"
    provider = "test"

    def __init__(
        self,
        results: list[WebSearchResult] | None = None,
        error: Exception | None = None,
        contents: list[WebContentResult] | None = None,
    ):
        self.results = results or []
        self.error = error
        self.contents = contents or []

    def search_with_details(
        self,
        query: str,
        num_results: int,
        category: str | None,
    ) -> list[WebSearchResult]:
        if self.error:
            raise self.error
        return self.results

    def get_contents(self, urls: list[str]) -> list[WebContentResult]:
        return self.contents


def _execution(source: FakeWebSource) -> ToolExecution:
    return ToolExecution(
        tool_id="call-1",
        tool_name="web_search",
        ctx=ToolContext(
            session_state=SessionState(session_id="session-1", started_at=datetime(2026, 5, 24, tzinfo=UTC)),
            registry=ToolRegistry(),
            run=RunContext(run_id="run-1"),
            io=IOBridge(),
            services={"web": source},
        ),
    )


@pytest.mark.asyncio
async def test_web_search_tells_model_to_simplify_empty_queries():
    result = await web_search(
        _execution(FakeWebSource()),
        WebSearchInput(query='"latest exact phrase" AND obscure term', num_results=5),
    )

    assert result.is_error is False
    assert result.preview == "0 results"
    assert "No results" in result.content
    assert "simpler or broader query" in result.content


@pytest.mark.asyncio
async def test_web_search_self_reports_each_result_source():
    source = FakeWebSource(
        results=[
            WebSearchResult(title="First result", url="https://example.com/first"),
            WebSearchResult(title="   ", url="https://example.com/second"),
        ]
    )

    result = await web_search(_execution(source), WebSearchInput(query="examples", num_results=5))

    assert [ref.to_dict() for ref in result.source_refs] == [
        {
            "provider": "web",
            "kind": "page",
            "ref": "https://example.com/first",
            "title": "First result",
            "url": "https://example.com/first",
        },
        {
            "provider": "web",
            "kind": "page",
            "ref": "https://example.com/second",
            "title": "https://example.com/second",
            "url": "https://example.com/second",
        },
    ]


@pytest.mark.asyncio
async def test_web_fetch_self_reports_source_refs():
    source = FakeWebSource(
        contents=[WebContentResult(title="Example Page", url="https://example.com/canonical", text="body text")]
    )
    result = await web_fetch(_execution(source), WebFetchInput(url="https://example.com/x"))

    assert result.is_error is False
    assert [ref.to_dict() for ref in result.source_refs] == [
        {
            "provider": "web",
            "kind": "page",
            "ref": "https://example.com/canonical",
            "title": "Example Page",
            "url": "https://example.com/canonical",
        }
    ]


@pytest.mark.asyncio
async def test_web_fetch_uses_url_when_page_title_is_blank():
    source = FakeWebSource(contents=[WebContentResult(title="   ", url="https://example.com/x", text="body text")])

    result = await web_fetch(_execution(source), WebFetchInput(url="https://example.com/x"))

    assert result.source_refs[0].title == "https://example.com/x"


@pytest.mark.asyncio
async def test_web_search_treats_no_search_results_exception_as_empty_result():
    result = await web_search(
        _execution(FakeWebSource(error=NoSearchResultsException("empty search"))),
        WebSearchInput(query="too specific query", num_results=5),
    )

    assert result.is_error is False
    assert result.preview == "0 results"
    assert "No results" in result.content
    assert "1-3 core keywords" in result.content


@pytest.mark.asyncio
async def test_web_search_keeps_provider_exceptions_as_errors_even_if_message_says_no_results():
    result = await web_search(
        _execution(FakeWebSource(error=RuntimeError("No results found for query"))),
        WebSearchInput(query="too specific query", num_results=5),
    )

    assert result.is_error is True
    assert result.preview == "Search failed"
    assert "No results found for query" in result.content


@pytest.mark.asyncio
async def test_web_search_keeps_real_provider_errors_as_errors():
    result = await web_search(
        _execution(FakeWebSource(error=RuntimeError("backend unavailable"))),
        WebSearchInput(query="normal query", num_results=5),
    )

    assert result.is_error is True
    assert result.preview == "Search failed"
    assert "backend unavailable" in result.content


@pytest.mark.asyncio
async def test_web_search_sanitizes_provider_failures():
    raw_error = "('error sending request for url (https://html.duckduckgo.com/html/)', 'https://html.duckduckgo.com/html/')"
    result = await web_search(
        _execution(FakeWebSource(error=WebSearchProviderException("DuckDuckGo request failed."))),
        WebSearchInput(query="normal query", num_results=5),
    )

    assert result.is_error is True
    assert result.preview == "Search failed"
    assert "DuckDuckGo request failed" in result.content
    assert "provider/network failure" in result.content
    assert raw_error not in result.content


def test_ddgs_web_source_raises_no_search_results_for_exact_sentinel(monkeypatch):
    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def text(self, query: str, max_results: int):
            raise DDGSException("No results found.")

    monkeypatch.setattr(ddgs_module, "DDGS", FakeDDGS)

    with pytest.raises(NoSearchResultsException):
        DDGSWebSource().search_with_details("too specific", 5, None)


def test_ddgs_web_source_raises_provider_exception_for_request_failures(monkeypatch):
    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def text(self, query: str, max_results: int):
            raise DDGSException(
                "RuntimeError: error sending request for url (https://html.duckduckgo.com/html/)"
            )

    monkeypatch.setattr(ddgs_module, "DDGS", FakeDDGS)

    with pytest.raises(WebSearchProviderException, match="DuckDuckGo request failed"):
        DDGSWebSource().search_with_details("normal query", 5, None)
