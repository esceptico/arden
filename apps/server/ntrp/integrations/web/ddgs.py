import logging
from urllib.parse import urlparse

import trafilatura
from ddgs import DDGS
from ddgs.exceptions import DDGSException
from trafilatura import bare_extraction
from trafilatura.settings import use_config

from ntrp.integrations.web.exceptions import NoSearchResultsException, WebSearchProviderException
from ntrp.integrations.web.types import WebContentResult, WebSearchResult

_logger = logging.getLogger(__name__)

_cfg = use_config()
_cfg.set("DEFAULT", "DOWNLOAD_TIMEOUT", "10")
_cfg.set("DEFAULT", "MAX_FILE_SIZE", "1000000")

_DDGS_NO_RESULTS = ("No results found.",)


def _guess_title(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc or url


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
        results: list[WebSearchResult] = []
        with DDGS() as client:
            try:
                items = client.text(query, max_results=limit) or []
            except DDGSException as e:
                if e.args == _DDGS_NO_RESULTS:
                    raise NoSearchResultsException(query) from e
                else:
                    _logger.warning("DuckDuckGo search failed: %s", e)
                    raise WebSearchProviderException("DuckDuckGo request failed.") from e
            for item in items:
                title = (item.get("title") or item.get("heading") or "").strip()
                url = (item.get("href") or item.get("url") or "").strip()
                snippet = (item.get("body") or item.get("snippet") or "").strip()
                published = item.get("date")
                if not url:
                    continue
                if not title:
                    title = _guess_title(url)
                results.append(
                    WebSearchResult(
                        title=title,
                        url=url,
                        published_date=str(published) if published else None,
                        summary=snippet or None,
                    )
                )
        return results

    def get_contents(self, urls: list[str]) -> list[WebContentResult]:
        out: list[WebContentResult] = []
        for url in urls:
            try:
                downloaded = trafilatura.fetch_url(url, config=_cfg)
                if not downloaded:
                    out.append(WebContentResult(title=_guess_title(url), url=url))
                    continue

                doc = bare_extraction(
                    downloaded,
                    url=url,
                    favor_recall=True,
                    include_links=True,
                    with_metadata=True,
                    config=_cfg,
                )
                if doc:
                    title = doc.title or _guess_title(url)
                    out.append(
                        WebContentResult(
                            title=title,
                            url=url,
                            text=doc.text or None,
                            published_date=doc.date,
                            author=doc.author,
                        )
                    )
                else:
                    out.append(WebContentResult(title=_guess_title(url), url=url))
            except Exception as e:
                _logger.warning("Could not fetch content from %s: %s", url, e)
                out.append(WebContentResult(title=_guess_title(url), url=url))
        return out
