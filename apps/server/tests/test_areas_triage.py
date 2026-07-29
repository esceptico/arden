"""Chat triage uses the explicit auxiliary model and validates its result."""

import json

import pytest

from arden.areas.triage import triage_chat

CANDIDATES = [
    {"key": "o-1a", "title": "O-1A Visa"},
    {"key": "proj_123", "title": "Marathon Training"},
]


class _FakeLLM:
    def __init__(self, payload: dict | None = None, raises: bool = False):
        self._payload = payload
        self._raises = raises

    async def completion(self, *, messages, model, reasoning_effort, response_format):
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
        client=_FakeLLM(payload, raises),
        model="auxiliary-model",
        reasoning_effort="low",
    )


@pytest.mark.asyncio
async def test_move_restamps_from_catalog_not_model_echo():
    # Model echoes a wrong title; we trust our catalog.
    d = await _run(
        {
            "decision": "move",
            "target": {"key": "o-1a", "title": "WRONG"},
            "rationale": "About the visa.",
        }
    )
    assert d.decision == "move"
    assert d.target.title == "O-1A Visa"


@pytest.mark.asyncio
async def test_move_to_unknown_home_fails_explicitly():
    with pytest.raises(ValueError, match="unknown home"):
        await _run({"decision": "move", "target": {"key": "ghost", "title": "Ghost"}})


@pytest.mark.asyncio
async def test_create_trims_title_and_rejects_empty_title():
    ok = await _run({"decision": "create", "new_title": "  Tax Filing  ", "rationale": "New arc."})
    assert ok.decision == "create" and ok.new_title == "Tax Filing"
    with pytest.raises(ValueError, match="requires a title"):
        await _run({"decision": "create", "new_title": "   "})


@pytest.mark.asyncio
async def test_none_passthrough_and_model_failure_surfaces():
    assert (await _run({"decision": "none", "rationale": "throwaway"})).decision == "none"
    with pytest.raises(RuntimeError, match="model unavailable"):
        await _run(raises=True)
