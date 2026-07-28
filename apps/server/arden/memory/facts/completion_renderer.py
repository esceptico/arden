"""Small completion-client adapter for fact synthesis prose."""

from arden.llm.base import CompletionClient
from arden.memory.facts.synthesis import SynthesisFact


class CompletionFactSynthesisRenderer:
    """Render only from pinned facts; the synthesis validator remains the authority."""

    def __init__(self, client: CompletionClient, model: str, *, reasoning_effort: str | None = None) -> None:
        self._client = client
        self._model = model
        self._reasoning_effort = reasoning_effort

    async def render(self, *, title: str, facts: tuple[SynthesisFact, ...]) -> str:
        evidence = "\n".join(f"- {fact.token}: {fact.text} [source: {fact.source_summary}]" for fact in facts)
        response = await self._client.completion(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Write a concise Markdown synthesis for one wiki page. "
                        "Use only the supplied facts. Every non-heading claim line must end with one or more "
                        "exact citations like `(fact:F001)` or `(fact:F001, fact:F002)`. "
                        "Never put fact tokens in headings, never invent tokens, and return Markdown only."
                    ),
                },
                {"role": "user", "content": f"Page title: {title}\n\nPinned facts:\n{evidence}"},
            ],
            reasoning_effort=self._reasoning_effort,
        )
        if not response.choices or response.choices[0].message.content is None:
            raise ValueError("fact synthesis completion returned no content")
        return response.choices[0].message.content
