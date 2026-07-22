from dataclasses import dataclass, field

from arden.agent.ledger import GapNote, SharedLedger


@dataclass(frozen=True, slots=True)
class ResearchLedgerEvalCase:
    name: str
    required_note_kinds: set[str] = field(default_factory=set)
    required_covered_sections: set[str] = field(default_factory=set)
    forbidden_gap_substrings: set[str] = field(default_factory=set)
    min_coverage: float | None = None
    tags: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class ResearchLedgerEvalCaseResult:
    name: str
    passed: bool
    missing_note_kinds: set[str]
    missing_sections: set[str]
    forbidden_gaps: set[str]
    coverage: float | None


@dataclass(frozen=True, slots=True)
class ResearchLedgerEvalResult:
    cases: list[ResearchLedgerEvalCaseResult]

    @property
    def passed(self) -> bool:
        return all(case.passed for case in self.cases)

    @property
    def failed(self) -> list[ResearchLedgerEvalCaseResult]:
        return [case for case in self.cases if not case.passed]


def run_research_ledger_eval_cases(
    ledger: SharedLedger,
    cases: list[ResearchLedgerEvalCase],
) -> ResearchLedgerEvalResult:
    notes = ledger.notes
    note_kinds = {note.kind for note in notes}
    gap_texts = [note.what_missing for note in notes if isinstance(note, GapNote)]
    report = ledger.coverage_report()
    covered_sections = {title for title, sources in report.sections.items() if sources} if report is not None else set()
    coverage = report.coverage if report is not None else None

    return ResearchLedgerEvalResult(
        cases=[
            _run_research_ledger_eval_case(
                case,
                note_kinds=note_kinds,
                covered_sections=covered_sections,
                gap_texts=gap_texts,
                coverage=coverage,
            )
            for case in cases
        ]
    )


def _run_research_ledger_eval_case(
    case: ResearchLedgerEvalCase,
    *,
    note_kinds: set[str],
    covered_sections: set[str],
    gap_texts: list[str],
    coverage: float | None,
) -> ResearchLedgerEvalCaseResult:
    missing_note_kinds = case.required_note_kinds - note_kinds
    missing_sections = case.required_covered_sections - covered_sections
    forbidden_gaps = {
        gap_text for gap_text in gap_texts for forbidden in case.forbidden_gap_substrings if forbidden in gap_text
    }
    coverage_failed = case.min_coverage is not None and (coverage is None or coverage < case.min_coverage)
    passed = not missing_note_kinds and not missing_sections and not forbidden_gaps and not coverage_failed

    return ResearchLedgerEvalCaseResult(
        name=case.name,
        passed=passed,
        missing_note_kinds=missing_note_kinds,
        missing_sections=missing_sections,
        forbidden_gaps=forbidden_gaps,
        coverage=coverage,
    )
