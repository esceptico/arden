import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

import arden.tools.wiki as wiki_tools
from arden.constants import DAILY_NOTES_AUTOMATION_ID
from arden.context.models import SessionState
from arden.integrations.core import CORE_INTEGRATIONS, WIKI
from arden.revisions import ManagedFileRepository
from arden.services.session import SessionService
from arden.tools.core.context import (
    BackgroundTaskRegistry,
    IOBridge,
    ResourceObservation,
    RunContext,
    ToolContext,
    ToolExecution,
)
from arden.tools.core.registry import ToolRegistry
from arden.tools.wiki import (
    WikiArchivePageInput,
    WikiCreatePageInput,
    WikiEditPageInput,
    WikiLinksInput,
    WikiMovePageInput,
    WikiPatchPageInput,
    WikiPublishGeneratedInput,
    WikiReadPageInput,
    wiki_archive_page_tool,
    wiki_create_page_tool,
    wiki_edit_page_tool,
    wiki_links_tool,
    wiki_list_changes_tool,
    wiki_list_pages_tool,
    wiki_move_page_tool,
    wiki_patch_page_tool,
    wiki_publish_generated_tool,
    wiki_read_page_tool,
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
    resource_observations: dict[str, ResourceObservation] | None = None,
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
        run=RunContext(
            run_id="run-1",
            automation_id=automation_id,
            _resource_observations=resource_observations if resource_observations is not None else {},
        ),
        io=IOBridge(),
        services=services,
        background_tasks=BackgroundTaskRegistry(session_id="wiki-tools"),
    )
    return ToolExecution(tool_id="wiki-1", tool_name="wiki_read_page", ctx=context)


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
        body=b"# Interaction Lab\n\n[[topics/peer]]\n[[Missing]]\n",
        page_id="interaction-lab",
        expected_head=None,
        actor="test",
        origin="test",
        reason="seed",
    )
    wiki.create_page(
        path="topics/peer.md",
        title="Peer",
        body=b"[[topics/interaction-lab]]\n",
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

    root = await wiki_list_pages_tool.execute(run)
    assert root.data == {
        "directory": "",
        "offset": 0,
        "entries": [
            {"kind": "directory", "path": "topics/"},
            {"kind": "page", "path": "about.md", "title": "About", "lifecycle": "active"},
        ],
        "total": 2,
        "has_more": False,
        "next_offset": None,
    }

    topics = await wiki_list_pages_tool.execute(run, directory="topics")
    assert [(entry["kind"], entry["path"], entry.get("title")) for entry in topics.data["entries"]] == [
        ("directory", "topics/labs/", None),
        ("page", "topics/README.md", "Topics README"),
        ("page", "topics/interaction-lab.md", "Interaction Lab"),
        ("page", "topics/peer.md", "Peer"),
    ]
    assert "topics/README.md" in topics.content and "retired.md" not in topics.content

    capped = await wiki_list_pages_tool.execute(run, directory="topics", limit=1)
    assert capped.data["has_more"] is True
    assert len(capped.data["entries"]) == 1
    assert capped.data["next_offset"] == 1
    continued = await wiki_list_pages_tool.execute(
        run,
        directory="topics",
        offset=capped.data["next_offset"],
        limit=1,
    )
    assert continued.data["entries"][0]["path"] == "topics/README.md"
    assert continued.data["next_offset"] == 2

    for directory in (".", "./topics", "topics/.", "../topics", "topics//labs", "/topics", r"topics\labs"):
        invalid = await wiki_list_pages_tool.execute(run, directory=directory)
        assert invalid.is_error
        assert invalid.outcome is not None and invalid.outcome.error is not None
        assert invalid.outcome.error.code == "invalid_directory"

    missing = await wiki_list_pages_tool.execute(run, directory="missing")
    assert missing.is_error
    assert missing.outcome is not None and missing.outcome.error is not None
    assert missing.outcome.error.code == "not_found"


@pytest.mark.asyncio
async def test_list_wiki_changes_reports_paths_and_reasons_without_revision_ids(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    result = await wiki_list_changes_tool.execute(
        _execution(wiki),
        since=datetime.now(UTC) - timedelta(minutes=1),
    )

    paths = {change["path"] for entry in result.data["entries"] for change in entry["changes"]}
    assert {"topics/interaction-lab.md", "topics/peer.md"} <= paths
    assert all(entry["reason"] == "seed" for entry in result.data["entries"])
    assert "commit_id" not in json.dumps(result.serialized_payload())

    empty = await wiki_list_changes_tool.execute(
        _execution(wiki),
        since=datetime.now(UTC) + timedelta(minutes=1),
    )
    assert empty.data["entries"] == []


@pytest.mark.asyncio
async def test_list_wiki_pages_reports_byte_budget_truncation(tmp_path: Path, monkeypatch) -> None:
    wiki = _wiki(tmp_path)
    monkeypatch.setattr(wiki_tools, "_MAX_LIST_DATA_BYTES", 1)

    result = await wiki_list_pages_tool.execute(_execution(wiki), directory="topics")

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
    second = wiki_tools._listing_page_data(
        next(record for record in wiki.snapshot().pages if record.resource.path == "topics/interaction-lab.md")
    )
    monkeypatch.setattr(
        wiki_tools,
        "_MAX_LIST_DATA_BYTES",
        max(
            len(json.dumps(first, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
            len(json.dumps(second, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
        )
        + 1,
    )

    first_page = await wiki_list_pages_tool.execute(_execution(wiki), directory="topics")

    assert [entry["path"] for entry in first_page.data["entries"]] == ["topics/README.md"]
    assert first_page.data["next_offset"] == 1
    second_page = await wiki_list_pages_tool.execute(
        _execution(wiki), directory="topics", offset=first_page.data["next_offset"]
    )
    assert [entry["path"] for entry in second_page.data["entries"]] == ["topics/interaction-lab.md"]
    assert second_page.data["next_offset"] == 2
    third_page = await wiki_list_pages_tool.execute(
        _execution(wiki),
        directory="topics",
        offset=second_page.data["next_offset"],
        limit=1,
    )
    assert [entry["path"] for entry in third_page.data["entries"]] == ["topics/peer.md"]
    assert third_page.data["has_more"] is False


@pytest.mark.asyncio
async def test_read_wiki_page_uses_paths_without_exposing_storage_ids(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    run = _execution(wiki)

    result = await wiki_read_page_tool.execute(run, path="topics/interaction-lab.md", limit=2)

    assert not result.is_error
    assert "[" in result.content and "lines" in result.content
    assert "Wiki page content:" not in result.content
    assert "version" not in result.content
    assert "head" not in result.content
    assert result.data["page"] == {
        "path": "topics/interaction-lab.md",
        "title": "Interaction Lab",
        "aliases": ["Lab"],
        "lifecycle": "active",
    }
    assert result.data["content_truncated"] is False
    assert [(ref.kind, ref.ref) for ref in result.source_refs] == [("wiki_page", "topics/interaction-lab.md")]


@pytest.mark.asyncio
async def test_repeated_unchanged_full_wiki_read_returns_receipt_until_compaction(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    execution = _execution(wiki)

    first = await wiki_read_page_tool.execute(execution, path="topics/interaction-lab.md")
    second = await wiki_read_page_tool.execute(execution, path="topics/interaction-lab.md")

    assert "Interaction" in first.content
    assert second.data["deduplicated"] is True
    assert second.data["content_returned"] is False
    assert "still current and visible" in second.content

    execution.ctx.run.downgrade_resource_observations()
    refreshed = await wiki_read_page_tool.execute(execution, path="topics/interaction-lab.md")

    assert refreshed.data["content_truncated"] is False
    assert "Interaction" in refreshed.content


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

    bounded = await wiki_read_page_tool.execute(run, path="topics/long.md", limit=100)
    assert len(bounded.content) <= 40_000
    assert bounded.data["content_truncated"] is True

    missing = await wiki_read_page_tool.execute(run, path="topics/unknown.md")
    assert missing.is_error
    assert missing.outcome is not None and missing.outcome.error is not None
    assert missing.outcome.error.code == "not_found"

    def ambiguous_snapshot():
        raise WikiAmbiguityError("ambiguous wiki name 'lab': a, b")

    monkeypatch.setattr(wiki, "snapshot", ambiguous_snapshot)
    ambiguous = await wiki_read_page_tool.execute(run, path="topics/interaction-lab.md")
    assert ambiguous.is_error
    assert ambiguous.outcome is not None and ambiguous.outcome.error is not None
    assert ambiguous.outcome.error.code == "ambiguous_ref"
    assert "a, b" not in ambiguous.serialized_payload()

    with pytest.raises(ValidationError, match="Extra inputs"):
        WikiReadPageInput(path="topics/interaction-lab.md", page_id="interaction-lab")


@pytest.mark.asyncio
async def test_wiki_links_returns_resolved_unresolved_and_backlink_context(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    result = await wiki_links_tool.execute(_execution(wiki), path="topics/interaction-lab.md")

    assert not result.is_error
    assert result.data["page"]["path"] == "topics/interaction-lab.md"
    assert result.data["outgoing_total"] == 2
    assert result.data["backlinks_total"] == 1
    assert result.data["outgoing"][0]["target_path"] == "topics/peer.md"
    assert result.data["outgoing"][0]["status"] == "resolved"
    assert result.data["outgoing"][1]["status"] == "unresolved"
    assert result.data["backlinks"] == [
        {
            "source_path": "topics/peer.md",
            "target_path": "topics/interaction-lab.md",
            "status": "resolved",
            "page": "topics/interaction-lab",
            "fragment": None,
            "alias": None,
            "embed": False,
        }
    ]
    assert [(ref.kind, ref.ref) for ref in result.source_refs] == [("wiki_page", "topics/interaction-lab.md")]


@pytest.mark.asyncio
async def test_wiki_links_stays_on_the_selector_snapshot_during_a_concurrent_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wiki = _wiki(tmp_path)
    original = wiki.link_report

    def concurrent_report(page_id: str, *, at: str | None = None):
        wiki.create_page(page_id="later", path="topics/later.md", title="Later")
        return original(page_id, at=at)

    monkeypatch.setattr(wiki, "link_report", concurrent_report)
    result = await wiki_links_tool.execute(_execution(wiki), path="topics/interaction-lab.md")

    assert not result.is_error
    assert result.data["page"]["path"] == "topics/interaction-lab.md"


@pytest.mark.asyncio
async def test_wiki_links_bounds_the_complete_structured_payload(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    body = "\n".join(f"[[Missing {index}|{'x' * 5_000}]]" for index in range(30)).encode()
    wiki.create_page(page_id="large-links", path="topics/large-links.md", title="Large links", body=body)

    result = await wiki_links_tool.execute(_execution(wiki), path="topics/large-links.md", limit=500)

    assert not result.is_error
    assert result.data["links_truncated"] is True
    assert result.data["fields_truncated"] is True
    assert len(json.dumps(result.data, ensure_ascii=False, separators=(",", ":")).encode()) <= 40_000


@pytest.mark.asyncio
async def test_wiki_edits_require_a_full_fresh_read(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    execution = _execution(wiki)

    partial = await wiki_read_page_tool.execute(execution, path="topics/interaction-lab.md", limit=1)
    assert not partial.is_error

    result = await wiki_edit_page_tool.execute(execution, path="topics/interaction-lab.md", body="Replacement.\n")
    assert result.outcome.error.code == "fresh_read_required"


@pytest.mark.asyncio
async def test_listing_or_links_do_not_authorize_a_page_mutation(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    execution = _execution(wiki, session=SessionService(_AreaStore()))
    await wiki_list_pages_tool.execute(execution, directory="topics")
    await wiki_links_tool.execute(execution, path="topics/interaction-lab.md")

    archived = await wiki_archive_page_tool.execute(
        execution,
        path="topics/interaction-lab.md",
        reason="should require the page",
    )
    moved = await wiki_move_page_tool.execute(
        execution,
        path="topics/interaction-lab.md",
        new_path="topics/moved.md",
    )

    assert archived.outcome.error.code == "fresh_read_required"
    assert moved.outcome.error.code == "fresh_read_required"


@pytest.mark.asyncio
async def test_wiki_edit_ignores_unrelated_commits_after_the_read(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    execution = _execution(wiki)
    await wiki_read_page_tool.execute(execution, path="topics/interaction-lab.md")
    wiki.create_page(path="topics/unrelated.md", title="Unrelated")

    result = await wiki_edit_page_tool.execute(
        execution,
        path="topics/interaction-lab.md",
        body="Updated after an unrelated commit.\n",
    )

    assert not result.is_error
    assert wiki.read_page("interaction-lab").page.body == b"Updated after an unrelated commit.\n"


@pytest.mark.asyncio
async def test_wiki_read_authorizes_edit_in_a_later_session_run(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    observations: dict[str, ResourceObservation] = {}
    read_run = _execution(wiki, resource_observations=observations)
    edit_run = _execution(wiki, resource_observations=observations)

    read = await wiki_read_page_tool.execute(read_run, path="topics/interaction-lab.md")
    edited = await wiki_edit_page_tool.execute(
        edit_run,
        path="topics/interaction-lab.md",
        body="Updated in a later run.\n",
    )

    assert not read.is_error
    assert not edited.is_error
    assert wiki.read_page("interaction-lab").page.body == b"Updated in a later run.\n"


@pytest.mark.asyncio
async def test_wiki_edit_preflight_rejects_before_requesting_approval(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    registry = ToolRegistry()
    registry.register("wiki_edit_page", wiki_edit_page_tool)
    registry.register("wiki_patch_page", wiki_patch_page_tool)

    result = await registry.execute(
        "wiki_edit_page",
        _execution(wiki),
        {"path": "topics/interaction-lab.md", "body": "Replacement.\n"},
    )

    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "fresh_read_required"

    missing_patch = await registry.execute(
        "wiki_patch_page",
        _execution(wiki),
        {"path": "topics/interaction-lab.md", "old_text": "not present", "new_text": "replacement"},
    )
    assert missing_patch.outcome is not None and missing_patch.outcome.error is not None
    assert missing_patch.outcome.error.code == "not_found"


@pytest.mark.asyncio
async def test_wiki_patch_uses_exact_current_text_without_a_full_read(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    execution = _execution(wiki)
    current = wiki.read_page("interaction-lab")
    wiki.update_page(
        "interaction-lab",
        content=current.page.with_body(current.page.body + b"External unrelated line.\n").to_bytes(),
        expected_version=current.resource.version_id,
    )

    result = await wiki_patch_page_tool.execute(
        execution,
        path="topics/interaction-lab.md",
        old_text="# Interaction Lab",
        new_text="# Interaction Laboratory",
    )

    assert not result.is_error
    assert result.data["changed"] is True
    assert wiki.read_page("interaction-lab").page.body == (
        b"# Interaction Laboratory\n\n[[topics/peer]]\n[[Missing]]\nExternal unrelated line.\n"
    )
    observation = execution.ctx.run.resource_observation("wiki:page:interaction-lab")
    assert observation is not None and observation.content_read is False


@pytest.mark.asyncio
async def test_wiki_patch_rejects_missing_and_ambiguous_exact_text(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    execution = _execution(wiki)

    missing = await wiki_patch_page_tool.execute(
        execution,
        path="topics/interaction-lab.md",
        old_text="not present",
        new_text="replacement",
    )
    ambiguous = await wiki_patch_page_tool.execute(
        execution,
        path="topics/interaction-lab.md",
        old_text="[[",
        new_text="{{",
    )

    assert missing.outcome is not None and missing.outcome.error is not None
    assert missing.outcome.error.code == "not_found"
    assert ambiguous.outcome is not None and ambiguous.outcome.error is not None
    assert ambiguous.outcome.error.code == "invalid_ref"


@pytest.mark.asyncio
async def test_wiki_patch_rechecks_version_at_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _wiki(tmp_path)
    original_update = wiki.update_page_with_commit

    def update_after_concurrent_change(page_id: str, **kwargs):
        current = wiki.read_page(page_id)
        concurrent = current.page.with_body(current.page.body.replace(b"[[Missing]]", b"Concurrent change")).to_bytes()
        original_update(page_id, content=concurrent, expected_version=current.resource.version_id)
        return original_update(page_id, **kwargs)

    monkeypatch.setattr(wiki, "update_page_with_commit", update_after_concurrent_change)

    result = await wiki_patch_page_tool.execute(
        _execution(wiki),
        path="topics/interaction-lab.md",
        old_text="# Interaction Lab",
        new_text="# Interaction Laboratory",
    )

    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "revision_conflict"
    assert wiki.read_page("interaction-lab").page.body.startswith(b"# Interaction Lab\n")
    assert b"Concurrent change" in wiki.read_page("interaction-lab").page.body


def test_compaction_downgrades_full_read_observations() -> None:
    run = RunContext(run_id="run")
    run.observe_resource("wiki:page", version="v1", container_version="head-1", content_read=True)

    run.downgrade_resource_observations()

    assert run.resource_observation("wiki:page") == ResourceObservation("v1", "head-1", False)


@pytest.mark.asyncio
async def test_wiki_edit_rejects_a_read_that_will_be_offloaded(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    wiki.create_page(page_id="large-page", path="topics/large.md", title="Large", body=("😀" * 20_000).encode())
    execution = _execution(wiki)

    read = await wiki_read_page_tool.execute(execution, path="topics/large.md")
    result = await wiki_edit_page_tool.execute(execution, path="topics/large.md", body="Replacement.\n")

    assert read.data["content_truncated"] is False
    assert result.outcome.error.code == "fresh_read_required"
    assert wiki.read_page("large-page").page.body == ("😀" * 20_000).encode()


def test_wiki_agent_schemas_never_expose_storage_ids_or_hashes() -> None:
    forbidden = {
        "page_id",
        "expected_head",
        "expected_version",
        "head",
        "version",
        "hash",
        "commit_id",
        "blob_id",
    }
    for input_model in (
        WikiReadPageInput,
        WikiLinksInput,
        WikiCreatePageInput,
        WikiEditPageInput,
        WikiPatchPageInput,
        WikiArchivePageInput,
        WikiMovePageInput,
        WikiPublishGeneratedInput,
    ):
        assert not (set(input_model.model_fields) & forbidden)


@pytest.mark.asyncio
async def test_wiki_results_never_expose_internal_page_or_revision_ids(tmp_path: Path) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "wiki"))
    internal_id = "internal-" + "a" * 64
    record = wiki.create_page(
        page_id=internal_id,
        path="topics/public.md",
        title="Public",
        body=b"Visible body.\n",
    )
    observed_head = wiki.repository.head
    execution = _execution(wiki)

    listing = await wiki_list_pages_tool.execute(execution, directory="topics")
    read = await wiki_read_page_tool.execute(execution, path="topics/public.md")
    links = await wiki_links_tool.execute(execution, path="topics/public.md")
    edited = await wiki_edit_page_tool.execute(execution, path="topics/public.md", body="Updated.\n")

    internal_values = {
        internal_id,
        record.resource.version_id,
        observed_head,
        wiki.read_page(internal_id).resource.version_id,
        wiki.repository.head,
    }
    for result in (listing, read, links, edited):
        payload = result.serialized_payload()
        assert all(value is None or value not in payload for value in internal_values)


@pytest.mark.asyncio
async def test_create_wiki_page_creates_directory_readmes_with_the_first_child(tmp_path: Path) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "wiki"))
    execution = _execution(wiki)
    child = {
        "path": "automations/coast/2026-07-30.md",
        "title": "Coast digest 2026-07-30",
        "aliases": [],
        "body": "Digest.\n",
    }

    created = await wiki_create_page_tool.execute(execution, **child)
    replayed = await wiki_create_page_tool.execute(execution, **child)

    assert not created.is_error
    assert created.data["changed"] is True
    assert "automations/README.md, automations/coast/README.md" in created.content
    assert "replace any bootstrap text" in created.content
    assert replayed.data["changed"] is False
    assert "bootstrap" not in replayed.content
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
    assert coast_readme.page.title == "Coast README"
    assert b"## Purpose" in coast_readme.content
    assert b"before the creating task finishes" in coast_readme.content

    child_record = next(
        record for record in wiki.snapshot().pages if record.resource.path == "automations/coast/2026-07-30.md"
    )
    wiki.archive_page(
        child_record.page.page_id,
        expected_version=child_record.resource.version_id,
        base_head=wiki.repository.head,
    )
    wiki.archive_page(
        coast_readme.page.page_id,
        expected_version=coast_readme.resource.version_id,
        base_head=wiki.repository.head,
    )
    restored = await wiki_create_page_tool.execute(
        _execution(wiki),
        path="automations/coast/2026-07-31.md",
        title="Coast digest 2026-07-31",
        body="Digest.\n",
    )
    assert not restored.is_error
    assert "restored directory contracts: automations/coast/README.md" in restored.content
    assert b"before the creating task finishes" in wiki.read_page(coast_readme.page.page_id).content


@pytest.mark.asyncio
async def test_move_wiki_page_reports_a_new_destination_contract(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    execution = _execution(wiki, session=SessionService(_AreaStore()))
    await wiki_read_page_tool.execute(execution, path="topics/interaction-lab.md")

    moved = await wiki_move_page_tool.execute(
        execution,
        path="topics/interaction-lab.md",
        new_path="archives/research/interaction-lab.md",
    )

    assert not moved.is_error
    assert "archives/README.md, archives/research/README.md" in moved.content
    assert "replace any bootstrap text" in moved.content
    readmes = {
        record.resource.path: record.page.title
        for record in wiki.snapshot().pages
        if record.resource.path.endswith("/README.md")
    }
    assert readmes["archives/README.md"] == "Archives README"
    assert readmes["archives/research/README.md"] == "Research README"


@pytest.mark.asyncio
async def test_agent_wiki_writes_are_cas_safe_and_semantically_idempotent(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    execution = _execution(wiki)
    create_args = {
        "path": "daily/2026-07-29.md",
        "title": "2026-07-29",
        "aliases": [],
        "body": "# 2026-07-29\n\nInitial.\n",
    }

    created = await wiki_create_page_tool.execute(execution, **create_args)
    assert not created.is_error
    assert created.data["changed"] is True
    assert created.data["page"]["path"] == "daily/2026-07-29.md"

    replayed = await wiki_create_page_tool.execute(execution, **create_args)
    assert not replayed.is_error
    assert replayed.data["changed"] is False

    conflict = await wiki_create_page_tool.execute(execution, **{**create_args, "body": "Different.\n"})
    assert conflict.is_error
    assert conflict.outcome.error.code == "name_conflict"

    shared_title = await wiki_create_page_tool.execute(
        execution,
        **{**create_args, "path": "daily/2026-07-29-copy.md"},
    )
    assert not shared_title.is_error
    assert shared_title.data["changed"] is True

    missing = await wiki_edit_page_tool.execute(
        execution,
        path="daily/missing.md",
        body="No.",
    )
    assert missing.is_error
    assert missing.outcome.error.code == "not_found"
    assert missing.outcome.error.recovery_action is not None

    edit_args = {
        "path": "daily/2026-07-29.md",
        "body": "# 2026-07-29\n\nUpdated.\n",
    }
    edit_execution = _execution(wiki)
    unread = await wiki_edit_page_tool.execute(edit_execution, **edit_args)
    assert unread.outcome.error.code == "fresh_read_required"
    await wiki_read_page_tool.execute(edit_execution, path="daily/2026-07-29.md")
    edited = await wiki_edit_page_tool.execute(edit_execution, **edit_args)
    assert not edited.is_error
    assert edited.data["changed"] is True
    current = next(record for record in wiki.snapshot().pages if record.resource.path == "daily/2026-07-29.md")
    assert current.page.body == edit_args["body"].encode()

    replayed_edit = await wiki_edit_page_tool.execute(edit_execution, **edit_args)
    assert not replayed_edit.is_error
    assert replayed_edit.data["changed"] is False

    current = next(record for record in wiki.snapshot().pages if record.resource.path == "daily/2026-07-29.md")
    wiki.update_page(
        current.page.page_id,
        content=current.content + b"External change.\n",
        expected_version=current.resource.version_id,
        expected_head=wiki.repository.head,
    )
    stale_edit = await wiki_edit_page_tool.execute(edit_execution, **{**edit_args, "body": "Stale overwrite.\n"})
    assert stale_edit.is_error
    assert stale_edit.outcome.error.code == "revision_conflict"

    await wiki_read_page_tool.execute(edit_execution, path="daily/2026-07-29.md")
    wiki.create_page(path="topics/unrelated-before-archive.md", title="Unrelated before archive")
    archive_args = {
        "path": "daily/2026-07-29.md",
        "reason": "retention window elapsed",
    }
    archived = await wiki_archive_page_tool.execute(edit_execution, **archive_args)
    assert not archived.is_error
    assert archived.data["changed"] is True
    assert archived.data["page"]["lifecycle"] == "archived"

    replayed_archive = await wiki_archive_page_tool.execute(edit_execution, **archive_args)
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

    created = await wiki_create_page_tool.execute(
        _execution(wiki, post_commit=project_wiki),
        path="automations/result.md",
        title="Automation result",
        body="Initial.\n",
    )
    assert not created.is_error
    assert created.data["changed"] is True

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
    producer_execution = _execution(wiki, automation_id="producer", post_commit=project_wiki)
    await wiki_read_page_tool.execute(producer_execution, path="automations/generated.md")
    wiki.create_page(path="topics/unrelated-before-publish.md", title="Unrelated before publish")
    published = await wiki_publish_generated_tool.execute(
        producer_execution,
        path="automations/generated.md",
        generated="New.",
    )
    assert not published.is_error
    assert published.data["changed"] is True


@pytest.mark.asyncio
async def test_scheduled_automation_writes_stay_in_automation_workspace(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    automation = _execution(wiki, automation_id="daily-digest")
    original_head = wiki.repository.head

    denied = await wiki_create_page_tool.execute(
        automation,
        path="daily/outside.md",
        title="Outside",
        body="No.",
    )
    assert denied.is_error
    assert denied.outcome.error.code == "automation_path_denied"
    assert wiki.repository.head == original_head

    for path in ("automations-old/outside.md", "automations/../topics/outside.md", r"automations\outside.md"):
        invalid = await wiki_create_page_tool.execute(
            automation,
            path=path,
            title=f"Invalid {path}",
            body="No.",
        )
        assert invalid.is_error
        assert wiki.repository.head == original_head

    created = await wiki_create_page_tool.execute(
        automation,
        path="automations/daily-digest.md",
        title="Daily digest",
        body="First.\n",
    )
    assert not created.is_error
    denied_edit = await wiki_edit_page_tool.execute(
        automation,
        path="topics/interaction-lab.md",
        body="No.",
    )
    assert denied_edit.is_error
    assert denied_edit.outcome.error.code == "automation_path_denied"

    await wiki_read_page_tool.execute(automation, path="automations/daily-digest.md")
    edited = await wiki_edit_page_tool.execute(
        automation,
        path="automations/daily-digest.md",
        body="Second.\n",
    )
    assert not edited.is_error

    archived = await wiki_archive_page_tool.execute(
        automation,
        path="automations/daily-digest.md",
        reason="replace rolling result",
    )
    assert not archived.is_error


@pytest.mark.asyncio
async def test_daily_notes_automation_writes_only_dated_daily_pages(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    automation = _execution(wiki, automation_id=DAILY_NOTES_AUTOMATION_ID)

    created = await wiki_create_page_tool.execute(
        automation,
        path="daily/2026-07-31.md",
        title="2026-07-31",
        body="A dated note.\n",
    )
    assert not created.is_error

    await wiki_read_page_tool.execute(automation, path="daily/2026-07-31.md")
    edited = await wiki_edit_page_tool.execute(
        automation,
        path="daily/2026-07-31.md",
        body="An updated dated note.\n",
    )
    assert not edited.is_error

    for path in (
        "daily/README.md",
        "daily/today.md",
        "daily/2026-99-99.md",
        "daily/2026-7-1.md",
        "active-work.md",
        "automations/daily-notes.md",
    ):
        denied = await wiki_create_page_tool.execute(
            automation,
            path=path,
            title="Denied",
            body="No.\n",
        )
        assert denied.is_error
        assert denied.outcome.error.code == "automation_path_denied"


@pytest.mark.asyncio
async def test_agent_wiki_writes_preserve_backend_and_producer_boundaries(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    interactive = _execution(wiki)
    automation = _execution(wiki, automation_id="owner")

    health = await wiki_create_page_tool.execute(
        interactive,
        path="health.md",
        title="Health",
        body="No.",
    )
    assert health.is_error
    assert health.outcome.error.code == "invalid_page"
    assert health.outcome.error.recovery_action is not None

    wiki.create_page(
        page_id="producer",
        path="automations/producer.md",
        title="Producer",
        body=b"<!-- generated -->\nCurrent.\n<!-- /generated -->\n",
        metadata={"producer_automation_id": "owner"},
        expected_head=wiki.repository.head,
    )
    edit = await wiki_edit_page_tool.execute(
        automation,
        path="automations/producer.md",
        body="Overwrite.",
    )
    assert edit.is_error
    assert edit.outcome.error.code == "producer_page_requires_generated_publish"

    archive = await wiki_archive_page_tool.execute(
        automation,
        path="automations/producer.md",
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

    producer_execution = _execution(wiki, automation_id=producer_id)
    await wiki_read_page_tool.execute(producer_execution, path="automations/email-updates.md")
    result = await wiki_publish_generated_tool.execute(
        producer_execution,
        path="automations/email-updates.md",
        generated="# Email Updates\n\nNew briefing.",
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

    unchanged_execution = _execution(wiki, automation_id=producer_id)
    await wiki_read_page_tool.execute(unchanged_execution, path="automations/email-updates.md")
    unchanged = await wiki_publish_generated_tool.execute(
        unchanged_execution,
        path="automations/email-updates.md",
        generated="# Email Updates\n\nNew briefing.\n",
    )
    assert not unchanged.is_error
    assert unchanged.data["changed"] is False
    assert wiki.repository.head == commit.commit_id


@pytest.mark.asyncio
async def test_generated_publisher_rejects_nonproducer_and_stale_pages(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    rejected = await wiki_publish_generated_tool.execute(
        _execution(wiki),
        path="topics/interaction-lab.md",
        generated="Wrong owner.",
    )
    assert rejected.is_error
    assert rejected.outcome.error.code == "not_producer_page"
    assert rejected.outcome.error.recovery_action is not None

    feed = wiki.create_page(
        page_id="feed",
        path="automations/current.md",
        title="Current",
        metadata={"producer_automation_id": "feed-worker"},
    )
    stale_execution = _execution(wiki, automation_id="feed-worker")
    await wiki_read_page_tool.execute(stale_execution, path="automations/current.md")
    wiki.update_page(
        feed.page.page_id,
        content=feed.content + b"Changed.\n",
        expected_version=feed.resource.version_id,
        expected_head=wiki.repository.head,
    )
    stale = await wiki_publish_generated_tool.execute(
        stale_execution,
        path="automations/current.md",
        generated="Current.",
    )
    assert stale.is_error
    assert stale.outcome.error.code == "revision_conflict"


@pytest.mark.asyncio
async def test_generated_publisher_requires_the_registered_automation_owner(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    wiki.create_page(
        page_id="feed",
        path="automations/current.md",
        title="Current",
        metadata={"producer_automation_id": "owner"},
    )

    no_automation = await wiki_publish_generated_tool.execute(
        _execution(wiki),
        path="automations/current.md",
        generated="Current.",
    )
    assert no_automation.is_error
    assert no_automation.outcome.error.code == "automation_required"
    assert no_automation.outcome.error.recovery_action is not None

    wrong_owner = await wiki_publish_generated_tool.execute(
        _execution(wiki, automation_id="intruder"),
        path="automations/current.md",
        generated="Current.",
    )
    assert wrong_owner.is_error
    assert wrong_owner.outcome.error.code == "producer_mismatch"
    assert wrong_owner.outcome.error.recovery_action is not None


@pytest.mark.asyncio
async def test_generated_publisher_exposes_an_exact_approval_preview(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)

    approval = await wiki_publish_generated_tool.approval_info(
        _execution(wiki, automation_id="owner"),
        path="automations/current.md",
        generated="New generated content.",
    )

    assert approval is not None
    assert approval.description == "Update generated wiki content for automations/current.md"
    assert approval.preview == "New generated content."
    assert approval.diff == "Update the current generated content."


@pytest.mark.asyncio
async def test_wiki_tools_require_the_wiki_capability(tmp_path: Path) -> None:
    del tmp_path
    execution = _execution(None)
    result = await wiki_read_page_tool.execute(execution, path="missing.md")
    listing = await wiki_list_pages_tool.execute(execution)

    assert result.is_error
    assert listing.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "not_configured"


@pytest.mark.asyncio
async def test_move_wiki_page_is_atomic_scoped_and_area_safe(tmp_path: Path) -> None:
    wiki = _wiki(tmp_path)
    session = SessionService(_AreaStore())
    execution = _execution(wiki, session=session)
    await wiki_read_page_tool.execute(execution, path="topics/interaction-lab.md")
    wiki.create_page(path="topics/unrelated-before-move.md", title="Unrelated before move")

    moved = await wiki_move_page_tool.execute(
        execution,
        path="topics/interaction-lab.md",
        new_path="topics/renamed-location.md",
    )
    assert not moved.is_error
    assert moved.data["changed"] is True
    assert wiki.read_page("interaction-lab").resource.path == "topics/renamed-location.md"
    assert wiki.read_page("interaction-lab").page.title == "Interaction Lab"
    assert b"[[topics/renamed-location]]" in wiki.read_page("peer").content
    assert all(page.page.lifecycle != "redirect" for page in wiki.list_pages(include_redirects=True))

    exact_replay = await wiki_move_page_tool.execute(
        execution,
        path="topics/interaction-lab.md",
        new_path="topics/renamed-location.md",
    )
    assert not exact_replay.is_error
    assert exact_replay.data["changed"] is False
    assert exact_replay.content == "topics/interaction-lab.md was already moved to topics/renamed-location.md."

    replayed = await wiki_move_page_tool.execute(
        execution,
        path="topics/renamed-location.md",
        new_path="topics/renamed-location.md",
    )
    assert replayed.data["changed"] is False

    automation = _execution(wiki, automation_id="daily", session=session)
    denied = await wiki_move_page_tool.execute(
        automation,
        path="topics/renamed-location.md",
        new_path="automations/daily.md",
    )
    assert denied.outcome is not None and denied.outcome.error is not None
    assert denied.outcome.error.code == "automation_path_denied"

    wiki.create_page(
        path="automations/daily.md",
        title="Daily output",
        page_id="daily-output",
        expected_head=wiki.repository.head,
    )
    destination_denied = await wiki_move_page_tool.execute(
        automation,
        path="automations/daily.md",
        new_path="topics/daily.md",
    )
    assert destination_denied.outcome is not None and destination_denied.outcome.error is not None
    assert destination_denied.outcome.error.code == "automation_path_denied"

    await wiki_read_page_tool.execute(execution, path="topics/renamed-location.md")
    current = wiki.read_page("interaction-lab")
    wiki.update_page(
        current.page.page_id,
        content=current.content + b"Changed.\n",
        expected_version=current.resource.version_id,
        expected_head=wiki.repository.head,
    )
    stale = await wiki_move_page_tool.execute(
        execution,
        path="topics/renamed-location.md",
        new_path="topics/stale.md",
    )
    assert stale.outcome is not None and stale.outcome.error is not None
    assert stale.outcome.error.code == "revision_conflict"

    bound = _execution(wiki, session=SessionService(_AreaStore({"name": "Research"})))
    blocked = await wiki_move_page_tool.execute(
        bound,
        path="topics/renamed-location.md",
        new_path="topics/blocked.md",
    )
    assert blocked.outcome is not None and blocked.outcome.error is not None
    assert blocked.outcome.error.code == "area_page_bound"


def test_wiki_read_boundary_has_its_own_core_integration() -> None:
    assert WIKI in CORE_INTEGRATIONS
    assert set(WIKI.tools) == {
        "wiki_list_pages",
        "wiki_list_changes",
        "wiki_read_page",
        "wiki_links",
        "wiki_create_page",
        "wiki_edit_page",
        "wiki_patch_page",
        "wiki_archive_page",
        "wiki_move_page",
        "wiki_publish_generated",
    }
    assert {tool.policy.action.value for tool in WIKI.tools.values()} == {"read", "write"}
    assert {
        WIKI.tools[name].policy.permissions
        for name in ("wiki_list_pages", "wiki_list_changes", "wiki_read_page", "wiki_links")
    } == {frozenset({"wiki"})}
    assert {
        WIKI.tools[name].policy.permissions
        for name in (
            "wiki_create_page",
            "wiki_edit_page",
            "wiki_patch_page",
            "wiki_archive_page",
            "wiki_publish_generated",
        )
    } == {frozenset({"wiki", WIKI_POST_COMMIT_SERVICE})}
    assert WIKI.tools["wiki_move_page"].policy.permissions == frozenset({"wiki", WIKI_POST_COMMIT_SERVICE, "session"})
    assert wiki_create_page_tool.policy.idempotent is True
    assert wiki_edit_page_tool.policy.idempotent is True
    assert wiki_patch_page_tool.policy.idempotent is True
    assert wiki_archive_page_tool.policy.idempotent is True
    assert wiki_move_page_tool.policy.idempotent is True
    assert wiki_publish_generated_tool.policy.idempotent is False
