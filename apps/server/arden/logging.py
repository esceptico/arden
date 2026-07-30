import asyncio
import logging
import logging.handlers
import os
import sys
from pathlib import Path

import structlog

from arden.constants import ARDEN_DIR

renderer = structlog.dev.ConsoleRenderer(colors=True)

# The server is as often a background daemon as a foreground `just server`, and
# stderr dies with the terminal. Everything therefore goes to both sinks: the
# console keeps colour for reading, the file gets JSON for grepping after the
# fact. One record, two renderings — so "check the logs" means the same thing
# regardless of how the server was started.
# Overridable on its own so a test run can redirect the sink without moving
# ARDEN_DIR, which also relocates settings, credentials, and every database.
LOG_DIR = Path(os.environ.get("ARDEN_LOG_DIR") or ARDEN_DIR / "logs")
LOG_FILE = LOG_DIR / "arden.log"
LOG_MAX_BYTES = 8 * 1024 * 1024
LOG_BACKUP_COUNT = 5


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


# The console renderer prints tracebacks itself; JSON needs format_exc_info or
# an exception lands in the file as a bare `"exc_info": true`.
_JSON_FORMATTER_ARGS = {
    "processors": [
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    "foreign_pre_chain": processors[:-1],
}


def _console_handler() -> logging.Handler:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(structlog.stdlib.ProcessorFormatter(processor=renderer, foreign_pre_chain=processors[:-1]))
    return handler


def _file_handler() -> logging.Handler:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(structlog.stdlib.ProcessorFormatter(**_JSON_FORMATTER_ARGS))
    return handler


# Configure immediately so all logs use consistent format from first import.
# Routing through stdlib (rather than writing straight to stderr) is what lets
# arden's own structlog records and uvicorn's stdlib records land in the same
# two sinks with the same shape.
structlog.configure(
    processors=[*processors[:-1], structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    logger_factory=structlog.stdlib.LoggerFactory(),
)

_root = logging.getLogger()
_root.handlers.clear()
_root.addHandler(_console_handler())
_root.addHandler(_file_handler())
_root.setLevel(logging.INFO)

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

json_formatter = {"()": structlog.stdlib.ProcessorFormatter, **_JSON_FORMATTER_ARGS}

_file_handler_config = {
    "class": "logging.handlers.RotatingFileHandler",
    "filename": str(LOG_FILE),
    "maxBytes": LOG_MAX_BYTES,
    "backupCount": LOG_BACKUP_COUNT,
    "encoding": "utf-8",
}

UVICORN_LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"drop_asgi_cancelled": {"()": _DropAsgiCancelledError}},
    "formatters": {"default": formatter, "access": formatter, "json": json_formatter},
    "handlers": {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
            "filters": ["drop_asgi_cancelled"],
        },
        "access": {"formatter": "access", "class": "logging.StreamHandler", "stream": "ext://sys.stderr"},
        # uvicorn's records reach the same file as arden's own, so a request
        # traceback and the server log it happened during read side by side.
        "default_file": {"formatter": "json", "filters": ["drop_asgi_cancelled"], **_file_handler_config},
        "access_file": {"formatter": "json", **_file_handler_config},
    },
    "loggers": {
        # uvicorn.error owns no handler and propagates to "uvicorn", whose "default"
        # handler carries drop_asgi_cancelled — keep it that way so the filter sees
        # run_asgi's CancelledError logs (don't attach a handler to uvicorn.error).
        "uvicorn": {"handlers": ["default", "default_file"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"level": "INFO"},
        "uvicorn.access": {"handlers": ["access", "access_file"], "level": "INFO", "propagate": False},
    },
}
