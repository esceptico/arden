from collections.abc import Iterable

from arden.agent.types.tools import ToolSourceRef, decode_source_refs, normalize_source_refs


def persistable_tool_result_data(
    data: dict | None,
    source_refs: Iterable[ToolSourceRef] | None = None,
) -> dict | None:
    persisted: dict = {}
    if isinstance(data, dict):
        child_agent = data.get("child_agent")
        if isinstance(child_agent, dict):
            persisted["child_agent"] = child_agent
        provenance = data.get("provenance")
        if isinstance(provenance, dict):
            persisted["provenance"] = provenance

    if source_refs is not None:
        refs = normalize_source_refs(source_refs)
    elif isinstance(data, dict) and "source_refs" in data:
        refs = decode_source_refs(data["source_refs"])
    else:
        refs = ()
    if refs:
        persisted["source_refs"] = [ref.to_dict() for ref in refs]
    return persisted or None
