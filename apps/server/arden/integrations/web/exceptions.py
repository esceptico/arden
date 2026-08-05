from arden.integrations.base import IntegrationOperationError


class WebProviderException(IntegrationOperationError):
    def __init__(self, safe_message: str, *, retryable: bool = True):
        super().__init__(code="provider_error", safe_message=safe_message, retryable=retryable)


class WebSearchProviderException(WebProviderException):
    pass


class WebFetchProviderException(WebProviderException):
    pass
