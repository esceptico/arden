from datetime import UTC, datetime
from pathlib import Path

import pytest

from arden.memory.facts.dream import (
    ACTOR,
    ORIGIN,
    DreamEvidence,
    DreamInsight,
    FactDream,
    FactDreamError,
)
from arden.memory.facts.ledger import FactLedger
from arden.revisions import ChangeSet, Create, ManagedFileRepository, RevisionConflictError, Update
from arden.wiki.pages import create_page, extract_generated_region, update_generated_region
from arden.wiki.service import GeneratedRegionConflictError, WikiService

NOW = datetime(2026, 7, 31, 22, 30, tzinfo=UTC)


class _Renderer:
    def __init__(self, insights: tuple[DreamInsight, ...] | None = None) -> None:
        self.insights = insights
        self.calls: list[tuple[object, tuple[DreamEvidence, ...]]] = []

    async def render(self, *, month, evidence: tuple[DreamEvidence, ...]) -> tuple[DreamInsight, ...]:
        self.calls.append((month, evidence))
        if self.insights is not None:
            return self.insights
        alpha = next(item.token for item in evidence if item.subjects == ("Alpha",))
        beta = next(item.token for item in evidence if item.subjects == ("Beta",))
        return (DreamInsight("The two domains reinforce each other.", (alpha, beta)),)


def _source(fact_id: str) -> dict[str, object]:
    return {
        "kind": "chat_message",
        "ref": f"session:{fact_id}",
        "occurred_at": "2026-07-28T11:59:00Z",
        "time_precision": "second",
    }


def _change(fact_id: str, subject: str, **extra: object) -> dict[str, object]:
    return {
        "op": "create",
        "fact_id": fact_id,
        "text": f"{subject} evidence {fact_id}",
        "kind": "fact",
        "labels": [],
        "subjects": [subject],
        "scope": {"kind": "area", "key": "general"},
        "sources": [_source(fact_id)],
        "certainty": "confirmed",
        "evidence_class": "direct",
        **extra,
    }


def _commit(ledger: FactLedger, *changes: dict[str, object]) -> None:
    plan = ledger.plan(changes, actor="test", origin="test", reason="seed Dream evidence")
    ledger.commit(plan)


def _setup(
    tmp_path: Path,
    renderer: _Renderer | None = None,
    *,
    clock=lambda: NOW,
) -> tuple[FactLedger, WikiService, _Renderer, FactDream]:
    ledger = FactLedger(tmp_path / "facts", clock=lambda: NOW)
    wiki = WikiService(ManagedFileRepository(tmp_path / "wiki"))
    selected = renderer or _Renderer()
    return ledger, wiki, selected, FactDream(ledger, wiki, selected, clock=clock)


def _seed_cross_domain(ledger: FactLedger) -> None:
    _commit(
        ledger,
        *(_change(f"a{index}", "Alpha") for index in range(3)),
        *(_change(f"b{index}", "Beta") for index in range(3)),
    )


@pytest.mark.asyncio
async def test_dream_skips_thin_evidence_without_creating_a_page(tmp_path: Path) -> None:
    ledger, wiki, renderer, dream = _setup(tmp_path)
    _commit(ledger, _change("a", "Alpha"), _change("b", "Beta"))

    result = await dream.run()

    assert result.empty is True
    assert result.published is False
    assert renderer.calls == []
    assert wiki.snapshot().pages == ()


@pytest.mark.asyncio
async def test_dream_publishes_only_direct_confirmed_cross_domain_evidence(tmp_path: Path) -> None:
    ledger, wiki, renderer, dream = _setup(tmp_path)
    _seed_cross_domain(ledger)
    _commit(
        ledger,
        _change("inferred", "Gamma", evidence_class="inferred"),
        _change("uncertain", "Gamma", certainty="uncertain"),
    )

    first = await dream.run()
    second = await dream.run()

    assert first.published is True
    assert first.insight_count == 1
    assert second.published is False
    assert len(renderer.calls) == 2
    assert {item.fact_id for item in renderer.calls[0][1]} == {
        "a0",
        "a1",
        "a2",
        "b0",
        "b1",
        "b2",
    }

    page = wiki.read_page("insights-2026-07")
    assert page.resource.path == "insights/2026-07.md"
    assert page.page.title == "Insights 2026-07"
    assert page.page.metadata["producer_automation_id"] == "builtin-memory-dream"
    assert page.page.metadata["provisional"] is True
    citations = page.page.metadata["fact_citations"]
    assert {item["fact_id"] for item in citations} == {"a0", "b0"}
    assert all(len(item["version"]) == 64 for item in citations)
    generated = extract_generated_region(page.content, expected_page_id=page.page.page_id)
    assert b"not canonical facts" in generated
    assert b"fact:a0" in generated
    assert b"fact:b0" in generated
    commits = wiki.repository.history(resource_id=page.page.page_id)
    assert commits[0].actor == ACTOR
    assert commits[0].origin == ORIGIN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "insight,error",
    [
        (DreamInsight("Unknown.", ("F001", "F999")), "unknown token"),
        (DreamInsight("One domain.", ("F001", "F002")), "distinct subjects"),
        (DreamInsight("Duplicate token.", ("F001", "F001")), "distinct fact tokens"),
        (DreamInsight("Spoofed fact:F001.", ("F001", "F004")), "claim is invalid"),
    ],
)
async def test_dream_rejects_invalid_model_citations(
    tmp_path: Path,
    insight: DreamInsight,
    error: str,
) -> None:
    ledger, _wiki, _renderer, dream = _setup(tmp_path, _Renderer((insight,)))
    _seed_cross_domain(ledger)

    with pytest.raises(FactDreamError, match=error):
        await dream.run()


@pytest.mark.asyncio
async def test_dream_rejects_fact_race_without_partial_wiki_write(tmp_path: Path) -> None:
    ledger, wiki, renderer, dream = _setup(tmp_path)
    _seed_cross_domain(ledger)
    original = renderer.render

    async def race(*, month, evidence):
        result = await original(month=month, evidence=evidence)
        _commit(ledger, _change("raced", "Gamma"))
        return result

    renderer.render = race

    with pytest.raises(FactDreamError, match="facts changed"):
        await dream.run()
    assert wiki.snapshot().pages == ()


@pytest.mark.asyncio
async def test_dream_rejects_wiki_race(tmp_path: Path) -> None:
    ledger, wiki, renderer, dream = _setup(tmp_path)
    _seed_cross_domain(ledger)
    original = renderer.render

    async def race(*, month, evidence):
        result = await original(month=month, evidence=evidence)
        page = b"---\npage_id: note\ntitle: Note\naliases: []\nlifecycle: active\n---\n\n"
        wiki.repository.commit(
            ChangeSet(
                operations=(Create("note", "note.md", page),),
                actor="user",
                origin="desktop",
                reason="concurrent note",
                idempotency_key="concurrent-note",
                expected_head=wiki.repository.head,
            )
        )
        return result

    renderer.render = race

    with pytest.raises(RevisionConflictError):
        await dream.run()
    assert {record.page.page_id for record in wiki.snapshot().pages} == {"note"}


@pytest.mark.asyncio
async def test_dream_yields_to_user_generated_region_edit(tmp_path: Path) -> None:
    ledger, wiki, _renderer, dream = _setup(tmp_path)
    _seed_cross_domain(ledger)
    await dream.run()

    record = wiki.read_page("insights-2026-07")
    edited = update_generated_region(
        record.content,
        expected_page_id=record.page.page_id,
        generated=b"User owns this now.\n",
    )
    wiki.repository.commit(
        ChangeSet(
            operations=(Update(record.page.page_id, record.resource.version_id, edited),),
            actor="user",
            origin="desktop",
            reason="edit Dream region",
            idempotency_key="edit-dream-region",
            expected_head=wiki.repository.head,
        )
    )

    with pytest.raises(GeneratedRegionConflictError, match="generated region"):
        await dream.run()


@pytest.mark.asyncio
async def test_dream_does_not_take_over_an_unowned_monthly_page(tmp_path: Path) -> None:
    ledger, wiki, _renderer, dream = _setup(tmp_path)
    _seed_cross_domain(ledger)
    content = create_page(page_id="insights-2026-07", title="My insights").to_bytes()
    commit = wiki.repository.commit(
        ChangeSet(
            operations=(Create("insights-2026-07", "insights/2026-07.md", content),),
            actor="user",
            origin="desktop",
            reason="create personal insights",
            idempotency_key="personal-insights",
            expected_head=None,
        )
    )

    with pytest.raises(FactDreamError, match="not owned by Dream"):
        await dream.run()

    assert wiki.repository.head == commit.commit_id
    assert wiki.repository.read("insights-2026-07") == content


@pytest.mark.asyncio
async def test_dream_clears_prior_projection_when_evidence_becomes_thin(tmp_path: Path) -> None:
    ledger, wiki, _renderer, dream = _setup(tmp_path)
    _seed_cross_domain(ledger)
    await dream.run()
    for fact_id in ("b0", "b1", "b2"):
        _commit(ledger, {"op": "retract", "fact_id": fact_id, "reason": "source was wrong"})

    result = await dream.run()

    assert result.published is True
    page = wiki.read_page("insights-2026-07")
    assert extract_generated_region(page.content, expected_page_id=page.page.page_id) == b""
    assert page.page.metadata["fact_citations"] == ()


@pytest.mark.asyncio
async def test_dream_uses_configured_timezone_for_month(tmp_path: Path) -> None:
    instant = datetime(2026, 7, 31, 22, 30, tzinfo=UTC)
    ledger, wiki, _renderer, dream = _setup(
        tmp_path,
        clock=lambda: instant,
    )
    dream = FactDream(ledger, wiki, _Renderer(), timezone_name="Asia/Yerevan", clock=lambda: instant)
    _seed_cross_domain(ledger)

    await dream.run()

    assert wiki.read_page("insights-2026-08").resource.path == "insights/2026-08.md"
