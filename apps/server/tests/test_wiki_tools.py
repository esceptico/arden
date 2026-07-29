import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

import arden.tools.wiki as wiki_tools
from arden.context.models import SessionState
from arden.integrations.core import CORE_INTEGRATIONS, WIKI
from arden.revisions import ManagedFileRepository
from arden.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry
from arden.tools.wiki import (
    ReadWikiPageInput,
    list_wiki_pages_tool,
    publish_wiki_generated_tool,
    read_wiki_page_tool,
    wiki_links_tool,
)
from arden.wiki.models import GeneratedPageTarget
from arden.wiki.pages import extract_generated_region
from arden.wiki.service import WikiAmbiguityError, WikiService


def _execution(wiki: WikiService | None, *, automation_id: str | None = None) -> ToolExecution:
    services = {} if wiki is None else {"wiki": wiki}
    context = ToolContext(
        session_state=SessionState(session_id="wiki-tools", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1", automation_id=automation_id),
        io=IOBridge(),
        services=services,
        background_tasks=BackgroundTaskRegistry(session_id="wiki-tools"),
    )
    return ToolExecution(tool_id="wiki-1", tool_name="read_wiki_page", ctx=context)


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
    }

    topics = await list_wiki_pages_tool.execute(run, directory="topics")
    assert [(entry["kind"], entry["path"], entry.get("title")) for entry in topics.data["entries"]] == [
        ("directory", "topics/labs/", None),
        ("page", "topics/interaction-lab.md", "Interaction Lab"),
        ("page", "topics/peer.md", "Peer"),
    ]
    assert "README.md" not in topics.content and "retired.md" not in topics.content

    capped = await list_wiki_pages_tool.execute(run, directory="topics", limit=1)
    assert capped.data["has_more"] is True
    assert len(capped.data["entries"]) == 1

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
    assert result.data["total"] == 2
    assert result.data["has_more"] is True


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
async def test_registered_feed_producer_updates_only_its_generated_region(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    producer_id = "email-feed-worker"
    wiki.publish_generated(
        (
            GeneratedPageTarget(
                page_id="email-feed",
                path="feeds/email-updates.md",
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

    feed = wiki.create_page(page_id="feed", path="feeds/current.md", title="Current")
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
        path="feeds/current.md",
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

    wrong_owner = await publish_wiki_generated_tool.execute(
        _execution(wiki, automation_id="intruder"),
        page_id=feed.page.page_id,
        generated="Current.",
        expected_version=feed.resource.version_id,
        expected_head=wiki.repository.head,
    )
    assert wrong_owner.is_error
    assert wrong_owner.outcome.error.code == "producer_mismatch"


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


def test_wiki_read_boundary_has_its_own_core_integration() -> None:
    assert WIKI in CORE_INTEGRATIONS
    assert set(WIKI.tools) == {"list_wiki_pages", "read_wiki_page", "wiki_links", "publish_wiki_generated"}
    assert {tool.policy.action.value for tool in WIKI.tools.values()} == {"read", "write"}
    assert {tool.policy.permissions for tool in WIKI.tools.values()} == {frozenset({"wiki"})}
    assert publish_wiki_generated_tool.policy.idempotent is False
