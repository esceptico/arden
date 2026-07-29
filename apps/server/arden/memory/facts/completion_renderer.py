"""Small completion-client adapter for fact synthesis prose."""

from arden.core.prompts import UNTRUSTED_DATA_RULE
from arden.llm.base import CompletionClient
from arden.memory.facts.synthesis import NO_ADDITIONAL_FACTS, SynthesisFact

_SYNTHESIS_SYSTEM_PROMPT = (
    "Write a concise Markdown synthesis for one wiki page. "
    "Use only the supplied facts. Every non-heading claim line must end with one or more "
    "exact citations like `(fact:F001)` or `(fact:F001, fact:F002)`. "
    "The existing authored body is context only: do not repeat, paraphrase, or cite claims already present there. "
    f"If every supplied fact is already present in the authored body, return exactly `{NO_ADDITIONAL_FACTS}`. "
    f"{UNTRUSTED_DATA_RULE} "
    "Never put fact tokens in headings, never invent tokens, and return Markdown only."
)


class CompletionFactSynthesisRenderer:
    """Render only from pinned facts; the synthesis validator remains the authority."""

    def __init__(self, client: CompletionClient, model: str, *, reasoning_effort: str | None = None) -> None:
        self._client = client
        self._model = model
        self._reasoning_effort = reasoning_effort

    async def render(self, *, title: str, facts: tuple[SynthesisFact, ...], existing_body: str) -> str:
        evidence = "\n".join(f"- {fact.token}: {fact.text} [source: {fact.source_summary}]" for fact in facts)
        response = await self._client.completion(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": _SYNTHESIS_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": (
                        f"Page title: {title}\n\n"
                        f"Existing authored body:\n{existing_body or '(none)'}\n\n"
                        f"Pinned facts:\n{evidence}"
                    ),
                },
            ],
            reasoning_effort=self._reasoning_effort,
        )
        if not response.choices or response.choices[0].message.content is None:
            raise ValueError("fact synthesis completion returned no content")
        return response.choices[0].message.content
