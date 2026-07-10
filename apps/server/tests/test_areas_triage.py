"""Chat triage: classify a just-started chat into an existing home, propose a
new one, or stay silent — validated at the boundary so a hallucinated home or
a broken call can never reach the UI."""

import json

import pytest

from ntrp.areas.triage import triage_chat

CANDIDATES = [
    {"key": "o-1a", "title": "O-1A Visa"},
    {"key": "proj_123", "title": "Marathon Training"},
]


class _FakeLLM:
    def __init__(self, payload: dict | None = None, raises: bool = False):
        self._payload = payload
        self._raises = raises

    async def completion(self, *, messages, model, response_format):
        if self._raises:
            raise RuntimeError("model unavailable")

        class _Msg:
            content = json.dumps(self._payload)

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()


async def _run(payload=None, raises=False, candidates=CANDIDATES):
    return await triage_chat(
        transcript="user: my visa interview is next week",
        candidates=candidates,
        cheap_llm=_FakeLLM(payload, raises),
        model="cheap",
    )


@pytest.mark.asyncio
async def test_move_restamps_from_catalog_not_model_echo():
    # Model echoes a wrong title; we trust our catalog.
    d = await _run({
        "decision": "move",
        "target": {"key": "o-1a", "title": "WRONG"},
        "rationale": "About the visa.",
    })
    assert d.decision == "move"
    assert d.target.title == "O-1A Visa"


@pytest.mark.asyncio
async def test_move_to_unknown_home_drops_to_none():
    d = await _run({"decision": "move", "target": {"key": "ghost", "title": "Ghost"}})
    assert d.decision == "none"


@pytest.mark.asyncio
async def test_create_trims_title_and_empty_drops_to_none():
    ok = await _run({"decision": "create", "new_title": "  Tax Filing  ", "rationale": "New arc."})
    assert ok.decision == "create" and ok.new_title == "Tax Filing"
    empty = await _run({"decision": "create", "new_title": "   "})
    assert empty.decision == "none"


@pytest.mark.asyncio
async def test_none_passthrough_and_failure_is_silent():
    assert (await _run({"decision": "none", "rationale": "throwaway"})).decision == "none"
    assert (await _run(raises=True)).decision == "none"
