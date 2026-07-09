from dataclasses import dataclass
from typing import Literal

AskKind = Literal["review", "decide", "act", "drift"]
AskState = Literal["active", "done", "dismissed", "snoozed"]
Autonomy = Literal["observe", "act"]


@dataclass
class Slice:
    """Projection of a capability-bearing project row: key IS the project_id."""

    key: str
    title: str
    page_path: str | None  # vault-relative, e.g. "topics/o-1a.md"
    autonomy: Autonomy | None  # non-null iff the slice has a standing agent


def slices_from_projects(projects: list[dict]) -> list[Slice]:
    """The containers that are slices: any project carrying a capability
    (page or standing agent). Plain containers never surface as slices."""
    return [
        Slice(
            key=p["project_id"],
            title=p["name"],
            page_path=p.get("page_path"),
            autonomy=p.get("autonomy"),
        )
        for p in projects
        if p.get("page_path") or p.get("autonomy")
    ]


@dataclass
class Ask:
    id: str
    slice_key: str
    text: str
    kind: AskKind
    source: str  # "approval" | "run_failed" | "agent_output" | "open_loop" | "agent"
    actions: list[dict]  # [{"verb": "open_session", "ref": "<id>"}, ...]
    state: AskState
    created_at: str  # ISO
    snoozed_until: str | None = None
    provenance: str | None = None  # run/source that produced it
