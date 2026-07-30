from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from functools import partial

from anthropic import APIStatusError as AnthropicError
from google.genai.errors import APIError as GeminiError
from google.protobuf.duration_pb2 import Duration
from openai import APIStatusError as OpenAIError
from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, wait_exponential_jitter

from arden.logging import get_logger

_logger = get_logger(__name__)
_NON_RATE_LIMIT_RETRY_ATTEMPTS = 3
_RETRY_AFTER_HEADER = "retry-after"
_RETRY_AFTER_MS_HEADER = "retry-after-ms"
_GEMINI_RETRY_INFO = "type.googleapis.com/google.rpc.RetryInfo"
_OPENAI_HARD_QUOTA_CODES = frozenset({"billing_hard_limit_reached", "insufficient_quota"})
_DEFAULT_WAIT = wait_exponential_jitter(initial=0.5, max=8, jitter=2)

type RateLimitWaitCallback = Callable[[float | None], Awaitable[None]]


def _gemini_error_details(exc: GeminiError) -> list[dict]:
    details = exc.details
    if not isinstance(details, dict):
        return []
    error = details.get("error", details)
    if not isinstance(error, dict):
        return []
    return [detail for detail in error.get("details", []) if isinstance(detail, dict)]


def _gemini_daily_quota_exhausted(exc: GeminiError) -> bool:
    for detail in _gemini_error_details(exc):
        for violation in detail.get("violations", []):
            if isinstance(violation, dict) and "PerDay" in str(violation.get("quotaId", "")):
                return True
    return False


def _openai_hard_quota_exhausted(exc: OpenAIError) -> bool:
    if not isinstance(exc.body, dict):
        return False
    error = exc.body.get("error")
    return isinstance(error, dict) and error.get("code") in _OPENAI_HARD_QUOTA_CODES


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, OpenAIError):
        if _openai_hard_quota_exhausted(exc):
            return False
        return exc.status_code in {408, 409, 429} or exc.status_code >= 500

    if isinstance(exc, AnthropicError):
        return exc.status_code in {408, 409, 429} or exc.status_code >= 500

    if isinstance(exc, GeminiError):
        if _gemini_daily_quota_exhausted(exc):
            return False
        return exc.code in {408, 429} or exc.code >= 500

    return False


def _is_rate_limit(exc: BaseException) -> bool:
    if isinstance(exc, AnthropicError | OpenAIError):
        return exc.status_code == 429
    if isinstance(exc, GeminiError):
        return exc.code == 429
    return False


def _gemini_retry_delay(exc: GeminiError) -> float | None:
    for detail in _gemini_error_details(exc):
        if detail.get("@type") != _GEMINI_RETRY_INFO:
            continue
        value = detail.get("retryDelay")
        if not isinstance(value, str):
            return None
        duration = Duration()
        duration.FromJsonString(value)
        return duration.ToTimedelta().total_seconds()
    return None


def _http_retry_delay(exc: AnthropicError | OpenAIError) -> float | None:
    retry_after_ms = exc.response.headers.get(_RETRY_AFTER_MS_HEADER)
    if retry_after_ms is not None:
        return float(retry_after_ms) / 1000

    retry_after = exc.response.headers.get(_RETRY_AFTER_HEADER)
    if retry_after is None:
        return None
    try:
        return float(retry_after)
    except ValueError:
        retry_at = parsedate_to_datetime(retry_after)
        return (retry_at - datetime.now(UTC)).total_seconds()


def _provider_retry_delay(exc: BaseException) -> float | None:
    if isinstance(exc, GeminiError):
        return _gemini_retry_delay(exc)
    if isinstance(exc, AnthropicError | OpenAIError):
        return _http_retry_delay(exc)
    return None


def _retry_wait(retry_state: RetryCallState) -> float:
    error = retry_state.outcome.exception()
    if error is not None and (delay := _provider_retry_delay(error)) is not None:
        return max(0, delay)
    return _DEFAULT_WAIT(retry_state)


def _stop_retry(retry_state: RetryCallState) -> bool:
    error = retry_state.outcome.exception()
    if error is not None and _is_rate_limit(error) and _provider_retry_delay(error) is not None:
        return False
    return retry_state.attempt_number >= _NON_RATE_LIMIT_RETRY_ATTEMPTS


async def _before_attempt(callback: RateLimitWaitCallback | None, retry_state: RetryCallState) -> None:
    if callback is not None and retry_state.attempt_number > 1:
        await callback(None)


async def _before_sleep(callback: RateLimitWaitCallback | None, retry_state: RetryCallState) -> None:
    delay = retry_state.next_action.sleep if retry_state.next_action is not None else 0
    error = retry_state.outcome.exception()
    _logger.warning(
        "Model provider call failed (attempt %d), retrying in %.3fs: %s",
        retry_state.attempt_number,
        delay,
        error,
    )
    if callback is not None and error is not None and _is_rate_limit(error):
        await callback(delay)


async def with_retry(
    fn,
    *args,
    on_rate_limit_wait: RateLimitWaitCallback | None = None,
    **kwargs,
):
    retrying = AsyncRetrying(
        retry=retry_if_exception(_is_retryable),
        stop=_stop_retry,
        wait=_retry_wait,
        reraise=True,
        before=partial(_before_attempt, on_rate_limit_wait),
        before_sleep=partial(_before_sleep, on_rate_limit_wait),
    )
    return await retrying(fn, *args, **kwargs)
