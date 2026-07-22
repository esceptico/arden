from google.genai.errors import ClientError

from arden.llm.retry import _is_retryable


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
