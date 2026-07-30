"""Cross-domain wiki health evidence assembled at the server boundary."""

from arden.memory.facts.ledger import FactLedger
from arden.wiki.health import (
    WikiHealthIssue,
    WikiHealthIssueCode,
    fact_page_owner,
)
from arden.wiki.models import WikiChangesReport
from arden.wiki.provenance import parse_fact_provenance


def dangling_fact_citation_issues(
    ledger: FactLedger,
    report: WikiChangesReport,
) -> tuple[WikiHealthIssue, ...]:
    issues: list[WikiHealthIssue] = []
    for record in report.current_records:
        provenance = parse_fact_provenance(record.page.metadata, record.resource.path)
        for citation in provenance.citations:
            try:
                ledger.get_version(citation.fact_id, citation.version)
            except KeyError:
                issues.append(
                    WikiHealthIssue(
                        WikiHealthIssueCode.DANGLING_CITATION,
                        record.resource.path,
                        f"cited fact revision is unavailable for {citation.fact_id}",
                        fact_page_owner(record.page.metadata),
                    )
                )
    return tuple(issues)
