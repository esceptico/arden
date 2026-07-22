from collections.abc import Iterable, Mapping

from arden.agent.types.tools import ToolSourceRef, normalize_source_refs


def persistable_tool_result_data(
    data: dict | None,
    source_refs: Iterable[ToolSourceRef | Mapping[str, object]] | None = None,
) -> dict | None:
    persisted: dict = {}
    if isinstance(data, dict):
        child_agent = data.get("child_agent")
        if isinstance(child_agent, dict):
            persisted["child_agent"] = child_agent
        provenance = data.get("provenance")
        if isinstance(provenance, dict):
            persisted["provenance"] = provenance

    raw_refs = data.get("source_refs") if source_refs is None and isinstance(data, dict) else source_refs
    refs = normalize_source_refs(raw_refs)
    if refs:
        persisted["source_refs"] = [ref.to_dict() for ref in refs]
    return persisted or None
