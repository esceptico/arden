"""Typed completion call for canonical fact maintenance."""

from arden.llm.base import CompletionClient
from arden.memory.facts.maintenance.runner import FactMaintenanceDecision, FactMaintenancePreparedCluster

_SYSTEM = """Review one prepared canonical-fact cluster using only the supplied evidence.
Return no_change when evidence is weak or ambiguous.
For no_change, leave every action field null. You may only:
- amend metadata on the changed fact: kind, labels, subjects, lifecycle, or evidence class;
- merge two genuine duplicate claims from this cluster while preserving one as survivor.
- normalize one exact subject of the changed fact to the title of a supplied canonical wiki page.

Never create or rewrite fact text. Never review, expire, retract, age-archive, or perform
general evidence-based supersession. Never edit wiki prose. An inferred fact cannot replace
a direct fact. For normalize_topic, copy old_topic exactly from the changed fact and select
the canonical page only with its opaque P### token. Never return a page ID, path, or invented
page name. Do not normalize when the relationship is ambiguous or the old topic already has
its own page. Use only the opaque F### and P### tokens shown in the cluster.
"""


async def review_fact_maintenance(
    client: CompletionClient,
    model: str,
    cluster: FactMaintenancePreparedCluster,
    *,
    reasoning_effort: str | None = None,
) -> FactMaintenanceDecision:
    """Request one typed decision from the completion API."""

    response = await client.completion(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": cluster.markdown},
        ],
        response_format=FactMaintenanceDecision,
        reasoning_effort=reasoning_effort,
    )
    if not response.choices or response.choices[0].message.content is None:
        raise ValueError("fact maintenance reviewer returned no decision")
    content = response.choices[0].message.content
    return (
        content
        if isinstance(content, FactMaintenanceDecision)
        else FactMaintenanceDecision.model_validate_json(content)
    )
