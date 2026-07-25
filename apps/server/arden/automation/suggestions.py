import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from arden.automation.prompts import AUTOMATION_SUGGESTER_SYSTEM
from arden.automation.triggers import Trigger, build_trigger
from arden.constants import MAX_AUTOMATION_SUGGESTIONS
from arden.logging import get_logger
from arden.memory.models import Kind
from arden.observability import observed_trace

_logger = get_logger(__name__)

SuggestionStatus = Literal["active", "dismissed", "accepted"]


@dataclass
class AutomationSuggestion:
    id: str
    name: str
    description: str | None
    prompt: str
    triggers: list[Trigger]
    rationale: str
    category: str
    evidence: list[str] = field(default_factory=list)
    icon: str | None = None
    status: SuggestionStatus = "active"
    created_at: datetime | None = None
    source_automation_id: str | None = None


class ScheduleDraft(BaseModel):
    trigger_type: Literal["time", "event"]
    at: str | None = None
    days: str | None = None
    every: str | None = None
    event_type: str | None = None
    lead_minutes: int | None = None


class SuggestionDraft(BaseModel):
    name: str
    description: str
    prompt: str
    schedule: ScheduleDraft
    rationale: str
    category: str
    evidence: list[str] = []
    icon: str | None = None


class SuggestionSet(BaseModel):
    suggestions: list[SuggestionDraft]


_MAX_SESSIONS = 20
_MAX_MEMORY_RECORDS = 30


class AutomationSuggester:
    def __init__(self, *, records, sessions, automations, cheap_llm, model):
        self.records = records
        self.sessions = sessions
        self.automations = automations
        self.cheap_llm = cheap_llm
        self.model = model

    @observed_trace("automation.suggestions", tags="automation")
    async def run(self) -> str:
        context = await self._gather()
        response = await self.cheap_llm.completion(
            messages=[
                {"role": "system", "content": AUTOMATION_SUGGESTER_SYSTEM},
                {"role": "user", "content": context},
            ],
            model=self.model,
            response_format=SuggestionSet,
        )
        drafts = self._parse(response).suggestions

        kept: list[AutomationSuggestion] = []
        dropped = 0
        now = datetime.now(UTC)
        for draft in drafts:
            triggers = self._validate(draft)
            if triggers is None:
                dropped += 1
                continue
            kept.append(
                AutomationSuggestion(
                    id=str(uuid.uuid4()),
                    name=draft.name,
                    description=draft.description,
                    prompt=draft.prompt,
                    triggers=triggers,
                    rationale=draft.rationale,
                    category=draft.category,
                    evidence=draft.evidence,
                    icon=draft.icon,
                    created_at=now,
                )
            )
            if len(kept) >= MAX_AUTOMATION_SUGGESTIONS:
                break

        await self.automations.replace_active_suggestions(kept)
        return f"suggestions={len(kept)}; dropped={dropped}"

    def _parse(self, response) -> SuggestionSet:
        content = response.choices[0].message.content
        if isinstance(content, SuggestionSet):
            return content
        return SuggestionSet.model_validate_json(content)

    def _validate(self, draft: SuggestionDraft) -> list[Trigger] | None:
        schedule = draft.schedule
        try:
            trigger, _ = build_trigger(
                schedule.trigger_type,
                at=schedule.at,
                days=schedule.days,
                every=schedule.every,
                event_type=schedule.event_type,
                lead_minutes=schedule.lead_minutes,
            )
        except ValueError as e:
            _logger.warning("Dropping suggestion %r with invalid schedule: %s", draft.name, e)
            return None
        return [trigger]

    async def _gather(self) -> str:
        sections: list[str] = []

        memory = await self._memory_records()
        if memory:
            sections.append("Memory (durable user facts):\n" + memory)

        chats = await self._recent_sessions()
        if chats:
            sections.append("Recent chats:\n" + chats)

        existing = await self._existing_automations()
        if existing:
            sections.append("Existing automations (do NOT duplicate):\n" + existing)

        excluded = await self._excluded_signatures()
        if excluded:
            sections.append("Previously dismissed/accepted (do NOT re-suggest):\n" + excluded)

        if not sections:
            return "No signal available."
        return "\n\n".join(sections)

    async def _memory_records(self) -> str:
        try:
            # Durable kinds only — low-trust observations (90d integration noise) must not
            # flood the small window (mirrors the dreamer's kind-restrict). Keeps the
            # "durable user facts" label honest.
            records = await self.records.list(limit=_MAX_MEMORY_RECORDS, kinds=[Kind.FACT, Kind.DIRECTIVE, Kind.LESSON])
        except Exception as e:
            _logger.debug("gather memory records failed: %s", e)
            return ""
        return "\n".join(f"- [{r.kind}] {r.text}" for r in records)

    async def _recent_sessions(self) -> str:
        try:
            rows = await self.sessions.list_sessions(limit=_MAX_SESSIONS)
        except Exception as e:
            _logger.debug("gather sessions failed: %s", e)
            return ""
        return "\n".join(f"- {row['name']}" for row in rows if row.get("name"))

    async def _existing_automations(self) -> str:
        try:
            automations = await self.automations.list_all()
        except Exception as e:
            _logger.debug("gather existing automations failed: %s", e)
            return ""
        return "\n".join(f"- {a.name} — {a.description or '(description pending)'}" for a in automations)

    async def _excluded_signatures(self) -> str:
        try:
            signatures = await self.automations.list_excluded_signatures()
        except Exception as e:
            _logger.debug("gather excluded signatures failed: %s", e)
            return ""
        return "\n".join(f"- {sig}" for sig in signatures)
