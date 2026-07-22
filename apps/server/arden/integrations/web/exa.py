from exa_py import Exa

from arden.integrations.web.types import WebContentResult, WebSearchResult


class ExaWebSource:
    name = "web"
    provider = "exa"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("EXA_API_KEY not configured")
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = Exa(api_key=self._api_key)
        return self._client

    def search_with_details(
        self,
        query: str,
        limit: int = 5,
        category: str | None = None,
    ) -> list[WebSearchResult]:
        client = self._get_client()

        search_params = {
            "query": query,
            "num_results": limit,
            "type": "auto",
        }
        if category:
            search_params["category"] = category

        result = client.search_and_contents(
            **search_params,
            highlights={"num_sentences": 2, "highlights_per_url": 3, "query": query},
            summary={"query": f"Key information about: {query}"},
        )

        return [
            WebSearchResult(
                title=r.title or "",
                url=r.url or "",
                published_date=getattr(r, "published_date", None),
                summary=getattr(r, "summary", None),
                highlights=getattr(r, "highlights", None),
            )
            for r in result.results
        ]

    def get_contents(self, urls: list[str]) -> list[WebContentResult]:
        client = self._get_client()
        result = client.get_contents(urls, text=True)

        return [
            WebContentResult(
                title=getattr(r, "title", None),
                url=r.url or "",
                text=getattr(r, "text", None),
                published_date=getattr(r, "published_date", None),
                author=getattr(r, "author", None),
            )
            for r in result.results
        ]
