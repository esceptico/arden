from types import SimpleNamespace

from arden.automation.suggestions import (
    AutomationSuggester,
    ScheduleDraft,
    SuggestionDraft,
    SuggestionSet,
)


def _response(suggestion_set: SuggestionSet) -> SimpleNamespace:
    message = SimpleNamespace(content=suggestion_set)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class StubCheapLLM:
    def __init__(self, suggestion_set: SuggestionSet):
        self.suggestion_set = suggestion_set
        self.calls: list[dict] = []

    async def completion(self, **kwargs):
        self.calls.append(kwargs)
        return _response(self.suggestion_set)


class StubRecords:
    async def list(self, *, pinned_only=False, limit=30):
        return [SimpleNamespace(kind="action", text="User reviews arden PRs each morning")]


class StubSessions:
    async def list_sessions(self, limit=20):
        return [{"name": "arden work"}]


class StubAutomations:
    def __init__(self):
        self.replaced: list | None = None

    async def list_all(self):
        return []

    async def list_excluded_signatures(self):
        return []

    async def replace_active_suggestions(self, items):
        self.replaced = items


def _valid_draft(name: str) -> SuggestionDraft:
    return SuggestionDraft(
        name=name,
        description=f"Creates the {name} status report.",
        prompt=f"Run {name}",
        schedule=ScheduleDraft(trigger_type="time", at="09:00", days="mon"),
        rationale="fits the user",
        category="Status reports",
        evidence=["morning PR reviews"],
        icon="GitPullRequest",
    )


def _invalid_draft() -> SuggestionDraft:
    # trigger_type='time' with neither `at` nor `every` → build_trigger raises ValueError.
    return SuggestionDraft(
        name="broken",
        description="Creates an invalid test report.",
        prompt="Run broken",
        schedule=ScheduleDraft(trigger_type="time", days="mon"),
        rationale="should be dropped",
        category="Status reports",
    )


async def test_run_drops_invalid_and_persists_valid():
    suggestion_set = SuggestionSet(suggestions=[_valid_draft("digest-a"), _invalid_draft(), _valid_draft("digest-b")])
    cheap_llm = StubCheapLLM(suggestion_set)
    automations = StubAutomations()

    suggester = AutomationSuggester(
        records=StubRecords(),
        sessions=StubSessions(),
        automations=automations,
        cheap_llm=cheap_llm,
        model="cheap-model",
    )

    summary = await suggester.run()

    assert automations.replaced is not None
    assert [s.name for s in automations.replaced] == ["digest-a", "digest-b"]
    assert all(s.status == "active" for s in automations.replaced)
    assert all(s.id and s.created_at is not None for s in automations.replaced)
    assert summary == "suggestions=2; dropped=1"

    # cheap_llm was called with the SuggestionSet response_format and our model.
    assert cheap_llm.calls[0]["response_format"] is SuggestionSet
    assert cheap_llm.calls[0]["model"] == "cheap-model"
