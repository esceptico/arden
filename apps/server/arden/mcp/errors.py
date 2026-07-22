from collections.abc import Iterable

import httpx

_MAX_BODY_LEN = 300


def describe_mcp_error(exc: BaseException) -> str:
    messages = list(dict.fromkeys(_describe_leaf(e) for e in _leaf_errors(exc)))
    if not messages:
        return str(exc) or type(exc).__name__
    return "; ".join(messages)


def _leaf_errors(exc: BaseException) -> Iterable[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        for child in exc.exceptions:
            yield from _leaf_errors(child)
        return
    yield exc


def _describe_leaf(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return _describe_http_status(exc)
    if isinstance(exc, httpx.RequestError):
        return _describe_request_error(exc)
    return str(exc) or type(exc).__name__


def _describe_http_status(exc: httpx.HTTPStatusError) -> str:
    response = exc.response
    request = exc.request
    detail = f"HTTP {response.status_code} {response.reason_phrase} for {request.url}"
    body = _response_body(response)
    if body:
        detail = f"{detail}: {body}"
    hint = _status_hint(response.status_code)
    return f"{detail}. {hint}" if hint else detail


def _describe_request_error(exc: httpx.RequestError) -> str:
    url = exc.request.url if exc.request else "unknown URL"
    return f"Network error for {url}: {exc}"


def _response_body(response: httpx.Response) -> str:
    try:
        text = response.text.strip()
    except httpx.ResponseNotRead:
        return ""
    if not text:
        return ""
    text = " ".join(text.split())
    if len(text) > _MAX_BODY_LEN:
        text = f"{text[:_MAX_BODY_LEN]}..."
    return text


def _status_hint(status_code: int) -> str:
    if status_code in {401, 403}:
        return "Check the MCP server token or Authorization header."
    if status_code == 404:
        return "Check the MCP server URL and /mcp path."
    if status_code == 406:
        return "Check that the server supports MCP Streamable HTTP."
    return ""
