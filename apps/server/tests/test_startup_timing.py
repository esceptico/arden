import asyncio

from arden.server import startup


class _Logger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict]] = []

    def info(self, event: str, **fields) -> None:
        self.records.append(("info", event, fields))

    def error(self, event: str, **fields) -> None:
        self.records.append(("error", event, fields))


def test_startup_phase_logs_success_with_elapsed_time(monkeypatch) -> None:
    times = iter((10.0, 10.125))
    monkeypatch.setattr(startup, "perf_counter", lambda: next(times))
    logger = _Logger()

    with startup.startup_phase(logger, "runtime.stores_schema"):
        pass

    assert logger.records == [
        (
            "info",
            "Startup phase complete",
            {
                "startup_phase": "runtime.stores_schema",
                "outcome": "ok",
                "elapsed_ms": 125.0,
            },
        )
    ]


def test_startup_phase_logs_failure_and_reraises(monkeypatch) -> None:
    times = iter((20.0, 20.25))
    monkeypatch.setattr(startup, "perf_counter", lambda: next(times))
    logger = _Logger()

    try:
        with startup.startup_phase(logger, "runtime.index_sync"):
            raise RuntimeError("broken")
    except RuntimeError as error:
        assert str(error) == "broken"
    else:
        raise AssertionError("startup failure was not reraised")

    assert logger.records == [
        (
            "error",
            "Startup phase failed",
            {
                "startup_phase": "runtime.index_sync",
                "outcome": "error",
                "elapsed_ms": 250.0,
                "exc_info": True,
            },
        )
    ]


def test_startup_phase_logs_cancellation_without_error_traceback(monkeypatch) -> None:
    times = iter((30.0, 30.5))
    monkeypatch.setattr(startup, "perf_counter", lambda: next(times))
    logger = _Logger()

    try:
        with startup.startup_phase(logger, "warmup.runtime.index_sync"):
            raise asyncio.CancelledError
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("startup cancellation was not reraised")

    assert logger.records == [
        (
            "info",
            "Startup phase cancelled",
            {
                "startup_phase": "warmup.runtime.index_sync",
                "outcome": "cancelled",
                "elapsed_ms": 500.0,
            },
        )
    ]
