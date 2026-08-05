"""Chat triage uses the explicit auxiliary model and validates its result."""

import json

import pytest

from arden.areas.triage import TriageTarget, triage_chat
from arden.server.routers.session import _triage_candidates, _triage_response

CANDIDATES = [
    TriageTarget(area_ref="o-1a-visa~abc123", title="O-1A Visa"),
    TriageTarget(area_ref="marathon-training~def456", title="Marathon Training"),
]


class _FakeLLM:
    def __init__(self, payload: dict | None = None, raises: bool = False):
        self._payload = payload
        self._raises = raises
        self.messages = None

    async def completion(self, *, messages, model, reasoning_effort, response_format):
        self.messages = messages
        if self._raises:
            raise RuntimeError("model unavailable")

        class _Msg:
            content = json.dumps(self._payload)

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()


async def _run(payload=None, raises=False, candidates=CANDIDATES, client=None):
    return await triage_chat(
        transcript="user: my visa interview is next week",
        candidates=candidates,
        client=client or _FakeLLM(payload, raises),
        model="auxiliary-model",
        reasoning_effort="low",
    )


@pytest.mark.asyncio
async def test_move_restamps_from_catalog_not_model_echo():
    # Model echoes a wrong title; we trust our catalog.
    d = await _run(
        {
            "decision": "move",
            "target": {"area_ref": "o-1a-visa~abc123", "title": "WRONG"},
            "rationale": "About the visa.",
        }
    )
    assert d.decision == "move"
    assert d.target is not None
    assert d.target.area_ref == "o-1a-visa~abc123"
    assert d.target.title == "O-1A Visa"


@pytest.mark.asyncio
async def test_move_to_unknown_home_fails_explicitly():
    with pytest.raises(ValueError, match="unknown home"):
        await _run(
            {"decision": "move", "target": {"area_ref": "ghost~abc123", "title": "Ghost"}}
        )


@pytest.mark.asyncio
async def test_model_request_contains_public_area_refs_not_internal_ids():
    internal_area_id = "area-internal-01"

    class _CandidateService:
        async def list_areas(self):
            return [
                {
                    "area_id": internal_area_id,
                    "area_ref": "o-1a-visa~abc123",
                    "name": "O-1A Visa",
                }
            ]

    candidates = await _triage_candidates(_CandidateService())
    client = _FakeLLM(
        {
            "decision": "move",
            "target": {"area_ref": "o-1a-visa~abc123", "title": "O-1A Visa"},
        }
    )

    await _run(candidates=candidates, client=client)

    request = json.loads(client.messages[1]["content"])
    assert request["homes"] == [{"area_ref": "o-1a-visa~abc123", "title": "O-1A Visa"}]
    assert internal_area_id not in client.messages[1]["content"]


@pytest.mark.asyncio
async def test_api_boundary_resolves_public_ref_to_internal_area_id():
    class _BoundaryService:
        requested_ref = None

        async def get_area_by_ref(self, area_ref):
            self.requested_ref = area_ref
            return {
                "area_id": "area-internal-01",
                "area_ref": area_ref,
                "name": "O-1A Visa",
            }

    service = _BoundaryService()
    decision = await _run(
        {
            "decision": "move",
            "target": {"area_ref": "o-1a-visa~abc123", "title": "O-1A Visa"},
        }
    )

    response = await _triage_response(service, decision)

    assert service.requested_ref == "o-1a-visa~abc123"
    assert response.model_dump() == {
        "decision": "move",
        "target": {"key": "area-internal-01", "title": "O-1A Visa"},
        "new_title": None,
        "rationale": "",
    }


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
