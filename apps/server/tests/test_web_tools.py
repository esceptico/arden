import hashlib
from datetime import UTC, datetime

import httpx
import pytest

import arden.core.tool_result_files as tool_result_files
from arden.constants import OFFLOAD_THRESHOLD, WEB_SEARCH_MAX_RESULTS
from arden.context.models import SessionState
from arden.core.tool_executor import ArdenToolExecutor
from arden.integrations.web import ddgs as ddgs_module
from arden.integrations.web.ddgs import DDGSWebSource
from arden.integrations.web.exceptions import NoSearchResultsException, WebSearchProviderException
from arden.integrations.web.tools import WebFetchInput, WebSearchInput, web_fetch, web_search, web_search_tool
from arden.integrations.web.types import WebContentResult, WebSearchResult
from arden.tools.core import ToolResult
from arden.tools.core.context import IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry
from arden.tools.executor import ToolExecutor


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


def _execution(source: FakeWebSource, registry: ToolRegistry | None = None) -> ToolExecution:
    return ToolExecution(
        tool_id="call-1",
        tool_name="web_search",
        ctx=ToolContext(
            session_state=SessionState(session_id="session-1", started_at=datetime(2026, 5, 24, tzinfo=UTC)),
            registry=registry or ToolRegistry(),
            run=RunContext(run_id="run-1"),
            io=IOBridge(),
            services={"web": source},
        ),
    )


def _oversized_search_source() -> FakeWebSource:
    huge = 'line\n"😀' * 5_000
    return FakeWebSource(
        results=[
            WebSearchResult(
                title=huge,
                url=f"https://example.com/{index}/" + "a" * 3_900,
                published_date=huge,
                summary=huge,
                highlights=[huge] * 8,
            )
            for index in range(WEB_SEARCH_MAX_RESULTS)
        ]
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
async def test_web_search_renders_readable_bounded_result_fields():
    source = FakeWebSource(
        results=[
            WebSearchResult(
                title=' First\nresult "quoted" ',
                url="https://example.com/first",
                published_date="2026-08-05\nUTC",
                summary="Useful\nsummary with\x00 a control.",
                highlights=["One\nline", "Second", "Third", "Ignored fourth"],
            )
        ]
    )

    result = await web_search(_execution(source), WebSearchInput(query="examples", limit=5))

    assert result.content == (
        'Web results for: examples\n1. First result "quoted"\n'
        "   URL: https://example.com/first\n"
        "   Published: 2026-08-05 UTC\n"
        "   Summary: Useful summary with a control.\n"
        "   Highlight 1: One line\n"
        "   Highlight 2: Second\n"
        "   Highlight 3: Third"
    )
    assert not result.content.lstrip().startswith("{")
    assert result.data == {"query": "examples", "count": 1, "may_have_more": False}


@pytest.mark.asyncio
async def test_web_search_slices_provider_over_return_and_exposes_honest_cap():
    source = FakeWebSource(
        results=[WebSearchResult(title=f"Result {index}", url=f"https://example.com/{index}") for index in range(3)]
    )

    result = await web_search(_execution(source), WebSearchInput(query="examples", limit=2))

    assert "1. Result 0" in result.content
    assert "2. Result 1" in result.content
    assert "Result 2" not in result.content
    assert "more may exist" in result.content
    assert result.preview == "2 results (possibly capped)"
    assert result.data == {"query": "examples", "count": 2, "may_have_more": True}
    assert [ref.ref for ref in result.source_refs] == ["https://example.com/0", "https://example.com/1"]


@pytest.mark.asyncio
async def test_web_search_pathological_fields_preserve_exact_payload_for_executor_offload():
    result = await web_search(
        _execution(_oversized_search_source()),
        WebSearchInput(query="x" * 2_000, limit=WEB_SEARCH_MAX_RESULTS),
    )

    assert result.content.count("Highlight 3:") == WEB_SEARCH_MAX_RESULTS
    assert "Highlight 4:" not in result.content
    assert "Raise limit" not in result.content
    assert len(result.serialized_payload().encode()) > OFFLOAD_THRESHOLD
    assert len(result.source_refs) == WEB_SEARCH_MAX_RESULTS
    assert all(ref.ref.startswith("url-sha256:") for ref in result.source_refs)
    assert all(ref.url and len(ref.url) > 3_900 for ref in result.source_refs)
    assert result.data == {
        "query": "x" * 2_000,
        "count": WEB_SEARCH_MAX_RESULTS,
        "may_have_more": True,
    }


@pytest.mark.asyncio
async def test_web_search_executor_bounds_long_source_urls_without_losing_exact_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_result_files, "RESULTS_BASE", tmp_path / "tool-results")
    registry = ToolRegistry()
    registry.register("web_search", web_search_tool, source="web")
    execution = _execution(_oversized_search_source(), registry)
    executor = ArdenToolExecutor(ToolExecutor().with_registry(registry), execution.ctx)

    result = await executor.execute(
        "web_search",
        {"query": "x" * 2_000, "limit": WEB_SEARCH_MAX_RESULTS},
        "call-long-web-search",
    )

    assert len(result.serialized_payload().encode("utf-8")) <= OFFLOAD_THRESHOLD
    assert result.data["source_refs_compacted"] is True
    assert result.data["source_refs_total"] == WEB_SEARCH_MAX_RESULTS
    assert result.data["source_refs_included"] == WEB_SEARCH_MAX_RESULTS
    assert result.data["source_ref_urls_omitted"] > 0
    assert "file_read" in result.content

    exact = ToolResult.from_serialized_payload(
        tool_result_files.result_payload_file_path("session-1", "call-long-web-search").read_text()
    )
    assert exact is not None
    assert len(exact.source_refs) == WEB_SEARCH_MAX_RESULTS
    assert all(ref.url and len(ref.url) > 3_900 for ref in exact.source_refs)


@pytest.mark.asyncio
async def test_web_search_omits_credentials_and_private_url_parameters():
    source = FakeWebSource(
        results=[
            WebSearchResult(title="Credential", url="https://user:password@example.com/private"),
            WebSearchResult(title="Parameterized", url="https://example.com/item?id=123"),
        ]
    )

    result = await web_search(_execution(source), WebSearchInput(query="private", limit=5))
    serialized = result.serialized_payload()

    assert "user:password" not in result.content
    assert "user:password" not in serialized
    assert "omitted (invalid or credential-bearing URL)" in result.content
    assert "https://example.com/item" in result.content
    assert "id=123" not in serialized
    assert "Query and fragment parameters omitted for privacy" in result.content


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
async def test_web_fetch_keeps_clickable_provenance_for_long_public_url():
    long_url = "https://example.com/" + "a" * 2_100
    source = FakeWebSource(contents=[WebContentResult(title="Long page", url=long_url, text="body text")])

    result = await web_fetch(_execution(source), WebFetchInput(url=long_url))

    assert result.source_refs[0].ref.startswith("url-sha256:")
    assert result.source_refs[0].url == long_url


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
    assert "super-secret" not in result.content
    assert "super-secret" not in result.serialized_payload()
    assert "https://example.com/private" in result.content
    assert "Query and fragment parameters omitted for privacy" in result.content


@pytest.mark.asyncio
async def test_web_search_never_persists_unknown_or_camel_case_query_secrets():
    source = FakeWebSource(
        results=[
            WebSearchResult(
                title="Drive result",
                url="https://drive.google.com/open?id=doc&resourcekey=TOP_SECRET",
            ),
            WebSearchResult(
                title="OAuth result",
                url="https://example.com/callback?accessToken=super-secret&state=private-state",
            ),
        ]
    )

    result = await web_search(_execution(source), WebSearchInput(query="private", limit=5))
    serialized = result.serialized_payload()

    assert "TOP_SECRET" not in serialized
    assert "super-secret" not in serialized
    assert "private-state" not in serialized
    assert "https://drive.google.com/open" in result.content
    assert "https://example.com/callback" in result.content


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
