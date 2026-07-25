from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import arden.database as database
from arden.automation.descriptions import AutomationDescriptionDraft
from arden.automation.models import Automation
from arden.automation.scheduler import Scheduler
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
async def test_v14_migration_moves_legacy_prompts_and_leaves_display_copy_pending(tmp_path: Path):
    conn = await database.connect(tmp_path / "legacy.db")
    await conn.executescript(
        """
        CREATE TABLE automation_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE scheduled_tasks (
            task_id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL,
            model TEXT,
            triggers TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            last_run_at TEXT,
            next_run_at TEXT,
            last_result TEXT,
            running_since TEXT,
            auto_approve INTEGER NOT NULL DEFAULT 0,
            handler TEXT,
            builtin INTEGER NOT NULL DEFAULT 0,
            cooldown_minutes INTEGER,
            kind TEXT NOT NULL DEFAULT 'automation',
            max_iterations INTEGER,
            iteration_count INTEGER NOT NULL DEFAULT 0,
            stop_when TEXT,
            max_age_days INTEGER,
            thread_id TEXT,
            read_history INTEGER NOT NULL DEFAULT 0,
            parent_automation_id TEXT,
            idempotency_key TEXT,
            idempotency_scope TEXT,
            tool_scope TEXT,
            output_schema TEXT
        );
        CREATE TABLE automation_suggestions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            triggers TEXT NOT NULL,
            rationale TEXT NOT NULL,
            evidence TEXT,
            category TEXT NOT NULL,
            icon TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            source_automation_id TEXT
        );
        INSERT INTO automation_meta (key, value) VALUES ('schema_version', '13');
        """
    )
    now = datetime.now(UTC).isoformat()
    await conn.execute(
        """INSERT INTO scheduled_tasks (
            task_id, name, description, model, triggers, enabled, created_at
        ) VALUES ('legacy', 'Legacy', 'Read the full inbox and create a detailed report.', NULL, '[]', 1, ?)""",
        (now,),
    )
    await conn.execute(
        """INSERT INTO automation_suggestions (
            id, name, description, triggers, rationale, category, status, created_at
        ) VALUES ('legacy-suggestion', 'Legacy suggestion', 'Inspect all pull requests and summarize each one.', '[]', 'useful', 'Work', 'active', ?)""",
        (now,),
    )
    await conn.commit()

    store = AutomationStore(conn)
    await store.init_schema()

    automation = await store.get("legacy")
    assert automation is not None
    assert automation.prompt == "Read the full inbox and create a detailed report."
    assert automation.description is None
    assert automation.description_source is None

    suggestion = (await store.list_active_suggestions())[0]
    assert suggestion.prompt == "Inspect all pull requests and summarize each one."
    assert suggestion.description is None
    await conn.close()


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
        return SimpleNamespace(output="done")

    monkeypatch.setattr("arden.automation.scheduler.run_agent", _run_agent)
    scheduler = Scheduler(store=store, build_deps=lambda: object())

    assert await scheduler._run_agent(automation) == "done"
    assert "Read all inbox messages and produce a detailed urgent-only briefing." in captured["prompt"]
    assert "Checks urgent inbox updates." not in captured["prompt"]
    await conn.close()
