import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from arden.memory.facts.consumer_store import FactConsumerStore
from arden.memory.facts.ledger import FactLedger
from arden.memory.facts.synthesis import (
    NO_ADDITIONAL_FACTS,
    FactSynthesis,
    FactSynthesisError,
    SynthesisFact,
)
from arden.revisions import (
    ChangeSet,
    CorruptRepositoryError,
    Create,
    ManagedFileRepository,
    RevisionConflictError,
    Update,
)
from arden.wiki.pages import create_page, extract_generated_region, update_generated_region, update_page_title
from arden.wiki.service import WikiService

NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)


class _Renderer:
    def __init__(self, *, invalid: str | None = None) -> None:
        self.invalid = invalid
        self.calls: list[tuple[str, tuple[SynthesisFact, ...], str]] = []

    async def render(self, *, title: str, facts: tuple[SynthesisFact, ...], existing_body: str) -> str:
        self.calls.append((title, facts, existing_body))
        if self.invalid is not None:
            return self.invalid
        return "\n".join(f"- {fact.text} (fact:{fact.token})" for fact in facts)


class _FailingRenderer(_Renderer):
    async def render(self, *, title: str, facts: tuple[SynthesisFact, ...], existing_body: str) -> str:
        raise RuntimeError("offline")


def _source(fact_id: str) -> dict[str, object]:
    return {
        "kind": "chat_message",
        "ref": f"session:{fact_id}",
        "occurred_at": "2026-07-28T11:59:00Z",
        "time_precision": "second",
    }


def _change(fact_id: str, text: str, **extra: object) -> dict[str, object]:
    return {
        "op": "create",
        "fact_id": fact_id,
        "text": text,
        "kind": "fact",
        "labels": [],
        "subjects": ["Alpha"],
        "scope": {"kind": "area", "key": "general"},
        "sources": [_source(fact_id)],
        "certainty": "confirmed",
        **extra,
    }


async def _service(tmp_path: Path, renderer: _Renderer | None = None):
    ledger = FactLedger(tmp_path / "facts", clock=lambda: NOW)
    consumers = await FactConsumerStore.open(tmp_path / "state.sqlite")
    wiki = WikiService(ManagedFileRepository(tmp_path / "wiki"))
    return (
        ledger,
        consumers,
        wiki,
        FactSynthesis(
            ledger,
            consumers,
            wiki,
            renderer or _Renderer(),
            clock=lambda: NOW,
        ),
    )


@pytest.mark.asyncio
async def test_synthesis_uses_pinned_facts_and_advances_after_atomic_publish(tmp_path: Path) -> None:
    ledger, consumers, wiki, synthesis = await _service(tmp_path)
    try:
        ledger.commit(
            ledger.plan([_change("one", "First"), _change("two", "Second")], actor="test", origin="test", reason="seed")
        )
        pinned = ledger.revision
        assert pinned
        result = await synthesis.run()
        assert result.advanced and result.published_pages == 1
        page = wiki.read_page("topic-" + __import__("hashlib").sha256(b"topic\0alpha").hexdigest()[:12])
        assert b"First (from chat)" in extract_generated_region(page.content, expected_page_id=page.page.page_id)
        assert page.page.metadata["generated_from_revision"] == pinned
        assert await consumers.get("memory.synthesis")

        ledger.commit(
            ledger.plan([_change("later", "Later", subjects=["Later"])], actor="test", origin="test", reason="later")
        )
        assert b"Later" not in page.content
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_synthesis_passes_only_authored_body_to_renderer(tmp_path: Path) -> None:
    renderer = _Renderer()
    ledger, consumers, wiki, synthesis = await _service(tmp_path, renderer)
    try:
        ledger.commit(
            ledger.plan([_change("one", "First"), _change("two", "Second")], actor="test", origin="test", reason="seed")
        )
        await synthesis.run()
        page = next(page for page in wiki.list_pages() if page.page.title == "Alpha")
        current = wiki.repository.get(page.page.page_id)
        wiki.repository.commit(
            ChangeSet(
                (Update(page.page.page_id, current.version_id, page.content + b"\n## Notes\nKeep this.\n"),),
                "user",
                "wiki.user",
                "add authored notes",
                "add-authored-notes",
                wiki.repository.head,
            )
        )
        ledger.commit(ledger.plan([_change("three", "Third")], actor="test", origin="test", reason="new fact"))

        await synthesis.run()

        assert renderer.calls[-1][2] == b"\n## Notes\nKeep this.\n".decode()
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_empty_feed_does_not_write_or_advance(tmp_path: Path) -> None:
    _ledger, consumers, wiki, synthesis = await _service(tmp_path)
    try:
        head = wiki.repository.head
        result = await synthesis.run()
        assert result.empty and not result.advanced
        assert wiki.repository.head == head
        assert await consumers.get("memory.synthesis") is None
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_daily_projection_rolls_over_in_configured_timezone_without_new_fact_events(tmp_path: Path) -> None:
    ledger = FactLedger(tmp_path / "facts", clock=lambda: NOW)
    consumers = await FactConsumerStore.open(tmp_path / "state.sqlite")
    wiki = WikiService(ManagedFileRepository(tmp_path / "wiki"))
    current = [datetime(2026, 7, 28, 19, 30, tzinfo=UTC)]
    renderer = _Renderer()
    synthesis = FactSynthesis(
        ledger,
        consumers,
        wiki,
        renderer,
        timezone_name="Asia/Yerevan",
        clock=lambda: current[0],
    )
    try:
        ledger.commit(
            ledger.plan(
                [_change("temporary", "Still relevant", lifecycle="temporary")],
                actor="test",
                origin="test",
                reason="seed",
            )
        )
        first = await synthesis.run()
        watermark = await consumers.get("memory.synthesis")
        assert first.advanced
        assert wiki.read_page("daily-2026-07-28").resource.path == "daily/2026-07-28.md"

        same_day = await synthesis.run()
        assert same_day.empty and not same_day.advanced
        assert len(renderer.calls) == 2
        assert await consumers.get("memory.synthesis") == watermark

        current[0] = datetime(2026, 7, 28, 20, 30, tzinfo=UTC)
        rollover = await synthesis.run()

        assert not rollover.empty and not rollover.advanced
        next_day = wiki.read_page("daily-2026-07-29")
        assert next_day.resource.path == "daily/2026-07-29.md"
        assert next_day.page.metadata["timezone"] == "Asia/Yerevan"
        assert next_day.page.metadata["fact_citations"][0]["fact_id"] == "temporary"
        assert await consumers.get("memory.synthesis") == watermark
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_routes_temporary_me_project_and_existing_alias(tmp_path: Path) -> None:
    ledger, consumers, wiki, synthesis = await _service(tmp_path)
    try:
        wiki.create_page(path="custom.md", title="Custom", aliases=("Alpha",), page_id="custom")
        ledger.commit(
            ledger.plan(
                [
                    _change("temporary", "Today", lifecycle="temporary"),
                    _change("me", "Personal", subjects=["ME"]),
                    _change(
                        "project",
                        "Build",
                        subjects=["Arden"],
                        scope={"kind": "project", "key": "arden"},
                    ),
                    _change("alias", "Alias fact"),
                ],
                actor="test",
                origin="test",
                reason="seed",
            )
        )
        result = await synthesis.run()
        assert result.advanced
        assert wiki.read_page("active-work").page.title == "Active Work"
        daily = wiki.read_page("daily-2026-07-28")
        assert daily.resource.path == "daily/2026-07-28.md"
        assert daily.page.metadata["date"] == "2026-07-28"
        assert daily.page.metadata["timezone"] == "UTC"
        assert daily.page.metadata["fact_citations"][0]["fact_id"] == "temporary"
        assert wiki.read_page("me").page.title == "Me"
        assert wiki.read_page("custom").page.metadata["fact_citations"][0]["fact_id"] == "alias"
        projects = [
            page for page in wiki.list_pages() if page.page.metadata.get("scope") == {"kind": "project", "key": "arden"}
        ]
        assert len(projects) == 1
        assert projects[0].page.title == "Arden"
        assert projects[0].resource.path.startswith("projects/arden-")
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_reconciles_new_topic_page_with_preexisting_fact_and_preserves_authored_body(tmp_path: Path) -> None:
    renderer = _Renderer()
    ledger, consumers, wiki, synthesis = await _service(tmp_path, renderer)
    try:
        ledger.commit(ledger.plan([_change("one", "First")], actor="test", origin="test", reason="seed"))
        initial = await synthesis.run()
        assert initial.skipped_under_threshold == 1

        wiki.create_page(
            page_id="alpha",
            path="topics/alpha.md",
            title="Alpha",
            body=b"## Notes\nKeep this.\n",
        )
        result = await synthesis.run()

        page = wiki.read_page("alpha")
        assert result.published_pages == 1
        assert renderer.calls[-1][2] == "## Notes\nKeep this.\n"
        assert b"First (from chat)" in extract_generated_region(page.content, expected_page_id="alpha")
        assert page.content.endswith(b"## Notes\nKeep this.\n")
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_reconciliation_skips_topic_page_without_matching_facts(tmp_path: Path) -> None:
    renderer = _Renderer()
    ledger, consumers, wiki, synthesis = await _service(tmp_path, renderer)
    try:
        ledger.commit(ledger.plan([_change("one", "First")], actor="test", origin="test", reason="seed"))
        await synthesis.run()
        wiki.create_page(page_id="beta", path="topics/beta.md", title="Beta")
        head = wiki.repository.head

        result = await synthesis.run()

        assert result.empty
        assert result.published_pages == 0
        assert wiki.repository.head == head
        assert not renderer.calls
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_reconciliation_does_not_rewrite_current_projection(tmp_path: Path) -> None:
    renderer = _Renderer()
    ledger, consumers, wiki, synthesis = await _service(tmp_path, renderer)
    try:
        ledger.commit(ledger.plan([_change("one", "First")], actor="test", origin="test", reason="seed"))
        await synthesis.run()
        wiki.create_page(page_id="alpha", path="topics/alpha.md", title="Alpha")
        await synthesis.run()
        head = wiki.repository.head
        calls = len(renderer.calls)

        result = await synthesis.run()

        assert result.empty
        assert wiki.repository.head == head
        assert len(renderer.calls) == calls
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_reconciliation_records_that_authored_body_already_covers_facts(tmp_path: Path) -> None:
    renderer = _Renderer(invalid=NO_ADDITIONAL_FACTS)
    ledger, consumers, wiki, synthesis = await _service(tmp_path, renderer)
    try:
        ledger.commit(ledger.plan([_change("one", "First")], actor="test", origin="test", reason="seed"))
        await synthesis.run()
        wiki.create_page(
            page_id="alpha",
            path="topics/alpha.md",
            title="Alpha",
            body=b"First is already documented here.\n",
        )

        first = await synthesis.run()
        page = wiki.read_page("alpha")
        first_head = wiki.repository.head
        calls = len(renderer.calls)
        second = await synthesis.run()

        assert first.published_pages == 1
        assert extract_generated_region(page.content, expected_page_id="alpha") == (NO_ADDITIONAL_FACTS + "\n").encode()
        assert page.page.metadata["fact_citations"] == ()
        assert second.empty
        assert wiki.repository.head == first_head
        assert len(renderer.calls) == calls
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_existing_projection_refreshes_when_its_fact_changes(tmp_path: Path) -> None:
    renderer = _Renderer()
    ledger, consumers, wiki, synthesis = await _service(tmp_path, renderer)
    try:
        ledger.commit(
            ledger.plan(
                [_change("one", "First"), _change("two", "Second")],
                actor="test",
                origin="test",
                reason="seed",
            )
        )
        await synthesis.run()
        ledger.commit(ledger.plan([_change("three", "Third")], actor="test", origin="test", reason="refresh"))

        result = await synthesis.run()

        page = wiki.read_page("topic-" + __import__("hashlib").sha256(b"topic\0alpha").hexdigest()[:12])
        assert result.published_pages == 1
        assert b"Third (from chat)" in extract_generated_region(page.content, expected_page_id=page.page.page_id)
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_threshold_archived_and_stale_route_are_safe(tmp_path: Path) -> None:
    ledger, consumers, wiki, synthesis = await _service(tmp_path)
    try:
        ledger.commit(ledger.plan([_change("a", "A"), _change("b", "B")], actor="test", origin="test", reason="alpha"))
        await synthesis.run()
        alpha = next(page for page in wiki.list_pages() if page.page.title == "Alpha")
        ledger.commit(
            ledger.plan(
                [
                    {"op": "amend", "fact_id": "a", "subjects": ["Beta"]},
                    {"op": "amend", "fact_id": "b", "subjects": ["Beta"]},
                ],
                actor="test",
                origin="test",
                reason="move",
            )
        )
        await synthesis.run()
        assert (
            extract_generated_region(wiki.read_page(alpha.page.page_id).content, expected_page_id=alpha.page.page_id)
            == b""
        )

        wiki.archive_page(alpha.page.page_id, base_head=wiki.repository.head)
        ledger.commit(
            ledger.plan(
                [_change("archived", "Old", subjects=["Alpha"])], actor="test", origin="test", reason="archived"
            )
        )
        result = await synthesis.run()
        assert result.skipped_archived >= 1
    finally:
        await consumers.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["claim", "claim (fact:F999)", "claim (fact:F001, wrong)"])
async def test_invalid_renderer_never_publishes_or_advances(tmp_path: Path, bad: str) -> None:
    ledger, consumers, wiki, synthesis = await _service(tmp_path, _Renderer(invalid=bad))
    try:
        head = wiki.repository.head
        ledger.commit(
            ledger.plan([_change("one", "First"), _change("two", "Second")], actor="test", origin="test", reason="seed")
        )
        with pytest.raises(FactSynthesisError):
            await synthesis.run()
        assert wiki.repository.head == head
        assert await consumers.get("memory.synthesis") is None
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_user_generated_edit_blocks_whole_synthesis_and_prior_publish_replays(tmp_path: Path) -> None:
    ledger, consumers, wiki, synthesis = await _service(tmp_path)
    try:
        ledger.commit(
            ledger.plan([_change("one", "First"), _change("two", "Second")], actor="test", origin="test", reason="seed")
        )
        feed = ledger.changes_since(None)
        facts = ledger.facts_at(feed.through_revision)
        routes, _, _, _ = synthesis._targets(feed, facts)
        targets = await synthesis._render_targets(routes, facts, None)
        reason = f"synthesize facts start..{feed.through_revision}"
        wiki.publish_generated(
            targets,
            source_revision=feed.through_revision or "",
            base_head=wiki.repository.head,
            reason=reason,
        )
        page = wiki.read_page(targets[0].page_id)
        current = wiki.repository.get(page.page.page_id)
        changed = update_generated_region(page.content, expected_page_id=page.page.page_id, generated=b"User edit\n")
        wiki.repository.commit(
            ChangeSet(
                (Update(page.page.page_id, current.version_id, changed),),
                "user",
                "wiki.user",
                "edit",
                "user-edit",
                wiki.repository.head,
            )
        )
        result = await synthesis.run()
        assert result.replayed and result.advanced
        assert (
            extract_generated_region(wiki.read_page(page.page.page_id).content, expected_page_id=page.page.page_id)
            == b"User edit\n"
        )
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_live_fact_conflict_never_publishes(tmp_path: Path) -> None:
    class _ChangingRenderer(_Renderer):
        async def render(self, *, title: str, facts: tuple[SynthesisFact, ...], existing_body: str) -> str:
            ledger.commit(
                ledger.plan(
                    [{"op": "amend", "fact_id": "one", "labels": ["changed"]}],
                    actor="test",
                    origin="test",
                    reason="race",
                )
            )
            return await super().render(title=title, facts=facts, existing_body=existing_body)

    ledger, consumers, wiki, _ = await _service(tmp_path)
    synthesis = FactSynthesis(ledger, consumers, wiki, _ChangingRenderer())
    try:
        head = wiki.repository.head
        ledger.commit(
            ledger.plan([_change("one", "First"), _change("two", "Second")], actor="test", origin="test", reason="seed")
        )
        with pytest.raises(FactSynthesisError, match="changed"):
            await synthesis.run()
        assert wiki.repository.head == head
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_casefolded_topics_share_threshold_and_one_page(tmp_path: Path) -> None:
    ledger, consumers, wiki, synthesis = await _service(tmp_path)
    try:
        ledger.commit(
            ledger.plan(
                [_change("upper", "Upper", subjects=["Alpha"]), _change("lower", "Lower", subjects=["alpha"])],
                actor="test",
                origin="test",
                reason="casefold",
            )
        )
        await synthesis.run()
        pages = tuple(page for page in wiki.list_pages() if not page.resource.path.endswith("/README.md"))
        assert len(pages) == 1
        assert pages[0].page.title == "Alpha"
        assert {item["fact_id"] for item in pages[0].page.metadata["fact_citations"]} == {
            "upper",
            "lower",
        }
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_project_scope_keys_remain_exact_case_sensitive_identities(tmp_path: Path) -> None:
    ledger, consumers, wiki, synthesis = await _service(tmp_path)
    try:
        ledger.commit(
            ledger.plan(
                [
                    _change(
                        "upper",
                        "Upper project",
                        subjects=["Upper"],
                        scope={"kind": "project", "key": "Arden"},
                    ),
                    _change(
                        "lower",
                        "Lower project",
                        subjects=["Lower"],
                        scope={"kind": "project", "key": "arden"},
                    ),
                    _change(
                        "spaced",
                        "Spaced project",
                        subjects=["Spaced"],
                        scope={"kind": "project", "key": " Arden "},
                    ),
                ],
                actor="test",
                origin="test",
                reason="exact scopes",
            )
        )
        await synthesis.run()
        projects = {
            page.page.metadata["scope"]["key"]: page for page in wiki.list_pages() if "scope" in page.page.metadata
        }
        assert set(projects) == {"Arden", "arden", " Arden "}
        assert projects["Arden"].page.page_id != projects["arden"].page.page_id
        assert projects["Arden"].page.page_id != projects[" Arden "].page.page_id
        assert projects["Arden"].page.title == "Upper"
        assert projects["arden"].page.title == "Lower"
        assert projects[" Arden "].page.title == "Spaced"
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_no_missing_route_page_is_created_without_assigned_facts(tmp_path: Path) -> None:
    ledger, consumers, wiki, synthesis = await _service(tmp_path)
    try:
        head = wiki.repository.head
        ledger.commit(
            ledger.plan(
                [
                    _change("topic", "Topic", certainty="uncertain"),
                    _change("me", "Me", subjects=["me"], certainty="uncertain"),
                    _change(
                        "project",
                        "Project",
                        scope={"kind": "project", "key": "arden"},
                        certainty="uncertain",
                    ),
                    _change("temporary", "Temporary", lifecycle="temporary", certainty="uncertain"),
                ],
                actor="test",
                origin="test",
                reason="ineligible",
            )
        )
        result = await synthesis.run()
        assert result.advanced and result.published_pages == 0
        assert wiki.repository.head == head
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_archived_fixed_page_identities_are_never_recreated(tmp_path: Path) -> None:
    ledger, consumers, wiki, synthesis = await _service(tmp_path)
    try:
        wiki.create_page(path="me.md", title="Me", page_id="me")
        wiki.create_page(path="active-work.md", title="Active Work", page_id="active-work")
        wiki.archive_page("me", base_head=wiki.repository.head)
        wiki.archive_page("active-work", base_head=wiki.repository.head)
        ledger.commit(
            ledger.plan(
                [
                    _change("personal", "Personal", subjects=["me"]),
                    _change("temporary", "Temporary", lifecycle="temporary"),
                ],
                actor="test",
                origin="test",
                reason="fixed archives",
            )
        )
        result = await synthesis.run()
        assert result.skipped_archived == 2
        assert [page.resource.path for page in wiki.list_pages() if not page.resource.path.endswith("/README.md")] == [
            "daily/2026-07-28.md"
        ]
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_archived_project_scope_blocks_differently_named_recreation(tmp_path: Path) -> None:
    ledger, consumers, wiki, synthesis = await _service(tmp_path)
    try:
        wiki.create_page(
            path="retired/README.md",
            title="Retired",
            body=b"Purpose: retired test pages.\nProducer: tests.\nConsumers: tests.\nRetention: test lifetime.\n",
            page_id="retired-readme",
        )
        wiki.create_page(
            path="retired/custom.md",
            title="Retired Custom Page",
            page_id="retired-custom",
            metadata={"scope": {"kind": "project", "key": "arden"}},
        )
        wiki.archive_page("retired-custom", base_head=wiki.repository.head)
        ledger.commit(
            ledger.plan(
                [
                    _change(
                        "project",
                        "Current project fact",
                        subjects=["Current Project"],
                        scope={"kind": "project", "key": "arden"},
                    )
                ],
                actor="test",
                origin="test",
                reason="archived project scope",
            )
        )

        result = await synthesis.run()

        assert result.skipped_archived == 1
        assert result.published_pages == 0
        assert "retired/README.md" in {record.resource.path for record in wiki.list_pages()}
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_unique_citation_keeps_user_renamed_page_and_one_fact_one_page(tmp_path: Path) -> None:
    ledger, consumers, wiki, synthesis = await _service(tmp_path)
    try:
        ledger.commit(
            ledger.plan(
                [_change("one", "First"), _change("two", "Second")],
                actor="test",
                origin="test",
                reason="seed",
            )
        )
        await synthesis.run()
        page = next(page for page in wiki.list_pages() if page.page.title == "Alpha")
        current = wiki.repository.get(page.page.page_id)
        renamed = update_page_title(
            page.content,
            expected_page_id=page.page.page_id,
            title="User Renamed",
        )
        wiki.repository.commit(
            ChangeSet(
                (Update(page.page.page_id, current.version_id, renamed),),
                "user",
                "wiki.user",
                "rename title",
                "rename-title",
                wiki.repository.head,
            )
        )
        ledger.commit(
            ledger.plan(
                [{"op": "amend", "fact_id": "one", "labels": ["updated"]}],
                actor="test",
                origin="test",
                reason="update",
            )
        )
        await synthesis.run()
        pages = tuple(page for page in wiki.list_pages() if not page.resource.path.endswith("/README.md"))
        assert len(pages) == 1
        assert pages[0].page.page_id == page.page.page_id
        citations = [item["fact_id"] for record in pages for item in record.page.metadata.get("fact_citations", [])]
        assert citations.count("one") == 1
        assert citations.count("two") == 1
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_a_to_b_to_c_feed_clears_every_stale_existing_route(tmp_path: Path) -> None:
    ledger, consumers, wiki, synthesis = await _service(tmp_path)
    try:
        ledger.commit(
            ledger.plan(
                [_change("one", "First"), _change("two", "Second")],
                actor="test",
                origin="test",
                reason="alpha",
            )
        )
        await synthesis.run()
        alpha = next(page for page in wiki.list_pages() if page.page.title == "Alpha")
        wiki.create_page(path="beta.md", title="Beta", page_id="beta")
        ledger.commit(
            ledger.plan(
                [
                    {"op": "amend", "fact_id": "one", "subjects": ["Beta"]},
                    {"op": "amend", "fact_id": "two", "subjects": ["Beta"]},
                ],
                actor="test",
                origin="test",
                reason="beta",
            )
        )
        ledger.commit(
            ledger.plan(
                [
                    {"op": "amend", "fact_id": "one", "subjects": ["Gamma"]},
                    {"op": "amend", "fact_id": "two", "subjects": ["Gamma"]},
                ],
                actor="test",
                origin="test",
                reason="gamma",
            )
        )
        await synthesis.run()
        assert (
            extract_generated_region(
                wiki.read_page(alpha.page.page_id).content,
                expected_page_id=alpha.page.page_id,
            )
            == b""
        )
        assert extract_generated_region(wiki.read_page("beta").content, expected_page_id="beta") == b""
        gamma = next(page for page in wiki.list_pages() if page.page.title == "Gamma")
        assert {item["fact_id"] for item in gamma.page.metadata["fact_citations"]} == {"one", "two"}
    finally:
        await consumers.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("heading", ["# Heading (fact:F001)", "# Heading F001", "# Heading fact:broken"])
async def test_headings_reject_opaque_or_malformed_fact_syntax(tmp_path: Path, heading: str) -> None:
    ledger, consumers, wiki, synthesis = await _service(tmp_path, _Renderer(invalid=heading))
    try:
        head = wiki.repository.head
        ledger.commit(
            ledger.plan(
                [_change("one", "First"), _change("two", "Second")],
                actor="test",
                origin="test",
                reason="seed",
            )
        )
        with pytest.raises(FactSynthesisError, match="headings"):
            await synthesis.run()
        assert wiki.repository.head == head
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_renderer_exception_is_atomic(tmp_path: Path) -> None:
    ledger, consumers, wiki, synthesis = await _service(tmp_path, _FailingRenderer())
    try:
        head = wiki.repository.head
        ledger.commit(
            ledger.plan(
                [_change("one", "First"), _change("two", "Second")],
                actor="test",
                origin="test",
                reason="seed",
            )
        )
        with pytest.raises(FactSynthesisError, match="renderer failed"):
            await synthesis.run()
        assert wiki.repository.head == head
        assert await consumers.get("memory.synthesis") is None
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_stale_clearing_fact_race_is_rejected(tmp_path: Path) -> None:
    ledger, consumers, wiki, synthesis = await _service(tmp_path)
    try:
        ledger.commit(
            ledger.plan(
                [_change("one", "First"), _change("two", "Second")],
                actor="test",
                origin="test",
                reason="seed",
            )
        )
        await synthesis.run()
        ledger.commit(
            ledger.plan(
                [
                    {
                        "op": "review",
                        "fact_id": "one",
                        "reason": "needs confirmation",
                        "review_at": "2026-08-15T00:00:00Z",
                        "review_basis": "inferred",
                        "certainty": "uncertain",
                    },
                    {
                        "op": "review",
                        "fact_id": "two",
                        "reason": "needs confirmation",
                        "review_at": "2026-08-15T00:00:00Z",
                        "review_basis": "inferred",
                        "certainty": "uncertain",
                    },
                ],
                actor="test",
                origin="test",
                reason="clear",
            )
        )

        class _RacingSynthesis(FactSynthesis):
            async def _render_targets(self, routes, facts, base_head):
                rendered = await super()._render_targets(routes, facts, base_head)
                ledger.commit(
                    ledger.plan(
                        [{"op": "amend", "fact_id": "one", "labels": ["raced"]}],
                        actor="test",
                        origin="test",
                        reason="race",
                    )
                )
                return rendered

        racing = _RacingSynthesis(ledger, consumers, wiki, _Renderer())
        before = wiki.repository.head
        with pytest.raises(FactSynthesisError, match="affected fact changed"):
            await racing.run()
        assert wiki.repository.head == before
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_intervening_wiki_edit_rejects_pinned_publication(tmp_path: Path) -> None:
    ledger, consumers, wiki, _synthesis = await _service(tmp_path)

    class _WikiRacingRenderer(_Renderer):
        async def render(self, *, title: str, facts: tuple[SynthesisFact, ...], existing_body: str) -> str:
            if wiki.repository.find_by_path("user.md") is None:
                wiki.repository.commit(
                    ChangeSet(
                        (Create("user", "user.md", create_page(page_id="user", title="User").to_bytes()),),
                        "user",
                        "wiki.user",
                        "intervening edit",
                        "intervening-edit",
                        wiki.repository.head,
                    )
                )
            return await super().render(title=title, facts=facts, existing_body=existing_body)

    synthesis = FactSynthesis(ledger, consumers, wiki, _WikiRacingRenderer())
    try:
        ledger.commit(
            ledger.plan(
                [_change("one", "First"), _change("two", "Second")],
                actor="test",
                origin="test",
                reason="seed",
            )
        )
        with pytest.raises(RevisionConflictError):
            await synthesis.run()
        assert wiki.repository.find_by_path("user.md") is not None
        assert [page.page.page_id for page in wiki.list_pages() if not page.resource.path.endswith("/README.md")] == [
            "user"
        ]
        assert await consumers.get("memory.synthesis") is None
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_trusted_replay_skips_newer_invalid_candidate(tmp_path: Path) -> None:
    ledger, consumers, wiki, synthesis = await _service(tmp_path)
    try:
        ledger.commit(
            ledger.plan(
                [_change("one", "First"), _change("two", "Second")],
                actor="test",
                origin="test",
                reason="seed",
            )
        )
        feed = ledger.changes_since(None)
        facts = ledger.facts_at(feed.through_revision)
        routes, _, _, base_head = synthesis._targets(feed, facts)
        targets = await synthesis._render_targets(routes, facts, base_head)
        reason = f"synthesize facts start..{feed.through_revision}"
        wiki.publish_generated(
            targets,
            source_revision=feed.through_revision or "",
            base_head=base_head,
            reason=reason,
        )
        page = wiki.read_page(targets[0].page_id)
        current = wiki.repository.get(page.page.page_id)
        invalid = page.page.with_metadata({"generated_from_revision": "wrong"}).to_bytes()
        wiki.repository.commit(
            ChangeSet(
                (Update(page.page.page_id, current.version_id, invalid),),
                "Synthesis",
                "memory.synthesis",
                reason,
                "invalid-replay-candidate",
                wiki.repository.head,
            )
        )

        result = await synthesis.run()

        assert result.replayed and result.advanced
        assert wiki.read_page(page.page.page_id).page.metadata["generated_from_revision"] == "wrong"
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_trusted_replay_surfaces_corrupt_published_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger, consumers, wiki, synthesis = await _service(tmp_path)
    try:
        ledger.commit(
            ledger.plan(
                [_change("one", "First"), _change("two", "Second")],
                actor="test",
                origin="test",
                reason="seed",
            )
        )
        feed = ledger.changes_since(None)
        facts = ledger.facts_at(feed.through_revision)
        routes, _, _, base_head = synthesis._targets(feed, facts)
        targets = await synthesis._render_targets(routes, facts, base_head)
        reason = f"synthesize facts start..{feed.through_revision}"
        wiki.publish_generated(
            targets,
            source_revision=feed.through_revision or "",
            base_head=base_head,
            reason=reason,
        )

        def corrupt_read(_resource_id: str, *, at: str | None = None) -> bytes:
            assert at is not None
            raise CorruptRepositoryError("published page blob is corrupt")

        monkeypatch.setattr(wiki.repository, "read", corrupt_read)

        with pytest.raises(CorruptRepositoryError, match="published page blob is corrupt"):
            synthesis._has_trusted_publication(feed, reason)
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_new_unrelated_facts_after_pinned_feed_remain_for_next_run(tmp_path: Path) -> None:
    ledger, consumers, wiki, _synthesis = await _service(tmp_path)

    class _AppendingRenderer(_Renderer):
        appended = False

        async def render(self, *, title: str, facts: tuple[SynthesisFact, ...], existing_body: str) -> str:
            if not self.appended:
                self.appended = True
                ledger.commit(
                    ledger.plan(
                        [
                            _change("later-one", "Later first", subjects=["Later"]),
                            _change("later-two", "Later second", subjects=["Later"]),
                        ],
                        actor="test",
                        origin="test",
                        reason="arrived during synthesis",
                    )
                )
            return await super().render(title=title, facts=facts, existing_body=existing_body)

    synthesis = FactSynthesis(ledger, consumers, wiki, _AppendingRenderer())
    try:
        ledger.commit(
            ledger.plan(
                [_change("one", "First"), _change("two", "Second")],
                actor="test",
                origin="test",
                reason="seed",
            )
        )
        pinned = ledger.revision
        first = await synthesis.run()
        watermark = await consumers.get("memory.synthesis")
        assert first.advanced and watermark is not None
        assert watermark.revision == pinned
        assert all(page.page.title != "Later" for page in wiki.list_pages())

        second = await synthesis.run()

        assert second.advanced
        later = next(page for page in wiki.list_pages() if page.page.title == "Later")
        assert {item["fact_id"] for item in later.page.metadata["fact_citations"]} == {
            "later-one",
            "later-two",
        }
    finally:
        await consumers.close()


@pytest.mark.asyncio
async def test_fact_commit_blocks_between_validation_and_wiki_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, consumers, wiki, _synthesis = await _service(tmp_path)
    ledger.commit(
        ledger.plan(
            [_change("one", "First"), _change("two", "Second")],
            actor="test",
            origin="test",
            reason="seed",
        )
    )
    pinned = ledger.revision
    later_plan = ledger.plan(
        [
            _change("later-one", "Later first", subjects=["Later"]),
            _change("later-two", "Later second", subjects=["Later"]),
        ],
        actor="test",
        origin="test",
        reason="concurrent facts",
    )
    attempted = threading.Event()
    committed = threading.Event()
    failures: list[BaseException] = []

    def commit_later() -> None:
        attempted.set()
        try:
            ledger.commit(later_plan)
        except BaseException as exc:
            failures.append(exc)
        finally:
            committed.set()

    class _LockedRaceSynthesis(FactSynthesis):
        thread: threading.Thread | None = None

        def _validate_live(self, feed, targets, facts, current) -> None:
            if self.thread is None:
                self.thread = threading.Thread(target=commit_later)
                self.thread.start()
                assert attempted.wait(1)
                assert not committed.wait(0.05)
            super()._validate_live(feed, targets, facts, current)

    original_publish = wiki.publish_generated
    publication_calls = 0

    def publish_while_locked(*args, **kwargs):
        nonlocal publication_calls
        check_lock = publication_calls == 0
        publication_calls += 1
        if check_lock:
            assert not committed.is_set()
        result = original_publish(*args, **kwargs)
        if check_lock:
            assert not committed.is_set()
        return result

    monkeypatch.setattr(wiki, "publish_generated", publish_while_locked)
    synthesis = _LockedRaceSynthesis(ledger, consumers, wiki, _Renderer())
    try:
        first = await synthesis.run()
        assert synthesis.thread is not None
        synthesis.thread.join(2)
        assert not synthesis.thread.is_alive()
        assert committed.is_set() and not failures
        watermark = await consumers.get("memory.synthesis")
        assert first.advanced and watermark is not None
        assert watermark.revision == pinned

        second = await synthesis.run()

        assert second.advanced
        assert any(page.page.title == "Later" for page in wiki.list_pages())
    finally:
        await consumers.close()
