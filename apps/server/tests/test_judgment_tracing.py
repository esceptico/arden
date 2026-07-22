import pytest

import arden.observability.judgment as judgment
from arden.observability import activate_tracing, init_tracing, shutdown_tracing, tracing_enabled


def test_unconfigured_is_safe_noop(monkeypatch):
    # No credentials -> init/shutdown must be idempotent no-ops that never raise
    # and never import/contact judgeval.
    monkeypatch.setattr(judgment, "_credentials", lambda: (None, None))
    monkeypatch.setattr(judgment, "_initialized", False)
    assert tracing_enabled() is False
    init_tracing()
    init_tracing()
    shutdown_tracing()
    assert judgment._initialized is False


def test_activate_tracing_sets_current_context(monkeypatch):
    calls = []

    class FakeTracer:
        def set_active(self):
            calls.append(("active",))

    monkeypatch.setattr(judgment, "_tracer", FakeTracer())

    from judgeval import Tracer

    monkeypatch.setattr(Tracer, "set_session_id", staticmethod(lambda value: calls.append(("session", value))))
    monkeypatch.setattr(Tracer, "tag", staticmethod(lambda value: calls.append(("tag", value))))

    activate_tracing("s1", tags=["chat"])

    assert calls == [("active",), ("session", "s1"), ("tag", ["chat"])]


def test_provider_span_names_use_active_workflow_label(monkeypatch):
    from judgeval.trace import BaseTracer

    seen = []

    def fake_start_span(name, attributes=None):
        seen.append(name)
        return object()

    monkeypatch.setattr(judgment, "_provider_span_names_patched", False)
    monkeypatch.setattr(BaseTracer, "start_span", staticmethod(fake_start_span))

    judgment._patch_provider_span_names()
    token = judgment._llm_span_name.set("memory.synthesis")
    try:
        BaseTracer.start_span("OPENAI_API_CALL", {})
    finally:
        judgment._llm_span_name.reset(token)

    assert seen == ["memory.synthesis.llm"]


@pytest.mark.asyncio
async def test_observed_trace_activates_before_entering_span(monkeypatch):
    calls = []
    monkeypatch.setattr(judgment, "activate_tracing", lambda *, tags: calls.append(tags))

    from judgeval import Tracer

    def fake_observe(**kwargs):
        def decorator(func):
            async def wrapper(*args, **inner_kwargs):
                calls.append(kwargs["span_name"])
                return await func(*args, **inner_kwargs)

            return wrapper

        return decorator

    monkeypatch.setattr(Tracer, "observe", staticmethod(fake_observe))

    @judgment.observed_trace("memory.curate", tags="memory")
    async def run():
        calls.append("body")
        return "ok"

    assert await run() == "ok"
    assert calls == ["memory", "memory.curate", "body"]
