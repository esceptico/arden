import asyncio
from types import SimpleNamespace

from arden.config import Config
from arden.server import app as server_app
from arden.server.app import _run_post_ready_warmup
from arden.server.runtime import Runtime


class _Store:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def list_interrupted_chat_runs(self):
        self.calls.append("chat_recovery")
        return []

    async def list_undelivered_background_completions(self):
        self.calls.append("completion_redelivery")
        return []

    async def list_respawnable_background_agent_runs(self, *, max_attempts):
        self.calls.append("background_respawns")
        return []


class _BusRegistry:
    pass


async def _resume(_run_id: str, _session_id: str):
    return None


async def _dispatch(*_args, **_kwargs):
    return None


async def _refresh_catalog() -> bool:
    return True


async def test_post_ready_warmup_is_nonblocking_and_orders_scheduler_after_recovery(monkeypatch, tmp_path) -> None:
    runtime = Runtime(Config(arden_dir=tmp_path, memory=False))
    runtime._connected = True
    calls: list[str] = []
    gate = asyncio.Event()
    store = _Store(calls)
    runtime.stores = SimpleNamespace(sessions=SimpleNamespace(store=store), outbox=object())

    async def complete_runtime() -> None:
        calls.append("runtime_warmup_started")
        await gate.wait()
        calls.append("runtime_warmup_finished")

    async def enqueue_projection() -> bool:
        calls.append("wiki_projection_enqueued")
        return True

    async def start_scheduler() -> None:
        calls.append("scheduler_started")
        runtime._set_warmup_capability("automations")

    async def maintain_storage() -> dict:
        calls.append("storage_maintained")
        return {"status": "ok"}

    monkeypatch.setattr(runtime, "complete_deferred_runtime_warmup", complete_runtime)
    monkeypatch.setattr(runtime, "enqueue_startup_wiki_projection", enqueue_projection)
    monkeypatch.setattr(runtime, "start_scheduler", start_scheduler)
    monkeypatch.setattr(runtime, "start_monitor", lambda: calls.append("monitor_started"))
    monkeypatch.setattr(runtime, "run_storage_maintenance_once", maintain_storage)
    monkeypatch.setattr(runtime, "start_storage_maintenance", lambda: calls.append("storage_worker_started"))
    monkeypatch.setattr(server_app, "prune_offload_store", lambda: None)

    task = asyncio.create_task(_run_post_ready_warmup(runtime, _BusRegistry(), _resume, _dispatch, _refresh_catalog))
    await asyncio.sleep(0)

    assert task.done() is False
    assert runtime.connected is True
    assert runtime.warmup_status()["status"] == "running"
    assert calls == ["runtime_warmup_started"]

    gate.set()
    await task

    assert calls == [
        "runtime_warmup_started",
        "runtime_warmup_finished",
        "chat_recovery",
        "wiki_projection_enqueued",
        "scheduler_started",
        "monitor_started",
        "completion_redelivery",
        "background_respawns",
        "storage_maintained",
        "storage_worker_started",
    ]
    assert runtime.warmup_status()["status"] == "ready"
    assert runtime.warmup_status()["capabilities"]["automations"] is True


async def test_post_ready_warmup_failure_keeps_core_api_available(monkeypatch, tmp_path) -> None:
    runtime = Runtime(Config(arden_dir=tmp_path, memory=False))
    runtime._connected = True

    async def fail_runtime() -> None:
        raise RuntimeError("projection failed")

    monkeypatch.setattr(runtime, "complete_deferred_runtime_warmup", fail_runtime)

    await _run_post_ready_warmup(runtime, _BusRegistry(), _resume, _dispatch, _refresh_catalog)

    assert runtime.connected is True
    assert runtime.warmup_status()["status"] == "error"
    assert runtime.warmup_status()["error"] == "RuntimeError: projection failed"
