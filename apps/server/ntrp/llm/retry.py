from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from ntrp.logging import get_logger

_logger = get_logger(__name__)


def _gemini_daily_quota_exhausted(exc: BaseException) -> bool:
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        return False
    error = details.get("error", details)
    if not isinstance(error, dict):
        return False
    for detail in error.get("details", []):
        if not isinstance(detail, dict):
            continue
        for violation in detail.get("violations", []):
            if isinstance(violation, dict) and "PerDay" in str(violation.get("quotaId", "")):
                return True
    return False


def _is_retryable(exc: BaseException) -> bool:
    from anthropic import APIStatusError as AnthropicError
    from google.genai.errors import APIError as GeminiError
    from openai import APIStatusError as OpenAIError

    if isinstance(exc, AnthropicError | OpenAIError):
        return exc.status_code in {408, 409, 429} or exc.status_code >= 500

    if isinstance(exc, GeminiError):
        if _gemini_daily_quota_exhausted(exc):
            return False
        return exc.code in {408, 429} or exc.code >= 500

    return False


def _log_retry(retry_state) -> None:
    _logger.warning(
        "LLM call failed (attempt %d/3), retrying: %s",
        retry_state.attempt_number,
        retry_state.outcome.exception(),
    )


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=0.5, max=8, jitter=2),
    reraise=True,
    before_sleep=_log_retry,
)
async def with_retry(fn, *args, **kwargs):
    return await fn(*args, **kwargs)
