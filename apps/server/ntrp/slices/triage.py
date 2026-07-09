import json

from pydantic import BaseModel

from ntrp.logging import get_logger

_logger = get_logger(__name__)

TRIAGE_SYSTEM = """You file a just-started chat into the user's existing \
workspace. You are given the opening exchange of a conversation and a list of \
existing HOMES — life-domain slices and projects the user already keeps.

Choose exactly ONE:
- move: the chat clearly belongs in an existing home. Return that home's key.
- create: the chat is about a real, nameable, ongoing thing that has no \
existing home. Propose a short title (2-4 words, Title Case).
- none: a throwaway, a one-off question, something generic, or anything with \
no lasting home — and whenever nothing clearly fits.

When in doubt, choose none. A wrong or noisy suggestion costs trust; silence \
costs nothing. Only choose move when the fit is unambiguous; only choose \
create when the topic plainly deserves its own standing space. Give one short \
plain-language rationale grounded in what the chat is actually about."""


class TriageTarget(BaseModel):
    key: str  # the container's project_id
    title: str


class TriageDecision(BaseModel):
    decision: str  # "move" | "create" | "none"
    target: TriageTarget | None = None
    new_title: str | None = None
    rationale: str = ""


async def triage_chat(*, transcript: str, candidates: list[dict], cheap_llm, model) -> TriageDecision:
    """One cheap classify: file this chat into an existing home, propose a new
    one, or stay silent. Any failure degrades to `none` — triage is additive,
    a broken call must never surface anything."""
    homes = [{"key": c["key"], "title": c["title"]} for c in candidates]
    payload = json.dumps({"conversation": transcript, "homes": homes}, ensure_ascii=False)
    try:
        response = await cheap_llm.completion(
            messages=[
                {"role": "system", "content": TRIAGE_SYSTEM},
                {"role": "user", "content": payload},
            ],
            model=model,
            response_format=TriageDecision,
        )
        content = response.choices[0].message.content
        decision = content if isinstance(content, TriageDecision) else TriageDecision.model_validate_json(content)
    except Exception as e:
        _logger.warning("Chat triage failed; treating as none: %s", e)
        return TriageDecision(decision="none")
    return _validated(decision, candidates)


def _validated(decision: TriageDecision, candidates: list[dict]) -> TriageDecision:
    """Trust our own catalog over the model's echo: a `move` must name a real
    home (else drop to none), and we re-stamp the title from the catalog so a
    hallucinated label can't reach the UI."""
    if decision.decision == "move":
        by_key = {c["key"]: c for c in candidates}
        home = by_key.get(decision.target.key) if decision.target else None
        if home is None:
            _logger.warning("Triage proposed unknown home; dropped to none")
            return TriageDecision(decision="none")
        return TriageDecision(
            decision="move",
            target=TriageTarget(key=home["key"], title=home["title"]),
            rationale=decision.rationale,
        )
    if decision.decision == "create":
        title = (decision.new_title or "").strip()
        if not title:
            return TriageDecision(decision="none")
        return TriageDecision(decision="create", new_title=title, rationale=decision.rationale)
    return TriageDecision(decision="none")
