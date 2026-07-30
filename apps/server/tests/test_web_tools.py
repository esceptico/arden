import hashlib
from datetime import UTC, datetime

import httpx
import pytest

from arden.context.models import SessionState
from arden.integrations.web import ddgs as ddgs_module
from arden.integrations.web.ddgs import DDGSWebSource
from arden.integrations.web.exceptions import NoSearchResultsException, WebSearchProviderException
from arden.integrations.web.tools import WebFetchInput, WebSearchInput, web_fetch, web_search
from arden.integrations.web.types import WebContentResult, WebSearchResult
from arden.tools.core.context import IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry


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
        limit: int,
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
        WebSearchInput(query='"latest exact phrase" AND obscure term', limit=5),
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

    result = await web_search(_execution(source), WebSearchInput(query="examples", limit=5))

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
async def test_web_search_uses_opaque_identity_for_query_urls_without_persisting_secrets():
    secret_url = "https://example.com/private?signature=super-secret#download"
    source = FakeWebSource(results=[WebSearchResult(title="Private result", url=secret_url)])

    result = await web_search(_execution(source), WebSearchInput(query="private", limit=5))

    expected_ref = f"url-sha256:{hashlib.sha256(secret_url.encode()).hexdigest()}"
    assert [ref.to_dict() for ref in result.source_refs] == [
        {
            "provider": "web",
            "kind": "page",
            "ref": expected_ref,
            "title": "Private result",
        }
    ]
    assert "super-secret" not in repr(result.source_refs)


@pytest.mark.asyncio
async def test_web_fetch_uses_opaque_identity_for_credential_urls_with_generic_title():
    private_url = "https://user:password@example.com/private"
    source = FakeWebSource(contents=[WebContentResult(title="   ", url=private_url, text="body text")])

    result = await web_fetch(_execution(source), WebFetchInput(url="https://example.com/request"))

    expected_ref = f"url-sha256:{hashlib.sha256(private_url.encode()).hexdigest()}"
    assert [ref.to_dict() for ref in result.source_refs] == [
        {
            "provider": "web",
            "kind": "page",
            "ref": expected_ref,
            "title": "Web page",
        }
    ]
    assert "user" not in repr(result.source_refs)
    assert "password" not in repr(result.source_refs)


@pytest.mark.asyncio
async def test_web_search_treats_no_search_results_exception_as_empty_result():
    result = await web_search(
        _execution(FakeWebSource(error=NoSearchResultsException("empty search"))),
        WebSearchInput(query="too specific query", limit=5),
    )

    assert result.is_error is False
    assert result.preview == "0 results"
    assert "No results" in result.content
    assert "1-3 core keywords" in result.content


@pytest.mark.asyncio
async def test_web_search_keeps_provider_exceptions_as_errors_even_if_message_says_no_results():
    result = await web_search(
        _execution(FakeWebSource(error=RuntimeError("No results found for query"))),
        WebSearchInput(query="too specific query", limit=5),
    )

    assert result.is_error is True
    assert result.preview == "Search failed"
    assert result.content == "The web provider request failed."


@pytest.mark.asyncio
async def test_web_search_keeps_real_provider_errors_as_errors():
    result = await web_search(
        _execution(FakeWebSource(error=RuntimeError("backend unavailable"))),
        WebSearchInput(query="normal query", limit=5),
    )

    assert result.is_error is True
    assert result.preview == "Search failed"
    assert result.content == "The web provider request failed."


@pytest.mark.asyncio
async def test_web_search_sanitizes_provider_failures():
    raw_error = (
        "('error sending request for url (https://html.duckduckgo.com/html/)', 'https://html.duckduckgo.com/html/')"
    )
    result = await web_search(
        _execution(FakeWebSource(error=WebSearchProviderException("Web search is temporarily unavailable."))),
        WebSearchInput(query="normal query", limit=5),
    )

    assert result.is_error is True
    assert result.preview == "Search failed"
    assert result.content == "Web search is temporarily unavailable."
    assert raw_error not in result.content


def test_ddgs_web_source_parses_results_and_removes_redirects(monkeypatch):
    response = httpx.Response(
        200,
        content=b"""
            <html><body>
                <div class="result">
                    <a class="result__a">Missing URL</a>
                </div>
                <div class="result results_links">
                    <a
                        class="result__a"
                        href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fitem%3Fx%3D1&amp;rut=tracker"
                    >Example result</a>
                    <div class="result__snippet"> Useful <b>search</b> result. </div>
                </div>
            </body></html>
        """,
    )
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        calls.append((url, kwargs))
        return response

    monkeypatch.setattr(ddgs_module.httpx, "get", fake_get)

    results = DDGSWebSource().search_with_details("example query", 5, None)

    assert results == [
        WebSearchResult(
            title="Example result",
            url="https://example.com/item?x=1",
            summary="Useful search result.",
        )
    ]
    assert calls[0][1]["params"] == {"q": "example query"}


def test_ddgs_web_source_raises_no_search_results_for_empty_page(monkeypatch):
    monkeypatch.setattr(
        ddgs_module.httpx,
        "get",
        lambda *args, **kwargs: httpx.Response(200, content=b"<html><body>No results.</body></html>"),
    )

    with pytest.raises(NoSearchResultsException):
        DDGSWebSource().search_with_details("too specific", 5, None)


def test_ddgs_web_source_raises_provider_exception_for_request_failures(monkeypatch):
    def fail(*args, **kwargs):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(ddgs_module.httpx, "get", fail)

    with pytest.raises(WebSearchProviderException, match="Web search is temporarily unavailable"):
        DDGSWebSource().search_with_details("normal query", 5, None)


def test_ddgs_web_source_treats_challenge_response_as_provider_failure(monkeypatch):
    monkeypatch.setattr(
        ddgs_module.httpx,
        "get",
        lambda *args, **kwargs: httpx.Response(202, content=b"<html><body>Challenge</body></html>"),
    )

    with pytest.raises(WebSearchProviderException, match="Web search is temporarily unavailable"):
        DDGSWebSource().search_with_details("normal query", 5, None)


def test_ddgs_web_source_falls_back_to_lite_search(monkeypatch):
    responses = iter(
        [
            httpx.Response(202, content=b"<html><body>Challenge</body></html>"),
            httpx.Response(
                200,
                content=b"""
                    <html><body><table>
                        <tr>
                            <td>1.</td>
                            <td>
                                <a class="result-link" href="https://example.com/fallback">Fallback result</a>
                            </td>
                        </tr>
                        <tr><td></td><td class="result-snippet">Fallback snippet.</td></tr>
                    </table></body></html>
                """,
            ),
        ]
    )
    calls: list[str] = []

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        calls.append(url)
        return next(responses)

    monkeypatch.setattr(ddgs_module.httpx, "get", fake_get)

    assert DDGSWebSource().search_with_details("normal query", 5, None) == [
        WebSearchResult(
            title="Fallback result",
            url="https://example.com/fallback",
            summary="Fallback snippet.",
        )
    ]
    assert calls == [
        "https://html.duckduckgo.com/html/",
        "https://lite.duckduckgo.com/lite/",
    ]
