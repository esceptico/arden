"""Shared pytest config + offline test doubles for the memory pipeline.

The pipeline components (admit/extract/reconcile/consolidate/retrieve, the
capture boundary judge, and the recall tool) talk to a CompletionClient and an
Embedder. Tests must never touch the network or a real model, and never open
~/.arden/memory.db — every store is a tmp_path / in-memory SQLite DB.

The fakes here cover the shared shapes; component tests that need scripted
structured outputs keep their own local stubs (admit/reconcile/consolidate),
since those assert exact-call semantics per CONTRACTS.md.
"""

import os
import tempfile

# Must precede every `arden` import: arden.logging attaches its rotating file
# handler at import time, so without this a test run appends thousands of lines
# to the developer's real ~/.arden/logs/arden.log. Only the log sink is moved —
# redirecting ARDEN_DIR itself also hides settings.json, and a number of tests
# silently depend on the provider keys there to resolve a model.
os.environ.setdefault("ARDEN_LOG_DIR", tempfile.mkdtemp(prefix="arden-test-logs-"))

import numpy as np
import pytest

from arden.agent.types.llm import (
    Choice,
    CompletionResponse,
    FinishReason,
    Message,
    Role,
)
from arden.agent.types.usage import Usage


@pytest.fixture(autouse=True)
def isolate_raw_tool_result_blobs(monkeypatch, tmp_path):
    import arden.core.raw_tool_results as raw_tool_results

    monkeypatch.setattr(raw_tool_results, "RAW_TOOL_RESULTS_BASE", tmp_path / "raw-tool-results")


def completion_response(content: str) -> CompletionResponse:
    """Wrap a JSON/string payload in the CompletionResponse shape the pipeline
    reads (response.choices[0].message.content)."""
    return CompletionResponse(
        choices=[
            Choice(
                message=Message(
                    role=Role.ASSISTANT,
                    content=content,
                    tool_calls=None,
                    reasoning_content=None,
                ),
                finish_reason=FinishReason.STOP,
            )
        ],
        usage=Usage(),
        model="fake",
    )


class FakeCompletionClient:
    """Offline CompletionClient. Returns a queued payload per call (FIFO) or a
    constant default; records every call so tests can assert the cost ceiling.

    Queue entries may be a str (returned as message content) or a pydantic
    model (serialized via model_dump_json). A constant `default` is reused once
    the queue drains; if neither is set, an empty string is returned."""

    def __init__(self, *, default=None, queue=None):
        self.default = default
        self._queue = list(queue or [])
        self.calls: list[dict] = []

    def enqueue(self, payload) -> None:
        self._queue.append(payload)

    @staticmethod
    def _content(payload) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload
        return payload.model_dump_json()

    async def completion(self, *, messages, model, response_format=None, **kwargs):
        self.calls.append({"messages": messages, "model": model, "response_format": response_format})
        payload = self._queue.pop(0) if self._queue else self.default
        return completion_response(self._content(payload))


class FakeEmbedder:
    """Deterministic offline embedder. Token-overlap pseudo-embeddings over a
    fixed vocab: monotone in lexical overlap so retrieval ordering is testable
    without a real model. Falls back to a hashed unit vector for OOV text."""

    def __init__(self, vocab=None, dim: int = 16):
        self.vocab = vocab
        self.dim = len(vocab) if vocab else dim

    def _vec(self, text: str) -> np.ndarray:
        if self.vocab is not None:
            toks = set(text.lower().split())
            v = np.array([1.0 if w in toks else 0.0 for w in self.vocab])
        else:
            v = np.zeros(self.dim)
            for tok in text.lower().split():
                v[hash(tok) % self.dim] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    async def embed(self, texts) -> np.ndarray:
        if not texts:
            return np.array([])
        return np.vstack([self._vec(t) for t in texts])

    async def embed_one(self, text: str) -> np.ndarray:
        return self._vec(text)


@pytest.fixture
def fake_llm():
    return FakeCompletionClient()


@pytest.fixture
def fake_embedder():
    return FakeEmbedder()
