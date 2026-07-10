from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel

from ntrp.areas.asks import AskStore
from ntrp.areas.models import Area, Ask

# Observe-mode toolset as DATA: the automation's tool_scope allowlist
# (visible and editable on the automation itself) instead of a code-side
# toolset hack. Read/search surfaces + the memory tools that edit the
# area's own page — nothing that acts on the outside world.
OBSERVE_TOOL_SCOPE = [
    "memory_*",
    "recall",
    "remember",
    "forget",
    "web_search",
    "web_fetch",
    "read_file",
    "list_files",
    "find_files",
    "search_text",
    "current_time",
    "update_todos",
]

_CONTRACT = {
    "observe": "You may read anything and update this area's topic page, but take no external action.",
    "act": "You may run this area's automations and workflows; irreversible actions still require approval.",
}


class AreaAskDraft(BaseModel):
    text: str
    kind: Literal["review", "decide", "act", "drift"]


class AreaAskNomination(BaseModel):
    """The run's structured output (registered as "area_ask"): at most ONE
    ask, or null when the day is quiet. Schema-validated by the constrained
    final step, so the transcript stays prose and the nomination arrives as
    a guaranteed-shaped object."""

    ask: AreaAskDraft | None


def area_agent_instructions(area: Area) -> str:
    """The automation's description = the standing per-turn message. The
    fresh topic page is NOT embedded here — it arrives via the AREA system
    block on the area-tagged channel session, so every turn sees current
    state while these instructions stay static."""
    return (
        f"You are the standing agent for the '{area.title}' area of the user's life. "
        f"Its topic page is in your AREA context block.\n"
        f"Autonomy contract ({area.autonomy}): {_CONTRACT[area.autonomy]}\n\n"
        "This turn: absorb what changed in this domain since your last turn (your channel "
        "history is your own past runs), update the topic page if warranted (memory tools), "
        "and decide whether ANYTHING needs the user.\n"
        "Ask-worthy: something new that needs their judgment, a drift between a commitment "
        "and reality, or a stale decision-ready open loop they haven't touched. Routine "
        "tracking is not ask-worthy.\n"
        "End with a short prose report. Afterwards you will be asked for a structured "
        "nomination: at most ONE ask — the single highest-leverage item — or none. "
        "Silence is correct on a quiet day.\n"
        "Pick the kind that fits, dimmest that's true:\n"
        "- review: an FYI — you did or noticed something worth a glance, no decision needed.\n"
        "- decide: a choice or judgment call is waiting on them.\n"
        "- act: they need to take a concrete external step (send, book, pay, submit).\n"
        "- drift: a commitment and reality have diverged and it's slipping."
    )


def record_area_run(asks: AskStore, area_key: str, page_path: str, structured_output: dict | None, run_ref: str) -> None:
    """Post-run ask sync (called from the outbox run-completed pipeline):
    every run re-decides the area's ONE ask — silence retires the previous
    nomination just like a new one supersedes it. `structured_output` is the
    schema-validated AreaAskNomination dump from the run (or None when the
    constrained step failed — treated as silence)."""
    nominated = (structured_output or {}).get("ask")
    asks.retire_active_agent_asks(area_key)
    if nominated:
        asks.upsert(
            Ask(
                id=f"agent:{area_key}:{uuid4().hex[:8]}",
                area_key=area_key,
                text=nominated["text"],
                kind=nominated["kind"],
                source="agent",
                actions=[{"verb": "open_page", "ref": page_path}],
                state="active",
                created_at=datetime.now(UTC).isoformat(),
                provenance=run_ref,
            )
        )
