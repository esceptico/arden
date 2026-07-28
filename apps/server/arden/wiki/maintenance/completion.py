"""Typed completion call for conservative Wiki Maintenance reviews."""

from arden.llm.base import CompletionClient
from arden.wiki.maintenance.runner import WikiMaintenanceDecision, WikiMaintenancePreparedReport

_SYSTEM = """You review one Markdown wiki change using only the supplied report.
User edits are authoritative. Preserve their intent. Do not add speculative
insights or claims. Never propose renames, moves, archives, merges, redirects,
or edits inside generated regions.

Return exactly one outcome:
- no_change: no safe ordinary edit is needed.
- updates: safe title, aliases, or ordinary body edits, using only opaque P###
  page tokens shown in the report.
- needs_review: a user decision is required. Give a stable lowercase concern
  key, a concise summary, a proposed resolution, and optional executable
  ordinary edits. Do not use resource IDs or versions: they do not exist here.
"""


async def review_wiki_maintenance(
    client: CompletionClient,
    model: str,
    report: WikiMaintenancePreparedReport,
    *,
    reasoning_effort: str | None = None,
) -> WikiMaintenanceDecision:
    """Request one typed decision from the completion API."""

    response = await client.completion(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": report.markdown},
        ],
        response_format=WikiMaintenanceDecision,
        reasoning_effort=reasoning_effort,
    )
    if not response.choices or response.choices[0].message.content is None:
        raise ValueError("maintenance reviewer returned no decision")
    content = response.choices[0].message.content
    return (
        content
        if isinstance(content, WikiMaintenanceDecision)
        else WikiMaintenanceDecision.model_validate_json(content)
    )
