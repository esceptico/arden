from ntrp.integrations.base import IntegrationOperationError
from ntrp.tools.core import ToolResult

_RECOVERY_ACTIONS = {
    "invalid_ref": "List or search the provider resources, then retry with an exact available reference.",
    "not_found": "List or search the provider resources before retrying.",
    "rate_limited": "Wait and retry the operation later.",
    "provider_error": "Retry later; reconnect the integration if the failure persists.",
}


def operation_error_result(error: IntegrationOperationError, *, preview: str) -> ToolResult:
    return ToolResult.failure(
        code=error.code,
        message=error.safe_message,
        preview=preview,
        retryable=error.retryable,
        recovery_action=_RECOVERY_ACTIONS.get(error.code, "Inspect the request and retry."),
    )
