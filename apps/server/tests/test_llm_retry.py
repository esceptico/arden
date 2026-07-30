import asyncio

import httpx
import pytest
from google.genai.errors import ClientError
from openai import APIStatusError as OpenAIError

from arden.llm.base import CompletionClient
from arden.llm.retry import _is_rate_limit, _is_retryable, _provider_retry_delay, with_retry


def _gemini_rate_limit(retry_delay: str) -> ClientError:
    return ClientError(
        429,
        {
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": retry_delay,
                    }
                ],
            }
        },
    )


def test_gemini_daily_quota_exhaustion_is_not_retryable():
    error = ClientError(
        429,
        {
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [
                            {
                                "quotaId": "EmbedContentRequestsPerDayPerUserPerProjectPerModel-FreeTier",
                            }
                        ],
                    }
                ],
            }
        },
    )

    assert _is_retryable(error) is False


def test_gemini_transient_rate_limit_remains_retryable():
    error = ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}})

    assert _is_retryable(error) is True


def test_gemini_retry_info_controls_the_retry_delay():
    error = _gemini_rate_limit("46.343954266s")

    assert _provider_retry_delay(error) == pytest.approx(46.343954266)


def test_http_retry_after_controls_the_retry_delay():
    response = httpx.Response(
        429,
        headers={"retry-after-ms": "1250"},
        request=httpx.Request("POST", "https://example.test"),
    )
    error = OpenAIError("rate limited", response=response, body=None)

    assert _provider_retry_delay(error) == pytest.approx(1.25)


def test_openai_hard_quota_exhaustion_is_not_retryable():
    response = httpx.Response(429, request=httpx.Request("POST", "https://example.test"))
    error = OpenAIError(
        "quota exhausted",
        response=response,
        body={"error": {"code": "insufficient_quota"}},
    )

    assert _is_retryable(error) is False


def test_server_errors_do_not_report_a_rate_limit_wait():
    response = httpx.Response(500, request=httpx.Request("POST", "https://example.test"))
    error = OpenAIError("server failed", response=response, body=None)

    assert _is_retryable(error) is True
    assert _is_rate_limit(error) is False


async def test_provider_retry_wait_is_reported_and_cancellable():
    waiting = asyncio.Event()
    observed: list[float | None] = []

    async def on_rate_limit_wait(delay: float | None) -> None:
        observed.append(delay)
        if delay is not None:
            waiting.set()

    async def rate_limited():
        raise _gemini_rate_limit("60s")

    task = asyncio.create_task(with_retry(rate_limited, on_rate_limit_wait=on_rate_limit_wait))
    await waiting.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert observed == [60.0]


async def test_provider_retry_wait_resumes_the_same_call():
    calls = 0
    observed: list[float | None] = []

    async def on_rate_limit_wait(delay: float | None) -> None:
        observed.append(delay)

    async def rate_limited_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _gemini_rate_limit("0.001s")
        return "embedded"

    result = await with_retry(rate_limited_once, on_rate_limit_wait=on_rate_limit_wait)

    assert result == "embedded"
    assert calls == 2
    assert observed == [0.001, None]


async def test_provider_directed_rate_limits_continue_past_the_generic_retry_cap():
    calls = 0

    async def rate_limited_four_times():
        nonlocal calls
        calls += 1
        if calls <= 4:
            raise _gemini_rate_limit("0.001s")
        return "embedded"

    assert await with_retry(rate_limited_four_times) == "embedded"
    assert calls == 5


async def test_stream_open_retries_rate_limits_before_exposing_output():
    class Client(CompletionClient):
        def __init__(self):
            self.calls = 0

        async def _completion(self, **_kwargs):
            raise AssertionError("streaming fallback was not expected")

        async def _stream_completion(self, **_kwargs):
            self.calls += 1
            if self.calls <= 4:
                raise _gemini_rate_limit("0.001s")
            yield "ready"

        async def close(self) -> None:
            return

    client = Client()
    output = [item async for item in client.stream_completion()]

    assert output == ["ready"]
    assert client.calls == 5
