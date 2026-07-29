from datetime import UTC, datetime
from pathlib import Path

from arden.constants import BUILTIN_MEMORY_DREAM_ID
from arden.memory.facts.ledger import FactLedger
from arden.revisions import ManagedFileRepository
from arden.server.wiki_health import dangling_fact_citation_issues
from arden.wiki.health import WikiHealthIssueCode, WikiHealthIssueOwner
from arden.wiki.service import WikiService

NOW = datetime(2026, 7, 29, tzinfo=UTC)


def _ledger(tmp_path: Path) -> FactLedger:
    return FactLedger(tmp_path / "facts", clock=lambda: NOW)


def _create_fact(ledger: FactLedger, fact_id: str) -> None:
    ledger.commit(
        ledger.plan(
            [
                {
                    "op": "create",
                    "fact_id": fact_id,
                    "text": "Canonical fact",
                    "kind": "fact",
                    "subjects": ["health"],
                    "scope": {"kind": "user", "key": None},
                    "sources": [{"kind": "test", "ref": fact_id}],
                }
            ],
            actor="test",
            origin="test",
            reason="seed health fact",
        )
    )


def _wiki(tmp_path: Path) -> WikiService:
    return WikiService(
        ManagedFileRepository(
            tmp_path / "wiki-pages",
            history_root=tmp_path / "wiki-history",
        )
    )


def test_missing_exact_fact_version_is_dangling(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    wiki = _wiki(tmp_path)
    wiki.create_page(
        page_id="missing",
        path="topics/missing.md",
        title="Missing",
        metadata={
            "generated_from_revision": "a" * 64,
            "fact_citations": [{"fact_id": "missing-fact", "version": "b" * 64}],
        },
    )

    issues = dangling_fact_citation_issues(ledger, wiki.changes_since(None))

    assert len(issues) == 1
    assert issues[0].code is WikiHealthIssueCode.DANGLING_CITATION
    assert issues[0].target == "topics/missing.md"
    assert issues[0].owner is WikiHealthIssueOwner.SYNTHESIS


def test_historical_retracted_fact_version_remains_valid_provenance(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _create_fact(ledger, "fact-1")
    historical_version = ledger.get("fact-1").version
    ledger.commit(
        ledger.plan(
            [{"op": "retract", "fact_id": "fact-1", "reason": "test retraction"}],
            actor="test",
            origin="test",
            reason="retract health fact",
        )
    )
    wiki = _wiki(tmp_path)
    wiki.create_page(
        page_id="historical",
        path="topics/historical.md",
        title="Historical",
        metadata={
            "generated_from_revision": "a" * 64,
            "fact_citations": [{"fact_id": "fact-1", "version": historical_version}],
        },
    )

    assert dangling_fact_citation_issues(ledger, wiki.changes_since(None)) == ()


def test_dream_page_assigns_dangling_citation_to_dream(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    wiki = _wiki(tmp_path)
    wiki.create_page(
        page_id="dream",
        path="insights/2026-07.md",
        title="Dream",
        metadata={
            "producer_automation_id": BUILTIN_MEMORY_DREAM_ID,
            "generated_from_revision": "a" * 64,
            "fact_citations": [{"fact_id": "missing-fact", "version": "b" * 64}],
        },
    )

    issues = dangling_fact_citation_issues(ledger, wiki.changes_since(None))

    assert issues[0].owner is WikiHealthIssueOwner.DREAM


def test_mixed_valid_and_invalid_citations_keep_valid_dangling_evidence(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    wiki = _wiki(tmp_path)
    wiki.create_page(
        page_id="mixed",
        path="topics/mixed.md",
        title="Mixed",
        metadata={
            "generated_from_revision": "a" * 64,
            "fact_citations": [
                {"fact_id": "missing-fact", "version": "b" * 64},
                {"fact_id": "", "version": "c" * 64},
            ],
        },
    )

    issues = dangling_fact_citation_issues(ledger, wiki.changes_since(None))

    assert len(issues) == 1
    assert issues[0].code is WikiHealthIssueCode.DANGLING_CITATION


def test_invalid_nul_fact_id_stays_validation_evidence(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    wiki = _wiki(tmp_path)
    wiki.create_page(
        page_id="invalid",
        path="topics/invalid.md",
        title="Invalid",
        metadata={
            "generated_from_revision": "a" * 64,
            "fact_citations": [{"fact_id": "bad\0fact", "version": "b" * 64}],
        },
    )
    report = wiki.changes_since(None)

    assert dangling_fact_citation_issues(ledger, report) == ()
    assert any(warning.code == "invalid_fact_citations" for warning in report.warnings)
