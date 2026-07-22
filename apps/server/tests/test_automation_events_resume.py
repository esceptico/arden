from arden.server.routers.automation import _effective_after_seq


def test_effective_after_seq_uses_last_event_id_header():
    # The standard SSE resume header (re-sent by fetch-event-source on
    # reconnect) drives the cursor; the higher of query/header wins.
    assert _effective_after_seq(None, "4") == 4
    assert _effective_after_seq(2, "4") == 4
    assert _effective_after_seq(7, "4") == 7


def test_effective_after_seq_is_lenient_on_invalid_last_event_id():
    # Unlike the chat endpoint (which 400s), a malformed Last-Event-ID on the
    # automation stream must NOT fail the reconnect — fall back to the query
    # cursor / live tail instead.
    assert _effective_after_seq(None, "wat") is None
    assert _effective_after_seq(3, "wat") == 3
    assert _effective_after_seq(None, "-1") is None
    assert _effective_after_seq(None, None) is None
