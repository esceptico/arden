"""Nightly memory automation handlers: reconcile the record pool, then publish
artifacts in a separate builtin so each phase can run and report independently."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from arden.server.runtime.automation import AutomationRuntime

pytestmark = pytest.mark.asyncio


class _Rep:
    def __init__(self, **kw):
        self.merged = kw.get("merged", 0)
        self.superseded = kw.get("superseded", 0)
        self.dropped = kw.get("dropped", 0)
        self.retyped = kw.get("retyped", 0)
        self.relabeled = kw.get("relabeled", 0)
        self.reclassified = kw.get("reclassified", 0)
        self.pruned = kw.get("pruned", 0)
        self.changed_memory = kw.get(
            "changed_memory",
            any(
                [
                    self.merged,
                    self.superseded,
                    self.dropped,
                    self.retyped,
                    self.relabeled,
                    self.reclassified,
                    self.pruned,
                ]
            ),
        )

    @property
    def summary_counts(self):
        return {
            "merged": self.merged,
            "superseded": self.superseded,
            "dropped": self.dropped,
            "retyped": self.retyped,
            "relabeled": self.relabeled,
            "reclassified": self.reclassified,
            "pruned": self.pruned,
        }


def _runtime(consolidate, knowledge) -> AutomationRuntime:
    # Bypass the heavy __init__; the handler closure only reads these two getters.
    rt = object.__new__(AutomationRuntime)
    rt.get_consolidate = lambda: consolidate
    rt.get_knowledge = lambda: knowledge
    return rt


async def test_consolidate_handler_reconciles_without_refreshing_artifacts():
    consolidate = AsyncMock()
    consolidate.run_once = AsyncMock(
        side_effect=[_Rep(merged=2, dropped=1, reclassified=3, pruned=4), _Rep(changed_memory=False)]
    )
    knowledge = AsyncMock()

    handler = _runtime(consolidate, knowledge)._build_memory_consolidate_handler()
    result = await handler(None)

    knowledge.rebuild_artifacts.assert_not_awaited()
    assert "merged 2" in result and "dropped 1" in result
    assert "reclassified 3" in result and "pruned 4" in result
    assert "refreshed" not in result


async def test_consolidate_handler_unavailable_without_memory_model():
    handler = _runtime(None, None)._build_memory_consolidate_handler()
    assert "unavailable" in await handler(None)


async def test_consolidate_handler_does_not_raise_when_knowledge_is_missing():
    consolidate = AsyncMock()
    consolidate.run_once = AsyncMock(side_effect=[_Rep(merged=1), _Rep(changed_memory=False)])

    handler = _runtime(consolidate, None)._build_memory_consolidate_handler()
    result = await handler(None)

    assert "merged 1" in result
    assert "artifact" not in result


async def test_consolidate_handler_continues_when_only_new_report_fields_changed():
    consolidate = AsyncMock()
    consolidate.run_once = AsyncMock(
        side_effect=[
            _Rep(reclassified=1, changed_memory=True),
            _Rep(pruned=2, changed_memory=True),
            _Rep(changed_memory=False),
        ]
    )
    handler = _runtime(consolidate, AsyncMock())._build_memory_consolidate_handler()
    result = await handler(None)

    assert consolidate.run_once.await_count == 3
    assert "reclassified 1" in result and "pruned 2" in result
