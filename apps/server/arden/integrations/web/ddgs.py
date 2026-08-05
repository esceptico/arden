import logging
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from lxml import etree, html
from trafilatura import bare_extraction
from trafilatura.settings import use_config

from arden.integrations.web.exceptions import WebFetchProviderException, WebSearchProviderException
from arden.integrations.web.types import WebContentResult, WebSearchResult

_logger = logging.getLogger(__name__)

_cfg = use_config()
_cfg.set("DEFAULT", "DOWNLOAD_TIMEOUT", "10")
_cfg.set("DEFAULT", "MAX_FILE_SIZE", "1000000")

_SEARCH_URLS = (
    "https://html.duckduckgo.com/html/",
    "https://lite.duckduckgo.com/lite/",
)
_SEARCH_TIMEOUT_SECONDS = 6
_SEARCH_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": "Mozilla/5.0",
}
_RESULT_XPATH = '//div[contains(concat(" ", normalize-space(@class), " "), " result ")]'
_RESULT_LINK_XPATH = './/a[contains(concat(" ", normalize-space(@class), " "), " result__a ")]'
_RESULT_SNIPPET_XPATH = 'string(.//*[contains(concat(" ", normalize-space(@class), " "), " result__snippet ")])'
_LITE_RESULT_LINK_XPATH = '//a[contains(concat(" ", normalize-space(@class), " "), " result-link ")]'
_LITE_RESULT_SNIPPET_XPATH = (
    "string(ancestor::tr[1]/following-sibling::tr[1]"
    '/td[contains(concat(" ", normalize-space(@class), " "), " result-snippet ")])'
)
_PROVIDER_FAILURE = "Web search is temporarily unavailable."
_FETCH_FAILURE = "The web page could not be fetched."


def _guess_title(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc or url


def _direct_result_url(raw_url: str) -> str:
    url = urljoin("https://duckduckgo.com", raw_url)
    parsed = urlparse(url)
    if parsed.hostname not in {"duckduckgo.com", "www.duckduckgo.com"} or parsed.path != "/l/":
        return url
    targets = parse_qs(parsed.query).get("uddg")
    return targets[0] if targets else url


def _parse_search_results(content: bytes, limit: int) -> list[WebSearchResult]:
    document = html.fromstring(content)
    results: list[WebSearchResult] = []
    for row in document.xpath(_RESULT_XPATH):
        links = row.xpath(_RESULT_LINK_XPATH)
        if not links:
            continue
        link = links[0]
        title = link.text_content().strip()
        raw_url = link.get("href")
        if not title or not raw_url:
            continue
        snippet = " ".join(row.xpath(_RESULT_SNIPPET_XPATH).split())
        results.append(
            WebSearchResult(
                title=title,
                url=_direct_result_url(raw_url),
                summary=snippet or None,
            )
        )
        if len(results) == limit:
            return results
    for link in document.xpath(_LITE_RESULT_LINK_XPATH):
        title = link.text_content().strip()
        raw_url = link.get("href")
        if not title or not raw_url:
            continue
        snippet = " ".join(link.xpath(_LITE_RESULT_SNIPPET_XPATH).split())
        results.append(
            WebSearchResult(
                title=title,
                url=_direct_result_url(raw_url),
                summary=snippet or None,
            )
        )
        if len(results) == limit:
            return results
    return results


class DDGSWebSource:
    name = "web"
    provider = "ddgs"

    def search_with_details(
        self,
        query: str,
        limit: int = 5,
        category: str | None = None,
    ) -> list[WebSearchResult]:
        del category
        completed_request = False
        for search_url in _SEARCH_URLS:
            try:
                response = httpx.get(
                    search_url,
                    params={"q": query},
                    headers=_SEARCH_HEADERS,
                    timeout=_SEARCH_TIMEOUT_SECONDS,
                    follow_redirects=True,
                )
                if response.status_code != httpx.codes.OK:
                    _logger.warning("Web search returned HTTP %s from %s", response.status_code, search_url)
                    continue
                completed_request = True
                results = _parse_search_results(response.content, limit)
            except (httpx.HTTPError, etree.ParserError) as error:
                _logger.warning("Web search request failed for %s: %s", search_url, error)
                continue
            if results:
                return results
        if completed_request:
            return []
        raise WebSearchProviderException(_PROVIDER_FAILURE)

    def get_contents(self, urls: list[str]) -> list[WebContentResult]:
        out: list[WebContentResult] = []
        for url in urls:
            try:
                response = httpx.get(
                    url,
                    headers=_SEARCH_HEADERS,
                    timeout=_cfg.getint("DEFAULT", "DOWNLOAD_TIMEOUT"),
                    follow_redirects=True,
                )
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise WebFetchProviderException(_FETCH_FAILURE) from error

            if len(response.content) > _cfg.getint("DEFAULT", "MAX_FILE_SIZE"):
                raise WebFetchProviderException("The web page exceeds the fetch size limit.", retryable=False)
            doc = bare_extraction(
                response.content,
                url=str(response.url),
                favor_recall=True,
                include_links=True,
                with_metadata=True,
                config=_cfg,
            )
            if doc is None or not doc.text:
                raise WebFetchProviderException("The web page did not contain readable content.", retryable=False)
            out.append(
                WebContentResult(
                    title=doc.title or _guess_title(str(response.url)),
                    url=str(response.url),
                    text=doc.text,
                    published_date=doc.date,
                    author=doc.author,
                )
            )
        return out
