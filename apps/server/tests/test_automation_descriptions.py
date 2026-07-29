from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import arden.database as database
from arden.automation.descriptions import AutomationDescriptionDraft
from arden.automation.models import Automation
from arden.automation.scheduler import CompletedAgentRun, Scheduler
from arden.automation.service import AutomationService
from arden.automation.store import AutomationStore
from arden.automation.triggers import TimeTrigger


class _DescriptionLLM:
    def __init__(self, descriptions: list[str]):
        self.descriptions = iter(descriptions)
        self.calls: list[dict] = []

    async def completion(self, **kwargs):
        self.calls.append(kwargs)
        content = AutomationDescriptionDraft(description=next(self.descriptions))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


async def _store(tmp_path: Path) -> tuple[AutomationStore, object]:
    conn = await database.connect(tmp_path / "automation.db")
    store = AutomationStore(conn)
    await store.init_schema()
    return store, conn


def test_service_enforces_concise_manual_description_contract():
    assert AutomationService._normalize_description("x" * 220) == "x" * 220
    with pytest.raises(ValueError, match="220"):
        AutomationService._normalize_description("x" * 221)


@pytest.mark.asyncio
async def test_service_generates_display_copy_and_preserves_manual_copy_on_prompt_edits(tmp_path: Path):
    store, conn = await _store(tmp_path)
    llm = _DescriptionLLM(["Checks the inbox for urgent updates.", "Reviews the inbox for new urgent updates."])
    scheduler = Scheduler(store=store, build_deps=lambda: None)
    service = AutomationService(
        store=store,
        scheduler=scheduler,
        session_service=object(),
        get_cheap_llm=lambda: llm,
        description_model="cheap-model",
    )

    created = await service.create(
        name="Inbox sweep",
        prompt="Read all new inbox messages, identify urgent ones, and report them to the user.",
        trigger_type="time",
        every="1h",
        thread_id="chat-1",
    )
    assert created is not None
    assert created.description == "Checks the inbox for urgent updates."
    assert created.description_source == "generated"
    assert created.prompt.startswith("Read all new inbox")

    regenerated = await service.update(
        created.task_id,
        prompt="Read all new inbox messages, identify urgent changes, and report them to the user.",
    )
    assert regenerated.description == "Reviews the inbox for new urgent updates."
    assert regenerated.description_source == "generated"

    manual = await service.update(created.task_id, description="My manual inbox note.")
    kept = await service.update(created.task_id, prompt="Use a different inbox procedure.")
    assert manual.description_source == "manual"
    assert kept.description == "My manual inbox note."
    assert kept.description_source == "manual"
    assert kept.prompt == "Use a different inbox procedure."
    assert len(llm.calls) == 2
    await conn.close()


@pytest.mark.asyncio
async def test_scheduler_executes_prompt_not_display_description(tmp_path: Path, monkeypatch):
    store, conn = await _store(tmp_path)
    automation = Automation(
        task_id="prompt-contract",
        name="Inbox sweep",
        description="Checks urgent inbox updates.",
        description_source="manual",
        prompt="Read all inbox messages and produce a detailed urgent-only briefing.",
        model=None,
        triggers=[TimeTrigger(every="1h")],
        enabled=True,
        created_at=datetime.now(UTC),
        next_run_at=None,
        last_run_at=None,
        last_result=None,
        running_since=None,
        auto_approve=False,
    )
    captured: dict[str, str] = {}

    async def _run_agent(_deps, request):
        captured["prompt"] = request.prompt
        return SimpleNamespace(run_id="agent-run", output="done")

    monkeypatch.setattr("arden.automation.scheduler.run_agent", _run_agent)
    scheduler = Scheduler(store=store, build_deps=lambda: object())

    assert await scheduler._run_agent(automation) == CompletedAgentRun("agent-run", "done")
    assert "Read all inbox messages and produce a detailed urgent-only briefing." in captured["prompt"]
    assert "Checks urgent inbox updates." not in captured["prompt"]
    await conn.close()
