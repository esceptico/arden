import asyncio
import logging
import sys

import structlog

renderer = structlog.dev.ConsoleRenderer(colors=True)


class _DropAsgiCancelledError(logging.Filter):
    """uvicorn's run_asgi logs every BaseException that escapes the app — including
    the asyncio.CancelledError it raises when force-cancelling an in-flight request
    at the graceful-shutdown deadline (or on a client disconnect mid-stream) — as a
    full "Exception in ASGI application" traceback. That cancellation is benign
    control flow, not an application error; drop those records so shutdown and
    client disconnects stay quiet."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        return not isinstance(exc, asyncio.CancelledError)


processors = [
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="%H:%M:%S"),
    renderer,
]

# Configure immediately so all logs use consistent format from first import
structlog.configure(
    processors=processors,
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    logger_factory=structlog.WriteLoggerFactory(file=sys.stderr),
)

for _name in ("googleapiclient.discovery_cache", "LiteLLM", "httpx"):
    logging.getLogger(_name).setLevel(logging.WARNING)


def get_logger(name: str | None = None):
    return structlog.get_logger(name or "arden")


def route_stdlib_logger(name: str, level: int = logging.INFO) -> None:
    """Funnel a third-party stdlib logger (judgeval, opentelemetry) into Arden's
    stderr/structlog stream. Clears the library's own handlers — judgeval attaches
    a stdout handler at import that would otherwise double-print — and stops
    propagation, so its records render once in the same format as the rest of the
    server. Must be called after the library is imported (its handler exists by then)."""
    log = logging.getLogger(name)
    log.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(structlog.stdlib.ProcessorFormatter(processor=renderer, foreign_pre_chain=processors[:-1]))
    log.addHandler(handler)
    log.setLevel(level)
    log.propagate = False


formatter = {
    "()": structlog.stdlib.ProcessorFormatter,
    "processor": renderer,
    "foreign_pre_chain": processors[:-1],
}

UVICORN_LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"drop_asgi_cancelled": {"()": _DropAsgiCancelledError}},
    "formatters": {"default": formatter, "access": formatter},
    "handlers": {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
            "filters": ["drop_asgi_cancelled"],
        },
        "access": {"formatter": "access", "class": "logging.StreamHandler", "stream": "ext://sys.stderr"},
    },
    "loggers": {
        # uvicorn.error owns no handler and propagates to "uvicorn", whose "default"
        # handler carries drop_asgi_cancelled — keep it that way so the filter sees
        # run_asgi's CancelledError logs (don't attach a handler to uvicorn.error).
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"level": "INFO"},
        "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
    },
}
