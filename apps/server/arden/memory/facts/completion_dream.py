"""Completion adapter for the provisional Dream projection."""

from datetime import date

from pydantic import BaseModel, ConfigDict

from arden.llm.base import CompletionClient

from .dream import DreamEvidence, DreamInsight


class _InsightPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    claim: str
    fact_tokens: list[str]


class _DreamPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    insights: list[_InsightPayload]


class CompletionFactDreamRenderer:
    def __init__(self, client: CompletionClient, model: str, *, reasoning_effort: str | None = None) -> None:
        self._client = client
        self._model = model
        self._reasoning_effort = reasoning_effort

    async def render(
        self,
        *,
        month: date,
        evidence: tuple[DreamEvidence, ...],
    ) -> tuple[DreamInsight, ...]:
        catalog = "\n".join(
            f"- {item.token} [{', '.join(item.subjects)}] {item.text} [source: {item.source_summary}]"
            for item in evidence
        )
        response = await self._client.completion(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Find up to five useful, non-obvious connections across the user's canonical facts. "
                        "Each connection is provisional: use only supplied evidence, cite at least two distinct "
                        "fact tokens whose explicit subjects differ, and do not restate a single fact. "
                        "Return exactly one JSON object shaped as "
                        '{"insights":[{"claim":"one concise claim, at most 600 characters",'
                        '"fact_tokens":["F001","F002"]}]}. Return an empty insights list when evidence is weak.'
                    ),
                },
                {
                    "role": "user",
                    "content": f"Month: {month:%Y-%m}\n\nPinned canonical facts:\n{catalog}",
                },
            ],
            reasoning_effort=self._reasoning_effort,
        )
        if not response.choices or response.choices[0].message.content is None:
            return ()
        payload = _DreamPayload.model_validate_json(response.choices[0].message.content)
        return tuple(DreamInsight(item.claim, tuple(item.fact_tokens)) for item in payload.insights)
