import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

import arden.tools.wiki as wiki_tools
from arden.context.models import SessionState
from arden.integrations.core import CORE_INTEGRATIONS, WIKI
from arden.revisions import ManagedFileRepository
from arden.services.session import SessionService
from arden.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry
from arden.tools.wiki import (
    ReadWikiPageInput,
    archive_wiki_page_tool,
    create_wiki_page_tool,
    edit_wiki_page_tool,
    list_wiki_pages_tool,
    move_wiki_page_tool,
    publish_wiki_generated_tool,
    read_wiki_page_tool,
    wiki_links_tool,
)
from arden.wiki.constants import WIKI_POST_COMMIT_SERVICE
from arden.wiki.models import GeneratedPageTarget
from arden.wiki.pages import extract_generated_region
from arden.wiki.service import WikiAmbiguityError, WikiService


def _execution(
    wiki: WikiService | None,
    *,
    automation_id: str | None = None,
    post_commit: Callable[[], Awaitable[bool]] | None = None,
    session: SessionService | None = None,
) -> ToolExecution:
    services = {} if wiki is None else {"wiki": wiki}
    if wiki is not None:

        async def project_wiki() -> bool:
            return False

        services[WIKI_POST_COMMIT_SERVICE] = post_commit or project_wiki
    if session is not None:
        services["session"] = session
    context = ToolContext(
        session_state=SessionState(session_id="wiki-tools", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1", automation_id=automation_id),
        io=IOBridge(),
        services=services,
        background_tasks=BackgroundTaskRegistry(session_id="wiki-tools"),
    )
    return ToolExecution(tool_id="wiki-1", tool_name="read_wiki_page", ctx=context)


class _AreaStore:
    def __init__(self, area: dict | None = None) -> None:
        self.area = area

    async def find_area_by_page_id(self, _page_id: str) -> dict | None:
        return self.area


def _wiki(root: Path) -> WikiService:
    wiki = WikiService(ManagedFileRepository(root / "wiki" / "pages", history_root=root / "wiki" / ".wiki-history"))
    wiki.create_page(
        path="topics/interaction-lab.md",
        title="Interaction Lab",
        aliases=("Lab",),
        body=b"# Interaction Lab\n\n[[Peer]]\n[[Missing]]\n",
        page_id="interaction-lab",
        expected_head=None,
        actor="test",
        origin="test",
        reason="seed",
    )
    wiki.create_page(
        path="topics/peer.md",
        title="Peer",
        body=b"[[Lab]]\n",
        page_id="peer",
        expected_head=wiki.repository.head,
        actor="test",
        origin="test",
        reason="seed",
    )
    return wiki


@pytest.mark.asyncio
async def test_list_wiki_pages_replaces_folder_index_pages(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    run = _execution(wiki)
    wiki.create_page(
        path="about.md",
        title="About",
        page_id="about",
        expected_head=wiki.repository.head,
    )
    wiki.create_page(
        path="topics/labs/deep.md",
        title="Deep",
        page_id="deep",
        expected_head=wiki.repository.head,
    )
    archived = wiki.create_page(
        path="topics/retired.md",
        title="Retired",
        page_id="retired",
        expected_head=wiki.repository.head,
    )
    wiki.archive_page(
        "retired",
        expected_version=archived.resource.version_id,
        base_head=wiki.repository.head,
    )

    root = await list_wiki_pages_tool.execute(run)
    assert root.data == {
        "head": wiki.repository.head,
        "directory": "",
        "offset": 0,
        "entries": [
            {"kind": "directory", "path": "topics/"},
            {
                "kind": "page",
                "page_id": "about",
                "path": "about.md",
                "title": "About",
                "lifecycle": "active",
                "version": wiki.read_page("about").resource.version_id,
            },
        ],
        "total": 2,
        "has_more": False,
        "next_offset": None,
    }

    topics = await list_wiki_pages_tool.execute(run, directory="topics")
    assert [(entry["kind"], entry["path"], entry.get("title")) for entry in topics.data["entries"]] == [
        ("directory", "topics/labs/", None),
        ("page", "topics/README.md", "Topics guide"),
        ("page", "topics/interaction-lab.md", "Interaction Lab"),
        ("page", "topics/peer.md", "Peer"),
    ]
    assert "topics/README.md" in topics.content and "retired.md" not in topics.content

    capped = await list_wiki_pages_tool.execute(run, directory="topics", limit=1)
    assert capped.data["has_more"] is True
    assert len(capped.data["entries"]) == 1
    assert capped.data["next_offset"] == 1
    continued = await list_wiki_pages_tool.execute(
        run,
        directory="topics",
        offset=capped.data["next_offset"],
        limit=1,
    )
    assert continued.data["entries"][0]["path"] == "topics/README.md"
    assert continued.data["next_offset"] == 2

    for directory in (".", "./topics", "topics/.", "../topics", "topics//labs", "/topics", r"topics\labs"):
        invalid = await list_wiki_pages_tool.execute(run, directory=directory)
        assert invalid.is_error
        assert invalid.outcome is not None and invalid.outcome.error is not None
        assert invalid.outcome.error.code == "invalid_directory"

    missing = await list_wiki_pages_tool.execute(run, directory="missing")
    assert missing.is_error
    assert missing.outcome is not None and missing.outcome.error is not None
    assert missing.outcome.error.code == "not_found"


@pytest.mark.asyncio
async def test_list_wiki_pages_reports_byte_budget_truncation(tmp_path: Path, monkeypatch) -> None:
    wiki = _wiki(tmp_path)
    monkeypatch.setattr(wiki_tools, "_MAX_LIST_DATA_BYTES", 1)

    result = await list_wiki_pages_tool.execute(_execution(wiki), directory="topics")

    assert result.data["entries"] == []
    assert result.data["total"] == 3
    assert result.data["has_more"] is True
    assert result.data["next_offset"] is None


@pytest.mark.asyncio
async def test_list_wiki_pages_continues_after_byte_budget_page(tmp_path: Path, monkeypatch) -> None:
    wiki = _wiki(tmp_path)
    first = wiki_tools._listing_page_data(
        next(record for record in wiki.snapshot().pages if record.resource.path == "topics/README.md")
    )
    monkeypatch.setattr(
        wiki_tools,
        "_MAX_LIST_DATA_BYTES",
        len(json.dumps(first, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1,
    )

    first_page = await list_wiki_pages_tool.execute(_execution(wiki), directory="topics")

    assert [entry["path"] for entry in first_page.data["entries"]] == ["topics/README.md"]
    assert first_page.data["next_offset"] == 1
    second_page = await list_wiki_pages_tool.execute(
        _execution(wiki), directory="topics", offset=first_page.data["next_offset"]
    )
    assert [entry["path"] for entry in second_page.data["entries"]] == ["topics/interaction-lab.md"]
    assert second_page.data["next_offset"] == 2
    third_page = await list_wiki_pages_tool.execute(
        _execution(wiki),
        directory="topics",
        offset=second_page.data["next_offset"],
        limit=1,
    )
    assert [entry["path"] for entry in third_page.data["entries"]] == ["topics/peer.md"]
    assert third_page.data["has_more"] is False


@pytest.mark.asyncio
async def test_read_wiki_page_resolves_each_exact_identity_and_returns_revision_metadata(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    run = _execution(wiki)

    for selector in (
        {"page_id": "interaction-lab"},
        {"path": "topics/interaction-lab.md"},
        {"title": "Interaction Lab"},
        {"alias": "Lab"},
    ):
        result = await read_wiki_page_tool.execute(run, **selector, limit=2)

        assert not result.is_error
        assert "[" in result.content and "lines" in result.content
        assert f'"version":"{wiki.read_page("interaction-lab").resource.version_id}"' in result.content
        assert f'"head":"{wiki.repository.head}"' in result.content
        assert "Wiki page content:" in result.content
        assert result.data["page"] == {
            "page_id": "interaction-lab",
            "path": "topics/interaction-lab.md",
            "title": "Interaction Lab",
            "aliases": ["Lab"],
            "lifecycle": "active",
            "version": wiki.read_page("interaction-lab").resource.version_id,
            "head": wiki.repository.head,
        }
        assert result.data["content_truncated"] is False
        assert [(ref.kind, ref.ref) for ref in result.source_refs] == [("wiki_page", "interaction-lab")]


@pytest.mark.asyncio
async def test_read_wiki_page_bounds_content_and_reports_missing_or_ambiguous_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _wiki(tmp_path)
    wiki.create_page(
        path="topics/long.md",
        title="Long",
        body=b"x" * 50_000,
        page_id="long",
        expected_head=wiki.repository.head,
        actor="test",
        origin="test",
        reason="seed",
    )
    run = _execution(wiki)

    bounded = await read_wiki_page_tool.execute(run, page_id="long", limit=100)
    assert len(bounded.content) <= 40_000
    assert bounded.data["content_truncated"] is True

    missing = await read_wiki_page_tool.execute(run, title="Unknown")
    assert missing.is_error
    assert missing.outcome is not None and missing.outcome.error is not None
    assert missing.outcome.error.code == "not_found"

    def ambiguous_snapshot():
        raise WikiAmbiguityError("ambiguous wiki name 'lab': a, b")

    monkeypatch.setattr(wiki, "snapshot", ambiguous_snapshot)
    ambiguous = await read_wiki_page_tool.execute(run, alias="Lab")
    assert ambiguous.is_error
    assert ambiguous.outcome is not None and ambiguous.outcome.error is not None
    assert ambiguous.outcome.error.code == "ambiguous_ref"

    with pytest.raises(ValidationError, match="exactly one"):
        ReadWikiPageInput(page_id="interaction-lab", title="Interaction Lab")


@pytest.mark.asyncio
async def test_wiki_links_returns_resolved_unresolved_and_backlink_context(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    result = await wiki_links_tool.execute(_execution(wiki), alias="Lab")

    assert not result.is_error
    assert result.data["page"]["page_id"] == "interaction-lab"
    assert result.data["outgoing_total"] == 2
    assert result.data["backlinks_total"] == 1
    assert result.data["outgoing"][0]["target_page_id"] == "peer"
    assert result.data["outgoing"][0]["status"] == "resolved"
    assert result.data["outgoing"][1]["status"] == "unresolved"
    assert result.data["backlinks"] == [
        {
            "source_page_id": "peer",
            "target_page_id": "interaction-lab",
            "status": "resolved",
            "candidates": [],
            "page": "Lab",
            "fragment": None,
            "alias": None,
            "embed": False,
        }
    ]
    assert [(ref.kind, ref.ref) for ref in result.source_refs] == [("wiki_page", "interaction-lab")]


@pytest.mark.asyncio
async def test_wiki_links_stays_on_the_selector_snapshot_during_a_concurrent_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wiki = _wiki(tmp_path)
    selected_head = wiki.repository.head
    original = wiki.link_report

    def concurrent_report(page_id: str, *, at: str | None = None):
        wiki.create_page(page_id="later", path="topics/later.md", title="Later")
        return original(page_id, at=at)

    monkeypatch.setattr(wiki, "link_report", concurrent_report)
    result = await wiki_links_tool.execute(_execution(wiki), page_id="interaction-lab")

    assert not result.is_error
    assert result.data["page"]["head"] == selected_head
    assert wiki.repository.head != selected_head


@pytest.mark.asyncio
async def test_wiki_links_bounds_the_complete_structured_payload(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    body = "\n".join(f"[[Missing {index}|{'x' * 5_000}]]" for index in range(30)).encode()
    wiki.create_page(page_id="large-links", path="topics/large-links.md", title="Large links", body=body)

    result = await wiki_links_tool.execute(_execution(wiki), page_id="large-links", limit=500)

    assert not result.is_error
    assert result.data["links_truncated"] is True
    assert result.data["fields_truncated"] is True
    assert len(json.dumps(result.data, ensure_ascii=False, separators=(",", ":")).encode()) <= 40_000


@pytest.mark.asyncio
async def test_create_wiki_page_creates_directory_readmes_with_the_first_child(tmp_path: Path) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "wiki"))
    execution = _execution(wiki)
    child = {
        "page_id": "coast-2026-07-30",
        "path": "automations/coast/2026-07-30.md",
        "title": "Coast digest 2026-07-30",
        "aliases": [],
        "body": "Digest.\n",
        "expected_head": None,
    }

    created = await create_wiki_page_tool.execute(execution, **child)
    replayed = await create_wiki_page_tool.execute(execution, **child)

    assert not created.is_error
    assert created.data["changed"] is True
    assert replayed.data["changed"] is False
    assert {record.resource.path for record in wiki.snapshot().pages} == {
        "automations/README.md",
        "automations/coast/README.md",
        "automations/coast/2026-07-30.md",
    }
    commit = wiki.repository.history(limit=1)[0]
    assert len(commit.changes) == 3
    coast_readme = next(
        record for record in wiki.snapshot().pages if record.resource.path == "automations/coast/README.md"
    )
    assert b"## Purpose" in coast_readme.content


@pytest.mark.asyncio
async def test_agent_wiki_writes_are_cas_safe_and_semantically_idempotent(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    execution = _execution(wiki)
    create_args = {
        "page_id": "daily-note",
        "path": "daily/2026-07-29.md",
        "title": "2026-07-29",
        "aliases": [],
        "body": "# 2026-07-29\n\nInitial.\n",
        "expected_head": wiki.repository.head,
    }

    created = await create_wiki_page_tool.execute(execution, **create_args)
    assert not created.is_error
    assert created.data["changed"] is True
    assert created.data["page"]["path"] == "daily/2026-07-29.md"

    replayed = await create_wiki_page_tool.execute(execution, **create_args)
    assert not replayed.is_error
    assert replayed.data["changed"] is False

    conflict = await create_wiki_page_tool.execute(execution, **{**create_args, "body": "Different.\n"})
    assert conflict.is_error
    assert conflict.outcome.error.code == "revision_conflict"

    name_conflict = await create_wiki_page_tool.execute(
        execution,
        **{
            **create_args,
            "page_id": "daily-note-copy",
            "path": "daily/2026-07-29-copy.md",
            "expected_head": wiki.repository.head,
        },
    )
    assert name_conflict.is_error
    assert name_conflict.outcome.error.code == "name_conflict"
    assert name_conflict.outcome.error.recovery_action is not None

    missing = await edit_wiki_page_tool.execute(
        execution,
        page_id="missing",
        body="No.",
        expected_version="a" * 64,
        expected_head=wiki.repository.head,
    )
    assert missing.is_error
    assert missing.outcome.error.code == "not_found"
    assert missing.outcome.error.recovery_action is not None

    record = wiki.read_page("daily-note")
    edit_args = {
        "page_id": record.page.page_id,
        "body": "# 2026-07-29\n\nUpdated.\n",
        "expected_version": record.resource.version_id,
        "expected_head": wiki.repository.head,
    }
    edited = await edit_wiki_page_tool.execute(execution, **edit_args)
    assert not edited.is_error
    assert edited.data["changed"] is True
    assert wiki.read_page("daily-note").page.body == edit_args["body"].encode()

    replayed_edit = await edit_wiki_page_tool.execute(execution, **edit_args)
    assert not replayed_edit.is_error
    assert replayed_edit.data["changed"] is False

    stale_edit = await edit_wiki_page_tool.execute(execution, **{**edit_args, "body": "Stale overwrite.\n"})
    assert stale_edit.is_error
    assert stale_edit.outcome.error.code == "revision_conflict"

    current = wiki.read_page("daily-note")
    archive_args = {
        "page_id": current.page.page_id,
        "expected_version": current.resource.version_id,
        "expected_head": wiki.repository.head,
        "reason": "retention window elapsed",
    }
    archived = await archive_wiki_page_tool.execute(execution, **archive_args)
    assert not archived.is_error
    assert archived.data["changed"] is True
    assert archived.data["page"]["lifecycle"] == "archived"

    replayed_archive = await archive_wiki_page_tool.execute(execution, **archive_args)
    assert not replayed_archive.is_error
    assert replayed_archive.data["changed"] is False


@pytest.mark.asyncio
async def test_wiki_write_results_return_head_after_projection(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    projection_count = 0

    async def project_wiki() -> bool:
        nonlocal projection_count
        projection_count += 1
        wiki.create_page(
            page_id=f"projection-{projection_count}",
            path=f"projection-{projection_count}.md",
            title=f"Projection {projection_count}",
            expected_head=wiki.repository.head,
        )
        return False

    created = await create_wiki_page_tool.execute(
        _execution(wiki, post_commit=project_wiki),
        page_id="automation-result",
        path="automations/result.md",
        title="Automation result",
        body="Initial.\n",
        expected_head=wiki.repository.head,
    )
    assert not created.is_error
    assert created.data["commit_id"] != wiki.repository.head
    assert created.data["page"]["head"] == wiki.repository.head

    wiki.publish_generated(
        (
            GeneratedPageTarget(
                page_id="generated-result",
                path="automations/generated.md",
                title="Generated result",
                aliases=(),
                generated=b"Old.\n",
                metadata={"producer_automation_id": "producer"},
            ),
        ),
        source_revision="a" * 64,
        base_head=wiki.repository.head,
        actor="Automation producer",
        origin="wiki.automation.producer",
    )
    generated = wiki.read_page("generated-result")
    published = await publish_wiki_generated_tool.execute(
        _execution(wiki, automation_id="producer", post_commit=project_wiki),
        page_id=generated.page.page_id,
        generated="New.",
        expected_version=generated.resource.version_id,
        expected_head=wiki.repository.head,
    )
    assert not published.is_error
    assert published.data["commit_id"] != wiki.repository.head
    assert published.data["page"]["head"] == wiki.repository.head


@pytest.mark.asyncio
async def test_scheduled_automation_writes_stay_in_automation_workspace(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    automation = _execution(wiki, automation_id="daily-digest")
    original_head = wiki.repository.head

    denied = await create_wiki_page_tool.execute(
        automation,
        page_id="outside",
        path="daily/outside.md",
        title="Outside",
        body="No.",
        expected_head=original_head,
    )
    assert denied.is_error
    assert denied.outcome.error.code == "automation_path_denied"
    assert wiki.repository.head == original_head

    for path in ("automations-old/outside.md", "automations/../topics/outside.md", r"automations\outside.md"):
        invalid = await create_wiki_page_tool.execute(
            automation,
            page_id=f"invalid-{len(path)}",
            path=path,
            title=f"Invalid {path}",
            body="No.",
            expected_head=wiki.repository.head,
        )
        assert invalid.is_error
        assert wiki.repository.head == original_head

    created = await create_wiki_page_tool.execute(
        automation,
        page_id="daily-digest",
        path="automations/daily-digest.md",
        title="Daily digest",
        body="First.\n",
        expected_head=wiki.repository.head,
    )
    assert not created.is_error
    record = wiki.read_page("daily-digest")

    denied_edit = await edit_wiki_page_tool.execute(
        automation,
        page_id="interaction-lab",
        body="No.",
        expected_version=wiki.read_page("interaction-lab").resource.version_id,
        expected_head=wiki.repository.head,
    )
    assert denied_edit.is_error
    assert denied_edit.outcome.error.code == "automation_path_denied"

    edited = await edit_wiki_page_tool.execute(
        automation,
        page_id=record.page.page_id,
        body="Second.\n",
        expected_version=record.resource.version_id,
        expected_head=wiki.repository.head,
    )
    assert not edited.is_error

    current = wiki.read_page("daily-digest")
    archived = await archive_wiki_page_tool.execute(
        automation,
        page_id=current.page.page_id,
        expected_version=current.resource.version_id,
        expected_head=wiki.repository.head,
        reason="replace rolling result",
    )
    assert not archived.is_error


@pytest.mark.asyncio
async def test_agent_wiki_writes_preserve_backend_and_producer_boundaries(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    interactive = _execution(wiki)
    automation = _execution(wiki, automation_id="owner")

    health = await create_wiki_page_tool.execute(
        interactive,
        page_id="health",
        path="health.md",
        title="Health",
        body="No.",
        expected_head=wiki.repository.head,
    )
    assert health.is_error
    assert health.outcome.error.code == "invalid_page"
    assert health.outcome.error.recovery_action is not None

    producer = wiki.create_page(
        page_id="producer",
        path="automations/producer.md",
        title="Producer",
        body=b"<!-- generated -->\nCurrent.\n<!-- /generated -->\n",
        metadata={"producer_automation_id": "owner"},
        expected_head=wiki.repository.head,
    )
    edit = await edit_wiki_page_tool.execute(
        automation,
        page_id=producer.page.page_id,
        body="Overwrite.",
        expected_version=producer.resource.version_id,
        expected_head=wiki.repository.head,
    )
    assert edit.is_error
    assert edit.outcome.error.code == "producer_page_requires_generated_publish"

    archive = await archive_wiki_page_tool.execute(
        automation,
        page_id=producer.page.page_id,
        expected_version=producer.resource.version_id,
        expected_head=wiki.repository.head,
        reason="No.",
    )
    assert archive.is_error
    assert archive.outcome.error.code == "producer_page_requires_generated_publish"


@pytest.mark.asyncio
async def test_registered_feed_producer_updates_only_its_generated_region(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    producer_id = "email-feed-worker"
    wiki.publish_generated(
        (
            GeneratedPageTarget(
                page_id="email-feed",
                path="automations/email-updates.md",
                title="Email Updates",
                aliases=(),
                generated=b"Old briefing.\n",
                metadata={"producer_automation_id": producer_id},
            ),
        ),
        source_revision="a" * 64,
        base_head=wiki.repository.head,
        actor=f"Automation {producer_id}",
        origin=f"wiki.automation.{producer_id}",
    )
    created = wiki.read_page("email-feed")
    created = wiki.update_page(
        created.page.page_id,
        content=created.content + b"\n## User notes\nKeep this.\n",
        expected_version=created.resource.version_id,
        expected_head=wiki.repository.head,
    )

    result = await publish_wiki_generated_tool.execute(
        _execution(wiki, automation_id=producer_id),
        page_id=created.page.page_id,
        generated="# Email Updates\n\nNew briefing.",
        expected_version=created.resource.version_id,
        expected_head=wiki.repository.head,
    )

    assert not result.is_error
    assert result.data["changed"] is True
    updated = wiki.read_page("email-feed")
    assert extract_generated_region(updated.content, expected_page_id="email-feed") == (
        b"# Email Updates\n\nNew briefing.\n"
    )
    assert b"## User notes\nKeep this.\n" in updated.content
    commit = wiki.repository.history(limit=1)[0]
    assert commit.actor == "Automation email-feed-worker"
    assert commit.origin == "wiki.automation.email-feed-worker"
    assert len(updated.page.metadata["generated_from_revision"]) == 64
    assert "invalid_generated_from_revision" not in {warning.code for warning in wiki.changes_since(None).warnings}

    unchanged = await publish_wiki_generated_tool.execute(
        _execution(wiki, automation_id=producer_id),
        page_id=updated.page.page_id,
        generated="# Email Updates\n\nNew briefing.\n",
        expected_version=updated.resource.version_id,
        expected_head=wiki.repository.head,
    )
    assert not unchanged.is_error
    assert unchanged.data["changed"] is False
    assert wiki.repository.head == commit.commit_id


@pytest.mark.asyncio
async def test_generated_publisher_rejects_nonproducer_and_stale_pages(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    topic = wiki.read_page("interaction-lab")

    rejected = await publish_wiki_generated_tool.execute(
        _execution(wiki),
        page_id=topic.page.page_id,
        generated="Wrong owner.",
        expected_version=topic.resource.version_id,
        expected_head=wiki.repository.head,
    )
    assert rejected.is_error
    assert rejected.outcome.error.code == "not_producer_page"
    assert rejected.outcome.error.recovery_action is not None

    feed = wiki.create_page(page_id="feed", path="automations/current.md", title="Current")
    stale = await publish_wiki_generated_tool.execute(
        _execution(wiki, automation_id="feed-worker"),
        page_id=feed.page.page_id,
        generated="Current.",
        expected_version=feed.resource.version_id,
        expected_head="0" * 64,
    )
    assert stale.is_error
    assert stale.outcome.error.code == "revision_conflict"


@pytest.mark.asyncio
async def test_generated_publisher_requires_the_registered_automation_owner(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    feed = wiki.create_page(
        page_id="feed",
        path="automations/current.md",
        title="Current",
        metadata={"producer_automation_id": "owner"},
    )

    no_automation = await publish_wiki_generated_tool.execute(
        _execution(wiki),
        page_id=feed.page.page_id,
        generated="Current.",
        expected_version=feed.resource.version_id,
        expected_head=wiki.repository.head,
    )
    assert no_automation.is_error
    assert no_automation.outcome.error.code == "automation_required"
    assert no_automation.outcome.error.recovery_action is not None

    wrong_owner = await publish_wiki_generated_tool.execute(
        _execution(wiki, automation_id="intruder"),
        page_id=feed.page.page_id,
        generated="Current.",
        expected_version=feed.resource.version_id,
        expected_head=wiki.repository.head,
    )
    assert wrong_owner.is_error
    assert wrong_owner.outcome.error.code == "producer_mismatch"
    assert wrong_owner.outcome.error.recovery_action is not None


@pytest.mark.asyncio
async def test_generated_publisher_exposes_an_exact_approval_preview(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)

    approval = await publish_wiki_generated_tool.approval_info(
        _execution(wiki, automation_id="owner"),
        page_id="feed",
        generated="New generated content.",
        expected_version="a" * 64,
        expected_head="b" * 64,
    )

    assert approval is not None
    assert approval.description == "Update generated wiki content for feed"
    assert approval.preview == "New generated content."
    assert approval.diff is not None
    assert "a" * 64 in approval.diff
    assert "b" * 64 in approval.diff


@pytest.mark.asyncio
async def test_wiki_tools_require_the_wiki_capability(tmp_path: Path) -> None:
    del tmp_path
    execution = _execution(None)
    result = await read_wiki_page_tool.execute(execution, page_id="missing")
    listing = await list_wiki_pages_tool.execute(execution)

    assert result.is_error
    assert listing.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "not_configured"


@pytest.mark.asyncio
async def test_move_wiki_page_is_atomic_scoped_and_area_safe(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    record = wiki.read_page("interaction-lab")
    session = SessionService(_AreaStore())
    execution = _execution(wiki, session=session)

    moved = await move_wiki_page_tool.execute(
        execution,
        page_id="interaction-lab",
        new_path="topics/renamed-location.md",
        expected_version=record.resource.version_id,
        expected_head=wiki.repository.head,
    )
    assert not moved.is_error
    assert moved.data["changed"] is True
    assert wiki.read_page("interaction-lab").resource.path == "topics/renamed-location.md"
    assert wiki.read_page("interaction-lab").page.title == "Interaction Lab"
    assert b"[[Lab]]" in wiki.read_page("peer").content
    assert all(page.page.lifecycle != "redirect" for page in wiki.list_pages(include_redirects=True))

    replayed = await move_wiki_page_tool.execute(
        execution,
        page_id="interaction-lab",
        new_path="topics/renamed-location.md",
        expected_version=record.resource.version_id,
        expected_head=wiki.repository.head,
    )
    assert replayed.data["changed"] is False

    automation = _execution(wiki, automation_id="daily", session=session)
    denied = await move_wiki_page_tool.execute(
        automation,
        page_id="interaction-lab",
        new_path="automations/daily.md",
        expected_version=wiki.read_page("interaction-lab").resource.version_id,
        expected_head=wiki.repository.head,
    )
    assert denied.outcome is not None and denied.outcome.error is not None
    assert denied.outcome.error.code == "automation_path_denied"

    automation_page = wiki.create_page(
        path="automations/daily.md",
        title="Daily output",
        page_id="daily-output",
        expected_head=wiki.repository.head,
    )
    destination_denied = await move_wiki_page_tool.execute(
        automation,
        page_id="daily-output",
        new_path="topics/daily.md",
        expected_version=automation_page.resource.version_id,
        expected_head=wiki.repository.head,
    )
    assert destination_denied.outcome is not None and destination_denied.outcome.error is not None
    assert destination_denied.outcome.error.code == "automation_path_denied"

    stale = await move_wiki_page_tool.execute(
        execution,
        page_id="interaction-lab",
        new_path="topics/stale.md",
        expected_version=record.resource.version_id,
        expected_head=record.resource.version_id,
    )
    assert stale.outcome is not None and stale.outcome.error is not None
    assert stale.outcome.error.code == "revision_conflict"

    bound = _execution(wiki, session=SessionService(_AreaStore({"name": "Research"})))
    blocked = await move_wiki_page_tool.execute(
        bound,
        page_id="interaction-lab",
        new_path="topics/blocked.md",
        expected_version=wiki.read_page("interaction-lab").resource.version_id,
        expected_head=wiki.repository.head,
    )
    assert blocked.outcome is not None and blocked.outcome.error is not None
    assert blocked.outcome.error.code == "area_page_bound"


def test_wiki_read_boundary_has_its_own_core_integration() -> None:
    assert WIKI in CORE_INTEGRATIONS
    assert set(WIKI.tools) == {
        "list_wiki_pages",
        "read_wiki_page",
        "wiki_links",
        "create_wiki_page",
        "edit_wiki_page",
        "archive_wiki_page",
        "move_wiki_page",
        "publish_wiki_generated",
    }
    assert {tool.policy.action.value for tool in WIKI.tools.values()} == {"read", "write"}
    assert {WIKI.tools[name].policy.permissions for name in ("list_wiki_pages", "read_wiki_page", "wiki_links")} == {
        frozenset({"wiki"})
    }
    assert {
        WIKI.tools[name].policy.permissions
        for name in (
            "create_wiki_page",
            "edit_wiki_page",
            "archive_wiki_page",
            "publish_wiki_generated",
        )
    } == {frozenset({"wiki", WIKI_POST_COMMIT_SERVICE})}
    assert WIKI.tools["move_wiki_page"].policy.permissions == frozenset({"wiki", WIKI_POST_COMMIT_SERVICE, "session"})
    assert create_wiki_page_tool.policy.idempotent is True
    assert edit_wiki_page_tool.policy.idempotent is True
    assert archive_wiki_page_tool.policy.idempotent is True
    assert move_wiki_page_tool.policy.idempotent is True
    assert publish_wiki_generated_tool.policy.idempotent is False
