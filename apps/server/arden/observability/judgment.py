import logging
import os
from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps
from typing import Any

from dotenv import dotenv_values, find_dotenv

from arden.logging import get_logger, route_stdlib_logger
from arden.settings import ARDEN_DIR

_logger = get_logger(__name__)
_initialized = False
_tracer = None
_provider_span_names_patched = False
_llm_span_name: ContextVar[str | None] = ContextVar("arden_llm_span_name", default=None)
_PROVIDER_SPAN_NAMES = {"OPENAI_API_CALL", "ANTHROPIC_API_CALL", "GOOGLE_API_CALL"}


def _credentials() -> tuple[str | None, str | None]:
    # judgeval reads os.environ, but Arden keeps secrets in .env (loaded by
    # pydantic-settings, not exported). Resolve from the same sources, mirroring
    # its precedence: ~/.arden/.env, then project .env, then the real environment.
    sources = {
        **dotenv_values(ARDEN_DIR / ".env"),
        **dotenv_values(find_dotenv(usecwd=True)),
        **os.environ,
    }
    return sources.get("JUDGMENT_API_KEY"), sources.get("JUDGMENT_ORG_ID")


def tracing_enabled() -> bool:
    return bool(_credentials()[0])


def trace_client(client):
    """The single, provider-agnostic LLM tracing wrapper.

    judgeval's wrap() instruments OpenAI (incl. codex/Responses API), Anthropic,
    and Google GenAI uniformly — one call per raw SDK client, identical for every
    provider. Every arden llm client routes its SDK client through here so there is
    one tracing path, not per-provider logic. No-op when tracing isn't configured.
    """
    if _tracer is None:
        return client
    from judgeval import wrap

    return wrap(client)


def _patch_provider_span_names() -> None:
    global _provider_span_names_patched
    if _provider_span_names_patched:
        return

    from judgeval.trace import BaseTracer

    original_start_span = BaseTracer.start_span

    def start_span(name: str, attributes: dict[str, Any] | None = None):
        llm_name = _llm_span_name.get()
        if llm_name and name in _PROVIDER_SPAN_NAMES:
            name = f"{llm_name}.llm"
        return original_start_span(name, attributes)

    BaseTracer.start_span = staticmethod(start_span)
    _provider_span_names_patched = True


def init_tracing() -> None:
    """Initialize Judgment tracing once, if a JUDGMENT_API_KEY is configured.

    No-op (and never raises) when unconfigured so the app runs without a key.
    """
    global _initialized, _tracer
    if _initialized:
        return
    api_key, org_id = _credentials()
    if not api_key:
        return
    if not org_id:
        _logger.warning("JUDGMENT_API_KEY set but JUDGMENT_ORG_ID missing — judgeval will not export spans")
    try:
        from judgeval import Tracer

        _tracer = Tracer.init(project_name="arden", api_key=api_key, organization_id=org_id)
        _patch_provider_span_names()
        # Surface judgeval's own export logs/warnings (it logs to its own stdout
        # handler at WARNING by default) into Arden's stream so traces are visible.
        route_stdlib_logger("judgeval", logging.INFO)
        route_stdlib_logger("opentelemetry", logging.WARNING)
        _initialized = True
        # Tracer.init never raises when it downgrades to no-export (missing org_id /
        # unresolvable project / network) — log the resolved state so a disabled
        # tracer is loud instead of looking identical to a working one.
        enabled = getattr(_tracer, "_enable_monitoring", None)
        _logger.info(
            "Judgment tracing initialized",
            project="arden",
            enabled=enabled,
            project_id=getattr(_tracer, "project_id", None),
            api_url=getattr(_tracer, "api_url", None),
        )
        if enabled is False:
            _logger.warning("Judgment tracing export is DISABLED — check JUDGMENT_ORG_ID / project resolution")
    except Exception as exc:  # ValueError / JudgmentRuntimeError / missing dep
        _logger.warning("Judgment tracing disabled", error=str(exc))


def activate_tracing(session_id: str | None = None, tags: str | list[str] | None = None) -> None:
    """Re-assert the active tracer in the CURRENT async context.

    judgeval resolves the active tracer from a ContextVar set by Tracer.init().
    init runs in the lifespan task, whose context request tasks do NOT inherit,
    so @Tracer.observe spans silently no-op in request handlers (and the tasks
    they spawn, e.g. run_chat). Call this at request entry so spans created in
    the request — and tasks it spawns — resolve the real tracer and export.
    """
    if _tracer is not None:
        from judgeval import Tracer

        current_span = Tracer.get_current_span()
        if not (current_span is not None and current_span.is_recording()):
            _tracer.set_active()
        if session_id:
            Tracer.set_session_id(session_id)
        if tags:
            Tracer.tag(tags)


def observed_trace(
    span_name: str,
    *,
    tags: str | list[str] | None = None,
    span_type: str = "agent",
    record_input: bool = False,
    record_output: bool = False,
) -> Callable:
    def decorator(func: Callable) -> Callable:
        from judgeval import Tracer

        observed = Tracer.observe(
            span_type=span_type,
            span_name=span_name,
            record_input=record_input,
            record_output=record_output,
        )(func)

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            activate_tracing(tags=tags)
            token = _llm_span_name.set(span_name)
            try:
                return await observed(*args, **kwargs)
            finally:
                _llm_span_name.reset(token)

        return wrapper

    return decorator


def shutdown_tracing() -> None:
    """Flush pending spans on graceful shutdown (best-effort, bounded)."""
    if not _initialized:
        return
    from judgeval import Tracer

    try:
        # Bounded flush inside uvicorn's graceful-shutdown window (cli.py: 3s).
        flushed = Tracer.force_flush(timeout_millis=2500)
        _logger.info("Judgment tracing flush on shutdown", flushed=flushed)
    except Exception as exc:
        _logger.warning("Judgment tracing flush failed", error=str(exc))
    try:
        Tracer.shutdown()
        _logger.info("Judgment tracing shutdown complete")
    except Exception as exc:
        _logger.warning("Judgment tracing shutdown failed", error=str(exc))
