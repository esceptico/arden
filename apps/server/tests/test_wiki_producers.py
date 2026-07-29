from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import arden.database as database
from arden.automation.models import Automation
from arden.automation.scheduler import Scheduler
from arden.automation.service import AutomationService
from arden.automation.store import AutomationStore
from arden.automation.triggers import MessageTrigger
from arden.context.models import SessionState
from arden.context.store import SessionStore
from arden.integrations.core import WIKI
from arden.revisions import ManagedFileRepository
from arden.revisions.errors import RevisionConflictError
from arden.services.session import SessionService
from arden.services.wiki_producer_models import (
    WikiProducerPartialProvisionError,
    WikiProducerProvisionConflictError,
    WikiProducerRequest,
)
from arden.services.wiki_producers import WikiProducerProvisioner
from arden.tools.core.base import Tool
from arden.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope
from arden.tools.wiki_producer import ProvisionWikiProducerInput, provision_wiki_producer_tool
from arden.wiki.pages import update_generated_region, update_page_metadata
from arden.wiki.service import WikiService


class _FakeTool(Tool):
    description = "test tool"
    policy = ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL)

    async def execute(self, execution, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def to_dict(self, name):
        return {"name": name}


async def _services(
    tmp_path: Path,
) -> tuple[WikiService, AutomationService, SessionService, AutomationStore, SessionStore]:
    automation_connection = await database.connect(tmp_path / "automations.db")
    automation_store = AutomationStore(automation_connection)
    await automation_store.init_schema()
    session_connection = await database.connect(tmp_path / "sessions.db")
    session_store = SessionStore(session_connection)
    await session_store.init_schema()
    sessions = SessionService(session_store)
    automation = AutomationService(
        store=automation_store,
        scheduler=Scheduler(store=automation_store, build_deps=lambda: None),
        session_service=sessions,
    )
    wiki = WikiService(ManagedFileRepository(tmp_path / "wiki" / "pages", history_root=tmp_path / "wiki" / ".history"))
    return wiki, automation, sessions, automation_store, session_store


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for name in ("gmail_search", "read_wiki_page", "publish_wiki_generated"):
        registry.register(name, _FakeTool())
    return registry


def _request(*, expected_head: str | None, prompt: str = "Read email and update this feed.") -> WikiProducerRequest:
    return WikiProducerRequest(
        page_id="email-updates",
        path="feeds/email-updates.md",
        title="Email Updates",
        aliases=("Email feed",),
        automation_name="Email update feed",
        prompt=prompt,
        model=None,
        trigger_type="time",
        at="08:00",
        days="daily",
        every=None,
        start=None,
        end=None,
        event_type=None,
        lead_minutes=None,
        channels=None,
        from_user=None,
        contains=None,
        source_tool_scope=("gmail_search",),
        expected_head=expected_head,
    )


def _message_request(*, expected_head: str | None) -> WikiProducerRequest:
    return WikiProducerRequest(
        page_id="slack-feed",
        path="feeds/slack.md",
        title="Slack feed",
        aliases=(),
        automation_name="Slack feed",
        prompt="Read Slack.",
        model=None,
        trigger_type="message",
        at=None,
        days=None,
        every=None,
        start=None,
        end=None,
        event_type=None,
        lead_minutes=None,
        channels=("C1", "random"),
        from_user="U1",
        contains=("Launch", "urgent"),
        source_tool_scope=("gmail_search",),
        expected_head=expected_head,
    )


class _FakeSlack:
    async def resolve_channel(self, value: str) -> tuple[str, str]:
        channels = {"C1": ("C1", "design"), "design": ("C1", "design"), "random": ("C2", "random")}
        return channels[value]

    async def resolve_user(self, value: str) -> dict[str, str]:
        assert value in {"U1", "Ada"}
        return {"id": "U1", "name": "Ada"}


def _foreign_automation(task_id: str, channel_id: str) -> Automation:
    return Automation(
        task_id=task_id,
        name="Foreign task",
        description="Foreign task",
        description_source="manual",
        prompt="Do something else.",
        model=None,
        triggers=[],
        enabled=True,
        created_at=datetime.now(UTC),
        next_run_at=None,
        last_run_at=None,
        last_result=None,
        running_since=None,
        auto_approve=False,
        thread_id=channel_id,
        read_history=False,
    )


@pytest.mark.asyncio
async def test_provisioner_creates_one_owned_page_channel_and_automation_and_replays_exactly(tmp_path: Path) -> None:
    wiki, automation, sessions, automation_store, session_store = await _services(tmp_path)
    try:
        provisioner = WikiProducerProvisioner(wiki, automation, sessions, _registry().tools)
        created = await provisioner.provision(_request(expected_head=None))

        assert created.page_created is True
        assert created.channel_created is True
        assert created.automation_created is True
        assert created.automation_id == "wiki-producer:email-updates"
        assert created.tool_scope == ("gmail_search", "publish_wiki_generated", "read_wiki_page")
        assert created.auto_approve is True
        page = wiki.read_page(created.page_id)
        assert page.page.metadata["producer_automation_id"] == "wiki-producer:email-updates"
        assert dict(page.page.metadata["producer_contract"])["version"] == 1
        assert (
            await session_store.load_session(created.channel_id)
        ).state.origin_automation_id == created.automation_id

        history_count = len(wiki.repository.history())
        replayed = await provisioner.provision(_request(expected_head=created.head))

        assert replayed.page_created is False
        assert replayed.channel_created is False
        assert replayed.automation_created is False
        assert replayed.head == created.head
        assert len(wiki.repository.history()) == history_count
        assert len(await automation_store.list_all()) == 1
    finally:
        await session_store.conn.close()
        await automation_store.conn.close()


@pytest.mark.asyncio
async def test_provisioner_recovers_from_a_page_only_partial_without_replacing_its_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki, automation, sessions, automation_store, session_store = await _services(tmp_path)
    try:
        provisioner = WikiProducerProvisioner(wiki, automation, sessions, _registry().tools)
        original_create = automation.create

        async def fail_create(**kwargs):
            del kwargs
            raise RuntimeError("automation store unavailable")

        monkeypatch.setattr(automation, "create", fail_create)
        with pytest.raises(RuntimeError, match="exists but automation"):
            await provisioner.provision(_request(expected_head=None))

        partial_head = wiki.repository.head
        channel = await session_store.load_session("wiki-producer:email-updates:channel")
        assert channel is not None
        with pytest.raises(RuntimeError, match="exists but automation"):
            await provisioner.provision(_request(expected_head=partial_head))
        monkeypatch.setattr(automation, "create", original_create)

        recovered = await provisioner.provision(_request(expected_head=partial_head))

        assert recovered.page_created is False
        assert recovered.channel_created is False
        assert recovered.automation_created is True
        assert recovered.channel_id == channel.state.session_id
    finally:
        await session_store.conn.close()
        await automation_store.conn.close()


@pytest.mark.asyncio
async def test_provisioner_rejects_changed_contract_foreign_task_invalid_scope_and_stale_head(tmp_path: Path) -> None:
    wiki, automation, sessions, automation_store, session_store = await _services(tmp_path)
    try:
        provisioner = WikiProducerProvisioner(wiki, automation, sessions, _registry().tools)
        created = await provisioner.provision(_request(expected_head=None))

        with pytest.raises(ValueError, match="different producer contract"):
            await provisioner.provision(_request(expected_head=created.head, prompt="Different request."))
        with pytest.raises(ValueError, match="tool_scope patterns"):
            await WikiProducerProvisioner(
                wiki, automation, sessions, ("read_wiki_page", "publish_wiki_generated")
            ).provision(_request(expected_head=created.head))
        with pytest.raises(ValueError, match="exact tool names"):
            await provisioner.provision(
                replace(
                    _request(expected_head=created.head),
                    source_tool_scope=("gmail_*",),
                )
            )
        with pytest.raises(RuntimeError, match="wiki changed"):
            await provisioner.provision(_request(expected_head="0" * 64))
    finally:
        await session_store.conn.close()
        await automation_store.conn.close()


@pytest.mark.asyncio
async def test_provisioner_validates_schedule_before_creating_the_page(tmp_path: Path) -> None:
    wiki, automation, sessions, automation_store, session_store = await _services(tmp_path)
    try:
        provisioner = WikiProducerProvisioner(wiki, automation, sessions, _registry().tools)
        invalid = replace(_request(expected_head=None), every="1h")

        with pytest.raises(ValueError, match="mutually exclusive"):
            await provisioner.provision(invalid)

        assert wiki.repository.head is None
    finally:
        await session_store.conn.close()
        await automation_store.conn.close()


@pytest.mark.asyncio
async def test_provisioner_normalizes_schedule_identity_and_validates_model_before_writes(tmp_path: Path) -> None:
    wiki, automation, sessions, automation_store, session_store = await _services(tmp_path)
    try:
        provisioner = WikiProducerProvisioner(wiki, automation, sessions, _registry().tools)
        invalid_model = replace(_request(expected_head=None), model="not-a-real-model")
        with pytest.raises(ValueError, match="Unknown model"):
            await provisioner.provision(invalid_model)
        with pytest.raises(ValueError, match="unit-separator"):
            await provisioner.provision(replace(_request(expected_head=None), page_id="bad\x1fid"))
        assert wiki.repository.head is None
        assert await automation_store.list_all() == []
        assert await session_store.load_session("wiki-producer:email-updates:channel") is None

        created = await provisioner.provision(replace(_request(expected_head=None), at="8:00", days="sun, mon"))
        replayed = await provisioner.provision(
            replace(_request(expected_head=created.head), at="08:00", days="mon,sun")
        )
        assert replayed.automation_created is False
        assert replayed.head == created.head
    finally:
        await session_store.conn.close()
        await automation_store.conn.close()


@pytest.mark.asyncio
async def test_provisioner_replays_message_trigger_by_resolved_identity(tmp_path: Path) -> None:
    wiki, automation, sessions, automation_store, session_store = await _services(tmp_path)
    try:
        automation._get_slack_client = lambda: _FakeSlack()
        provisioner = WikiProducerProvisioner(wiki, automation, sessions, _registry().tools)
        created = await provisioner.provision(_message_request(expected_head=None))
        replayed = await provisioner.provision(
            replace(
                _message_request(expected_head=created.head),
                channels=("random", "design"),
                from_user="Ada",
                contains=("URGENT", "launch"),
            )
        )

        assert replayed.automation_created is False
        task = await automation_store.get(created.automation_id)
        assert task is not None
        assert task.triggers[0].params()["channels"] == [
            {"id": "C1", "name": "design"},
            {"id": "C2", "name": "random"},
        ]
        assert task.triggers[0].params()["contains"] == ["launch", "urgent"]
    finally:
        await session_store.conn.close()
        await automation_store.conn.close()


@pytest.mark.asyncio
async def test_provisioner_rechecks_current_head_after_channel_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki, automation, sessions, automation_store, session_store = await _services(tmp_path)
    try:
        provisioner = WikiProducerProvisioner(wiki, automation, sessions, _registry().tools)
        original = provisioner._ensure_channel

        async def create_channel_then_edit_page(channel_id: str, automation_id: str, name: str) -> bool:
            created = await original(channel_id, automation_id, name)
            snapshot = wiki.snapshot()
            record = next(page for page in snapshot.pages if page.page.page_id == "email-updates")
            wiki.update_page(
                record.page.page_id,
                content=update_page_metadata(
                    record.content,
                    expected_page_id=record.page.page_id,
                    updates={"raced": True},
                ),
                expected_version=record.resource.version_id,
                expected_head=snapshot.head,
            )
            return created

        monkeypatch.setattr(provisioner, "_ensure_channel", create_channel_then_edit_page)
        with pytest.raises(RevisionConflictError, match="wiki changed"):
            await provisioner.provision(_request(expected_head=None))
        assert await automation_store.get("wiki-producer:email-updates") is None
    finally:
        await session_store.conn.close()
        await automation_store.conn.close()


@pytest.mark.asyncio
async def test_provisioner_disables_task_when_page_changes_during_enable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki, automation, sessions, automation_store, session_store = await _services(tmp_path)
    try:
        provisioner = WikiProducerProvisioner(wiki, automation, sessions, _registry().tools)
        original = automation_store.set_enabled_if_claim
        injected = False

        async def enable_then_edit_page(
            task_id: str,
            claim_key: str,
            *,
            expected: bool,
            enabled: bool,
        ) -> bool:
            nonlocal injected
            changed = await original(
                task_id,
                claim_key,
                expected=expected,
                enabled=enabled,
            )
            if not changed or not enabled or injected:
                return changed
            injected = True
            snapshot = wiki.snapshot()
            record = next(page for page in snapshot.pages if page.page.page_id == "email-updates")
            wiki.update_page(
                record.page.page_id,
                content=update_generated_region(
                    record.content,
                    expected_page_id=record.page.page_id,
                    generated=b"raced\n",
                ),
                expected_version=record.resource.version_id,
                expected_head=snapshot.head,
            )
            return changed

        monkeypatch.setattr(automation_store, "set_enabled_if_claim", enable_then_edit_page)
        with pytest.raises(RevisionConflictError, match="wiki changed"):
            await provisioner.provision(_request(expected_head=None))

        task = await automation_store.get("wiki-producer:email-updates")
        assert task is not None
        assert task.enabled is False
    finally:
        await session_store.conn.close()
        await automation_store.conn.close()


@pytest.mark.parametrize("mutation", ["fact_citations", "generated"])
@pytest.mark.asyncio
async def test_provisioner_rejects_changed_page_only_partial_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    wiki, automation, sessions, automation_store, session_store = await _services(tmp_path)
    try:
        provisioner = WikiProducerProvisioner(wiki, automation, sessions, _registry().tools)

        async def fail_create(**kwargs):
            del kwargs
            raise RuntimeError("automation store unavailable")

        monkeypatch.setattr(automation, "create", fail_create)
        with pytest.raises(WikiProducerPartialProvisionError):
            await provisioner.provision(_request(expected_head=None))

        snapshot = wiki.snapshot()
        record = next(page for page in snapshot.pages if page.page.page_id == "email-updates")
        content = (
            update_page_metadata(
                record.content,
                expected_page_id=record.page.page_id,
                updates={"fact_citations": []},
            )
            if mutation == "fact_citations"
            else update_generated_region(
                record.content,
                expected_page_id=record.page.page_id,
                generated=b"user edit\n",
            )
        )
        wiki.update_page(
            record.page.page_id,
            content=content,
            expected_version=record.resource.version_id,
            expected_head=snapshot.head,
        )

        with pytest.raises(WikiProducerProvisionConflictError):
            await provisioner.provision(_request(expected_head=wiki.repository.head))
        assert await automation_store.get("wiki-producer:email-updates") is None
    finally:
        await session_store.conn.close()
        await automation_store.conn.close()


@pytest.mark.asyncio
async def test_provisioner_does_not_overwrite_foreign_channel_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki, automation, sessions, automation_store, session_store = await _services(tmp_path)
    try:
        provisioner = WikiProducerProvisioner(wiki, automation, sessions, _registry().tools)
        original = session_store.create_session_if_absent
        injected = False

        async def insert_foreign_then_create(state, messages=None, metadata=None):
            nonlocal injected
            if not injected:
                injected = True
                foreign = sessions.create(name="Foreign", session_type="chat", session_id=state.session_id)
                await session_store.save_session(foreign, [])
            return await original(state, messages, metadata)

        monkeypatch.setattr(session_store, "create_session_if_absent", insert_foreign_then_create)
        with pytest.raises(WikiProducerProvisionConflictError, match="another owner"):
            await provisioner.provision(_request(expected_head=None))

        channel = await session_store.load_session("wiki-producer:email-updates:channel")
        assert channel is not None
        assert channel.state.name == "Foreign"
        assert channel.state.session_type == "chat"
        assert channel.state.origin_automation_id is None
    finally:
        await session_store.conn.close()
        await automation_store.conn.close()


@pytest.mark.asyncio
async def test_provisioner_does_not_overwrite_foreign_automation_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki, automation, sessions, automation_store, session_store = await _services(tmp_path)
    try:
        provisioner = WikiProducerProvisioner(wiki, automation, sessions, _registry().tools)
        original = automation.create

        async def insert_foreign_then_create(**kwargs):
            await automation_store.save(_foreign_automation(kwargs["task_id"], kwargs["thread_id"]))
            return await original(**kwargs)

        monkeypatch.setattr(automation, "create", insert_foreign_then_create)
        with pytest.raises(WikiProducerProvisionConflictError, match="different producer contract"):
            await provisioner.provision(_request(expected_head=None))

        task = await automation_store.get("wiki-producer:email-updates")
        assert task is not None
        assert task.name == "Foreign task"
        assert task.prompt == "Do something else."
    finally:
        await session_store.conn.close()
        await automation_store.conn.close()


@pytest.mark.asyncio
async def test_provisioner_does_not_enable_or_disable_a_foreign_task_during_enable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki, automation, sessions, automation_store, session_store = await _services(tmp_path)
    try:
        provisioner = WikiProducerProvisioner(wiki, automation, sessions, _registry().tools)
        original = automation_store.set_enabled_if_claim
        injected = False

        async def replace_before_enable(
            task_id: str,
            claim_key: str,
            *,
            expected: bool,
            enabled: bool,
        ) -> bool:
            nonlocal injected
            if enabled and not injected:
                injected = True
                await automation_store.save(_foreign_automation(task_id, "wiki-producer:email-updates:channel"))
            return await original(
                task_id,
                claim_key,
                expected=expected,
                enabled=enabled,
            )

        monkeypatch.setattr(automation_store, "set_enabled_if_claim", replace_before_enable)
        with pytest.raises(WikiProducerProvisionConflictError):
            await provisioner.provision(_request(expected_head=None))

        task = await automation_store.get("wiki-producer:email-updates")
        assert task is not None
        assert task.name == "Foreign task"
        assert task.enabled is True
    finally:
        await session_store.conn.close()
        await automation_store.conn.close()


def test_producer_source_scope_requires_exact_literals() -> None:
    for scope in (["*"], ["gmail_*"]):
        with pytest.raises(ValueError, match="exact tool names"):
            ProvisionWikiProducerInput(
                page_id="email-updates",
                path="feeds/email-updates.md",
                title="Email Updates",
                automation_name="Email update feed",
                prompt="Read email.",
                trigger_type="time",
                at="08:00",
                source_tool_scope=scope,
            )


def test_provisioner_rejects_a_changed_message_trigger_contract() -> None:
    request = WikiProducerRequest(
        page_id="slack-feed",
        path="feeds/slack.md",
        title="Slack feed",
        aliases=(),
        automation_name="Slack feed",
        prompt="Read Slack.",
        model=None,
        trigger_type="message",
        at=None,
        days=None,
        every=None,
        start=None,
        end=None,
        event_type=None,
        lead_minutes=None,
        channels=("design",),
        from_user="Ada",
        contains=("launch",),
        source_tool_scope=("gmail_search",),
        expected_head=None,
    )
    task = Automation(
        task_id="wiki-producer:slack-feed",
        name="Slack feed",
        description="Slack feed",
        description_source="manual",
        prompt="Read Slack.",
        model=None,
        triggers=[
            MessageTrigger(
                channels=[{"id": "C1", "name": "design"}],
                from_user_id="U1",
                from_user_name="Ada",
                contains=["different"],
            )
        ],
        enabled=True,
        created_at=datetime.now(UTC),
        next_run_at=None,
        last_run_at=None,
        last_result=None,
        running_since=None,
        auto_approve=True,
        thread_id="wiki-producer:slack-feed:channel",
        read_history=True,
        tool_scope=["gmail_search", "publish_wiki_generated", "read_wiki_page"],
        idempotency_key="wiki-producer:fingerprint",
        idempotency_scope="global",
    )

    with pytest.raises(WikiProducerProvisionConflictError, match="different producer contract"):
        WikiProducerProvisioner._validate_automation(
            task,
            request,
            "wiki-producer:slack-feed",
            "wiki-producer:slack-feed:channel",
            ("gmail_search", "publish_wiki_generated", "read_wiki_page"),
            "fingerprint",
            {
                "type": "message",
                "source": "slack",
                "channel_ids": ["C1"],
                "from_user_id": "U1",
                "contains": ["launch"],
            },
        )


@pytest.mark.asyncio
async def test_provision_tool_previews_both_artifacts_and_returns_structured_result(tmp_path: Path) -> None:
    wiki, automation, sessions, automation_store, session_store = await _services(tmp_path)
    try:
        context = ToolContext(
            session_state=SessionState(session_id="producer-tools", started_at=datetime.now(UTC)),
            registry=_registry(),
            run=RunContext(run_id="run-1"),
            io=IOBridge(),
            services={
                "wiki": wiki,
                "automation": automation,
                "wiki_producer": WikiProducerProvisioner(wiki, automation, sessions, _registry().tools),
            },
            background_tasks=BackgroundTaskRegistry(session_id="producer-tools"),
        )
        execution = ToolExecution(tool_id="tool-1", tool_name="provision_wiki_producer", ctx=context)
        args = {
            "page_id": "email-updates",
            "path": "feeds/email-updates.md",
            "title": "Email Updates",
            "aliases": ["Email feed"],
            "automation_name": "Email update feed",
            "prompt": "Read email and update this feed.",
            "trigger_type": "time",
            "at": "08:00",
            "days": "daily",
            "source_tool_scope": ["gmail_search"],
            "expected_head": None,
        }

        approval = await provision_wiki_producer_tool.approval_info(execution, **args)
        result = await provision_wiki_producer_tool.execute(execution, **args)

        assert approval is not None
        assert "Auto-approve: true" in approval.preview
        assert "Title: Email Updates" in approval.preview
        assert "Aliases: Email feed" in approval.preview
        assert "at=08:00" in approval.preview
        assert "days=daily" in approval.preview
        assert "every=none" in approval.preview
        assert "start=none" in approval.preview
        assert "end=none" in approval.preview
        assert "Exact tools:" in approval.preview
        assert "read_wiki_page" in approval.preview
        assert "Read email and update this feed." in approval.preview
        assert result.data["page"]["page_id"] == "email-updates"
        assert result.data["automation"]["task_id"] == "wiki-producer:email-updates"
        assert result.data["recovery"] == {
            "page_created": True,
            "channel_created": True,
            "automation_created": True,
        }
        assert "provision_wiki_producer" in WIKI.tools
        assert provision_wiki_producer_tool.policy.idempotent is True

        message_approval = await provision_wiki_producer_tool.approval_info(
            execution,
            page_id="slack-feed",
            path="feeds/slack.md",
            title="Slack feed",
            automation_name="Slack feed",
            prompt="Read Slack.",
            trigger_type="message",
            channels=["design", "random"],
            from_user="Ada",
            contains=["launch", "urgent"],
            source_tool_scope=["gmail_search"],
            expected_head=result.data["page"]["head"],
        )
        assert message_approval is not None
        assert "channels=design, random" in message_approval.preview
        assert "from_user=Ada" in message_approval.preview
        assert "contains=launch, urgent" in message_approval.preview

        context.run.automation_id = "legacy-wildcard-automation"
        blocked = await provision_wiki_producer_tool.execute(
            execution,
            page_id="blocked-feed",
            path="feeds/blocked.md",
            title="Blocked",
            automation_name="Blocked",
            prompt="Do not run.",
            trigger_type="time",
            at="09:00",
            source_tool_scope=["gmail_search"],
            expected_head=result.data["page"]["head"],
        )
        assert blocked.outcome.error.code == "interactive_required"
    finally:
        await session_store.conn.close()
        await automation_store.conn.close()
