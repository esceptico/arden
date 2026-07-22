"""Area derivation from memory: candidate scan over unpromoted topic pages,
LLM classification (validated at the boundary), persistent dismissals."""

import json

import pytest

from arden.areas.suggester import (
    AreaSuggester,
    AreaSuggestionStore,
    candidate_pages,
)


def _vault(tmp_path, pages: dict[str, str]):
    topics = tmp_path / "memory" / "topics"
    topics.mkdir(parents=True)
    for slug, body in pages.items():
        (topics / f"{slug}.md").write_text(body)
    return tmp_path / "memory"


def test_candidate_pages_excludes_promoted_areas(tmp_path):
    vault = _vault(
        tmp_path,
        {
            "o-1a": "---\ntitle: O-1A\n---\n# O-1A\nvisa case",
            "letta": "---\ntitle: Letta\n---\n# Letta\nagent framework notes",
        },
    )
    cands = candidate_pages(vault, {"o-1a"})
    assert [c["key"] for c in cands] == ["letta"]
    assert "agent framework" in cands[0]["head"]


class _FakeLLM:
    def __init__(self, payload: dict):
        self._payload = payload

    async def completion(self, *, messages, model, response_format):
        class _Msg:
            content = json.dumps(self._payload)

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()


@pytest.mark.asyncio
async def test_suggester_validates_keys_and_respects_dismissals(tmp_path):
    vault = _vault(
        tmp_path,
        {
            "mats": "---\ntitle: MATS\n---\n# MATS\nfellowship application arc",
            "letta": "---\ntitle: Letta\n---\n# Letta\nframework reference",
        },
    )
    store = AreaSuggestionStore(tmp_path / "suggestions.json")
    llm = _FakeLLM(
        {
            "suggestions": [
                {"key": "mats", "rationale": "Active application with deadlines."},
                {"key": "nonexistent", "rationale": "hallucinated"},
            ]
        }
    )
    suggester = AreaSuggester(attached_page_slugs=set(), vault_dir=vault, store=store, cheap_llm=llm, model="cheap")

    await suggester.run()
    listed = store.list(exclude_keys=set())
    assert [s["key"] for s in listed] == ["mats"]  # hallucinated key dropped
    assert listed[0]["page_path"] == "topics/mats.md"

    store.dismiss("mats")
    await suggester.run()  # re-run must NOT resurrect a dismissed suggestion
    assert store.list(exclude_keys=set()) == []
