"""Structured timing for startup work that can delay API readiness."""

from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter
from typing import Any


@contextmanager
def startup_phase(logger: Any, phase: str) -> Iterator[None]:
    """Log one startup phase with a stable name, outcome, and elapsed time."""

    started = perf_counter()
    try:
        yield
    except BaseException:
        logger.error(
            "Startup phase failed",
            startup_phase=phase,
            outcome="error",
            elapsed_ms=round((perf_counter() - started) * 1000, 1),
            exc_info=True,
        )
        raise
    logger.info(
        "Startup phase complete",
        startup_phase=phase,
        outcome="ok",
        elapsed_ms=round((perf_counter() - started) * 1000, 1),
    )
