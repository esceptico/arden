import httpx

from arden.mcp.errors import describe_mcp_error


def test_describe_mcp_error_unwraps_http_exception_group():
    request = httpx.Request("POST", "http://127.0.0.1:8008/mcp")
    response = httpx.Response(401, request=request, text='{"error":"unauthorized"}')
    error = httpx.HTTPStatusError("unauthorized", request=request, response=response)

    message = describe_mcp_error(ExceptionGroup("unhandled errors in a TaskGroup", [error]))

    assert "unhandled errors in a TaskGroup" not in message
    assert "HTTP 401 Unauthorized for http://127.0.0.1:8008/mcp" in message
    assert '{"error":"unauthorized"}' in message
    assert "Check the MCP server token or Authorization header." in message


def test_describe_mcp_error_formats_network_error():
    request = httpx.Request("POST", "http://127.0.0.1:8008/mcp")
    error = httpx.ConnectError("connection refused", request=request)

    message = describe_mcp_error(ExceptionGroup("unhandled errors in a TaskGroup", [error]))

    assert message == "Network error for http://127.0.0.1:8008/mcp: connection refused"


def test_describe_mcp_error_handles_unread_streaming_response_body():
    request = httpx.Request("POST", "http://127.0.0.1:8008/mcp")
    response = httpx.Response(401, request=request, stream=httpx.ByteStream(b'{"error":"unauthorized"}'))
    error = httpx.HTTPStatusError("unauthorized", request=request, response=response)

    message = describe_mcp_error(ExceptionGroup("unhandled errors in a TaskGroup", [error]))

    assert message == (
        "HTTP 401 Unauthorized for http://127.0.0.1:8008/mcp. Check the MCP server token or Authorization header."
    )


def test_describe_mcp_error_deduplicates_nested_errors():
    request = httpx.Request("POST", "http://127.0.0.1:8008/mcp")
    error = httpx.ConnectError("connection refused", request=request)

    message = describe_mcp_error(ExceptionGroup("outer", [ExceptionGroup("inner", [error, error])]))

    assert message == "Network error for http://127.0.0.1:8008/mcp: connection refused"
