from collections.abc import Iterable
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from arden.agent.types.tools import ToolSourceRef, decode_source_refs, normalize_source_refs
from arden.core.public_refs import PublicRef


class PersistedChildAgent(BaseModel):
    """Public child-agent metadata that may enter model history."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    agent_ref: PublicRef
    session_ref: PublicRef | None = None
    agent_type: str = Field(min_length=1, max_length=80)
    wait: bool
    status: Literal["running", "completed", "failed", "cancelled", "interrupted"]

    @model_validator(mode="after")
    def session_ref_identifies_the_same_agent(self) -> Self:
        if self.session_ref is not None and self.session_ref != self.agent_ref:
            raise ValueError("session_ref must equal agent_ref")
        return self


class ResearchProvenanceWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    depth: Literal["quick", "normal", "deep"]
    current_depth: int = Field(ge=0, le=100)
    max_depth: int = Field(ge=1, le=100)


class ResearchProvenanceDerivation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    agent_ref: PublicRef


class ResearchProvenance(BaseModel):
    """Bounded, public research metadata that may enter model history."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    query: str = Field(max_length=4_096)
    window: ResearchProvenanceWindow
    derivation: ResearchProvenanceDerivation
    workspace_ref: str = Field(
        pattern=r"^research-[a-z0-9]+(?:-[a-z0-9]+)*:_provenance\.json$",
        max_length=160,
    )


class ToolReferences(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    tool_references: list[str]

    @model_validator(mode="after")
    def valid_names(self) -> Self:
        if not self.tool_references or any(not name.strip() for name in self.tool_references):
            raise ValueError("tool references must contain non-empty tool names")
        if len(set(self.tool_references)) != len(self.tool_references):
            raise ValueError("tool references must be unique")
        return self


def persistable_tool_result_data(
    data: dict | None,
    source_refs: Iterable[ToolSourceRef] | None = None,
) -> dict | None:
    persisted: dict = {}
    if isinstance(data, dict):
        if "child_agent" in data:
            persisted["child_agent"] = PersistedChildAgent.model_validate(data["child_agent"]).model_dump(
                mode="json", exclude_none=True
            )
        if "provenance" in data:
            persisted["provenance"] = ResearchProvenance.model_validate(data["provenance"]).model_dump(mode="json")
        if "tool_references" in data:
            persisted.update(
                ToolReferences.model_validate({"tool_references": data["tool_references"]}).model_dump(mode="json")
            )

    if source_refs is not None:
        refs = normalize_source_refs(source_refs)
    elif isinstance(data, dict) and "source_refs" in data:
        refs = decode_source_refs(data["source_refs"])
    else:
        refs = ()
    if refs:
        persisted["source_refs"] = [ref.to_dict() for ref in refs]
    return persisted or None
