from arden.agent import SharedLedger
from arden.agent.coverage import ResearchOutline
from arden.agent.ledger import FactNote
from tests.research_eval_helpers import ResearchLedgerEvalCase, run_research_ledger_eval_cases


def test_research_ledger_eval_passes_when_required_notes_and_coverage_exist():
    ledger = SharedLedger()
    ledger.add_note(FactNote(claim="research agents have ledger tools", source="repo"))
    ledger.set_outline(ResearchOutline.from_titles(["Repo state", "Prompt behavior"]))
    ledger.cover_section("Repo state", "apps/server/arden/tools/research.py")
    ledger.cover_section("Prompt behavior", "research system prompt")

    result = run_research_ledger_eval_cases(
        ledger,
        [
            ResearchLedgerEvalCase(
                name="coverage",
                required_note_kinds={"fact"},
                required_covered_sections={"Repo state", "Prompt behavior"},
                min_coverage=1.0,
            )
        ],
    )

    assert result.passed
    assert result.failed == []


def test_research_ledger_eval_flags_missing_sections_notes_and_forbidden_gaps():
    ledger = SharedLedger()
    ledger.set_outline(ResearchOutline.from_titles(["Repo state", "Prompt behavior"]))
    ledger.cover_section("Repo state", "apps/server/arden/tools/research.py")
    ledger.add_coverage_gap_notes()

    result = run_research_ledger_eval_cases(
        ledger,
        [
            ResearchLedgerEvalCase(
                name="coverage",
                required_note_kinds={"fact", "gap"},
                required_covered_sections={"Repo state", "Prompt behavior"},
                forbidden_gap_substrings={"Prompt behavior"},
                min_coverage=1.0,
            )
        ],
    )

    case = result.cases[0]
    assert not result.passed
    assert case.missing_note_kinds == {"fact"}
    assert case.missing_sections == {"Prompt behavior"}
    assert case.forbidden_gaps == {"No source covered outline section: Prompt behavior"}
    assert case.coverage == 0.5
