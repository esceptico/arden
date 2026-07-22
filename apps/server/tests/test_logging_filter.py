"""Shutdown/disconnect quiet (arden/logging.py _DropAsgiCancelledError).

uvicorn's run_asgi logs every BaseException that escapes the app — including the
asyncio.CancelledError it raises when force-cancelling an in-flight request at the
graceful-shutdown deadline (the literal `task.cancel("Task cancelled, timeout
graceful shutdown exceeded")`) — as a full "Exception in ASGI application"
traceback. That cancellation is benign; the filter on UVICORN_LOG_CONFIG's
handler drops those records while leaving real errors intact.

These tests replicate uvicorn's exact call:
    logging.getLogger("uvicorn.error").error("Exception in ASGI application", exc_info=exc)

uvicorn.error owns no handlers; records propagate to the parent "uvicorn" logger
whose "default" handler carries the drop_asgi_cancelled filter — so the filter
must be tested through that propagation chain, which dictConfig sets up here.
"""

import asyncio
import io
import logging
import logging.config
from contextlib import redirect_stderr

import pytest

from arden.logging import UVICORN_LOG_CONFIG, _DropAsgiCancelledError

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture(autouse=True)
def _restore_logging_state():
    """dictConfig() mutates process-global logging. Snapshot the uvicorn loggers
    and restore them after each test so the added handlers/filters never leak
    into the rest of the suite."""
    saved = {
        n: (list(lg.handlers), list(lg.filters), lg.level, lg.propagate, lg.disabled)
        for n in _UVICORN_LOGGERS
        for lg in [logging.getLogger(n)]
    }
    yield
    for n, (handlers, filters, level, propagate, disabled) in saved.items():
        lg = logging.getLogger(n)
        lg.handlers, lg.filters = handlers, filters
        lg.level, lg.propagate, lg.disabled = level, propagate, disabled


def _record(exc: BaseException) -> logging.LogRecord:
    try:
        raise exc
    except BaseException as e:
        return logging.LogRecord(
            "uvicorn.error",
            logging.ERROR,
            __file__,
            0,
            "Exception in ASGI application",
            (),
            (type(e), e, e.__traceback__),
        )


def test_filter_drops_cancelled_keeps_real():
    f = _DropAsgiCancelledError()
    assert f.filter(_record(asyncio.CancelledError("timeout graceful shutdown exceeded"))) is False
    assert f.filter(_record(ValueError("boom"))) is True
    # no exc_info at all (a plain message) must pass through
    plain = logging.LogRecord("uvicorn.error", logging.ERROR, __file__, 0, "hi", (), None)
    assert f.filter(plain) is True


def _emit(exc: BaseException) -> str:
    buf = io.StringIO()
    with redirect_stderr(buf):
        logging.config.dictConfig(UVICORN_LOG_CONFIG)
        log = logging.getLogger("uvicorn.error")
        try:
            raise exc
        except BaseException as e:
            log.error("Exception in ASGI application\n", exc_info=e)
    return buf.getvalue()


def test_uvicorn_config_suppresses_cancelled_traceback():
    out = _emit(asyncio.CancelledError("Task cancelled, timeout graceful shutdown exceeded"))
    assert "Exception in ASGI application" not in out
    assert "CancelledError" not in out


def test_uvicorn_config_still_logs_real_errors():
    out = _emit(ValueError("boom"))
    assert "Exception in ASGI application" in out
    assert "ValueError" in out or "boom" in out
