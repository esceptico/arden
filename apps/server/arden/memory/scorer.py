"""Importance scoring (1-10 poignancy) + recency/salience for memory ranking.

Generative-Agents poignancy prompt when an LLM is available; cheap heuristic
table otherwise. Never called on the hot path (curator sweep + migrate backfill
only). salience() is the only piece search() calls, and it is pure/local.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from arden.memory.models import Kind
from arden.observability import observed_trace

_HEURISTIC: dict[str, int] = {
    Kind.DIRECTIVE: 7,
    Kind.FACT: 5,
    Kind.SOURCE: 3,
    Kind.CHANGELOG: 2,
}
_PINNED_BUMP = 1  # pinned -> +1, capped at 8 (9-10 reserved for the LLM)

_POIGNANCY_SYSTEM = (
    "On the scale of 1 to 10, where 1 is purely mundane (e.g. 'brushed teeth') "
    "and 10 is extremely poignant (e.g. a life-changing decision or revelation), "
    "rate the likely long-term importance of the following memory for a personal "
    "assistant that must recall it in future conversations. "
    "Output ONLY the integer, nothing else."
)


def heuristic_score(kind: str, pinned: bool) -> int:
    base = _HEURISTIC.get(kind, 5)
    if pinned:
        base = min(8, base + _PINNED_BUMP)
    return base


@observed_trace("memory.score", tags="memory")
async def score_importance(
    text: str, kind: str, pinned: bool, llm, model: str, reasoning_effort: str | None = None
) -> int:
    if llm is None or not model:
        return heuristic_score(kind, pinned)
    try:
        resp = await llm.completion(
            messages=[
                {"role": "system", "content": _POIGNANCY_SYSTEM},
                {"role": "user", "content": text},
            ],
            model=model,
            reasoning_effort=reasoning_effort,
        )
        raw = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        return max(1, min(10, int(raw.split()[0])))
    except Exception:
        return heuristic_score(kind, pinned)


def recency_decay(date_str: str, *, floor: float = 0.3, half_life_days: float = 180.0) -> float:
    if not date_str:
        return floor
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return floor
    days_old = max(0.0, (datetime.now(UTC) - d).total_seconds() / 86400.0)
    return max(floor, math.exp(-days_old * math.log(2) / half_life_days))


def salience(imp: int | None, date_str: str) -> float:
    """Composite multiplier for search ranking: (imp/10) * recency_decay.
    imp None -> 5 (neutral). Floor 0.3 keeps old high-imp lines findable.
    Range [0.03, 1.0]."""
    imp_eff = imp if imp is not None else 5
    return (imp_eff / 10.0) * recency_decay(date_str)


if __name__ == "__main__":
    from datetime import date, timedelta

    assert heuristic_score(Kind.DIRECTIVE, False) == 7
    assert heuristic_score(Kind.FACT, True) == 6
    assert heuristic_score(Kind.SOURCE, False) == 3
    today = date.today().isoformat()
    assert salience(10, today) > 0.99  # fresh line ~ full salience (decay <1d is negligible)
    assert abs(salience(None, today) - salience(5, today)) < 1e-9
    old = (date.today() - timedelta(days=2000)).isoformat()
    assert salience(10, old) >= 0.3 * 0.99  # floor holds
    print("scorer.py self-check OK")
