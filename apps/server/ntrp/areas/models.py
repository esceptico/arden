from dataclasses import dataclass
from typing import Literal

AskKind = Literal["review", "decide", "act", "drift"]
AskState = Literal["active", "done", "dismissed", "snoozed"]
Autonomy = Literal["observe", "act"]


@dataclass
class Area:
    """Projection of a capability-bearing area row: key IS the area_id."""

    key: str
    title: str
    page_path: str | None  # vault-relative, e.g. "topics/o-1a.md"
    autonomy: Autonomy | None  # non-null iff the area has a standing agent


def areas_from_records(areas: list[dict]) -> list[Area]:
    """The containers that are areas: any area carrying a capability
    (page or standing agent). Plain containers never surface as areas."""
    return [
        Area(
            key=p["area_id"],
            title=p["name"],
            page_path=p.get("page_path"),
            autonomy=p.get("autonomy"),
        )
        for p in areas
        if p.get("page_path") or p.get("autonomy")
    ]


@dataclass
class Ask:
    id: str
    area_key: str
    text: str
    kind: AskKind
    source: str  # "approval" | "run_failed" | "agent_output" | "open_loop" | "agent"
    actions: list[dict]  # [{"verb": "open_session", "ref": "<id>"}, ...]
    state: AskState
    created_at: str  # ISO
    snoozed_until: str | None = None
    provenance: str | None = None  # run/source that produced it
