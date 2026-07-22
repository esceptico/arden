class NoSearchResultsException(Exception):
    """Search completed successfully, but the provider found no results."""


class WebSearchProviderException(Exception):
    """Search provider failed before it could return results."""
