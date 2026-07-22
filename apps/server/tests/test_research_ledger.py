import asyncio

import pytest

from arden.agent import SharedLedger
from arden.agent.coverage import ResearchOutline
from arden.agent.ledger import FactNote, GapNote


def test_ledger_records_research_notes_in_order():
    ledger = SharedLedger()
    first = FactNote(claim="Arden has research agents.", source="repo")
    second = GapNote(what_missing="No coverage for prompt behavior.")

    ledger.add_note(first)
    ledger.add_note(second)

    assert ledger.notes == [first, second]


@pytest.mark.asyncio
async def test_ledger_claim_read_skips_only_after_success():
    ledger = SharedLedger()

    first_key = await ledger.claim_read("search", {"query": "mcp"})
    assert first_key is not None

    waiter = asyncio.create_task(ledger.claim_read("search", {"query": "mcp"}))
    await asyncio.sleep(0)
    assert not waiter.done()

    ledger.finish_read(first_key, succeeded=True)

    assert await waiter is None


@pytest.mark.asyncio
async def test_ledger_claim_read_retries_after_failure():
    ledger = SharedLedger()

    first_key = await ledger.claim_read("search", {"query": "mcp"})
    assert first_key is not None

    waiter = asyncio.create_task(ledger.claim_read("search", {"query": "mcp"}))
    await asyncio.sleep(0)
    assert not waiter.done()

    ledger.finish_read(first_key, succeeded=False)

    retry_key = await waiter
    assert retry_key == first_key


def test_ledger_reports_outline_coverage_and_gap_notes():
    ledger = SharedLedger()
    ledger.set_outline(ResearchOutline.from_titles(["Repo state", "Prompt behavior"]))

    ledger.cover_section("Repo state", "apps/server/arden/tools/research.py")
    report = ledger.coverage_report()
    gaps = ledger.add_coverage_gap_notes()

    assert report is not None
    assert report.coverage == 0.5
    assert report.gaps == ["Prompt behavior"]
    assert gaps == [GapNote(what_missing="No source covered outline section: Prompt behavior")]
    assert ledger.add_coverage_gap_notes() == []
