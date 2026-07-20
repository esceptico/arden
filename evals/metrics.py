from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentErgonomicsMetrics:
    tool_calls: int = 0
    wrong_tool_calls: int = 0
    errors: int = 0
    retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    truncations: int = 0
    offloads: int = 0
    recoveries: int = 0
    latency_ms: int = 0

    def to_dict(self) -> dict[str, int]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}


def compute_agent_ergonomics_metrics(
    events: list[dict[str, Any]],
    *,
    expected_tools: set[str] | frozenset[str] = frozenset(),
    latency_ms: int = 0,
) -> AgentErgonomicsMetrics:
    calls: list[str] = []
    failed_tools: set[str] = set()
    errors = input_tokens = output_tokens = truncations = offloads = recoveries = 0
    for event in events:
        event_type = str(event.get("type") or "")
        tool_name = str(event.get("tool_call_name") or event.get("tool_name") or event.get("name") or "")
        if event_type in {"TOOL_CALL_START", "tool_call_start", "tool_started"} and tool_name:
            calls.append(tool_name)
        outcome = event.get("outcome") if isinstance(event.get("outcome"), dict) else {}
        status = str(outcome.get("status") or event.get("status") or "")
        is_error = bool(event.get("is_error")) or status in {"failed", "denied", "uncertain"}
        if event_type == "RUN_ERROR":
            is_error = True
        if is_error:
            errors += 1
            if tool_name:
                failed_tools.add(tool_name)
        elif tool_name and tool_name in failed_tools and event_type in {"TOOL_CALL_RESULT", "tool_call_result", "tool_completed"}:
            recoveries += 1
            failed_tools.discard(tool_name)

        usage = event.get("usage") if isinstance(event.get("usage"), dict) else event
        input_tokens += int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if data.get("truncated"):
            truncations += 1
        if event.get("raw_ref") or data.get("raw_ref"):
            offloads += 1

    wrong = sum(1 for name in calls if name not in expected_tools and name != "load_tools")
    retries = len(calls) - len(set(calls))
    return AgentErgonomicsMetrics(
        tool_calls=len(calls),
        wrong_tool_calls=wrong,
        errors=errors,
        retries=retries,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        truncations=truncations,
        offloads=offloads,
        recoveries=recoveries,
        latency_ms=max(0, latency_ms),
    )


@dataclass
class RetrievalMetrics:
    recall_all: float  # 1.0 if ALL gold sessions covered, else 0.0
    session_recall: float  # fraction of gold sessions covered
    fact_precision: float  # fraction of retrieved facts from gold sessions
    sessions_covered: int
    gold_sessions: int
    total_facts: int
    facts_retrieved: int
    facts_from_gold: int
    selectivity: float  # facts_retrieved / total_facts


def compute_retrieval_metrics(
    retrieved_session_ids: list[str],
    gold_session_ids: list[str],
    total_facts: int,
    facts_retrieved: int,
) -> RetrievalMetrics:
    gold = set(gold_session_ids)

    if not gold:
        return RetrievalMetrics(
            recall_all=1.0,
            session_recall=1.0,
            fact_precision=1.0 if not retrieved_session_ids else 0.0,
            sessions_covered=0,
            gold_sessions=0,
            total_facts=total_facts,
            facts_retrieved=facts_retrieved,
            facts_from_gold=0,
            selectivity=facts_retrieved / total_facts if total_facts else 0,
        )

    retrieved_set = set(s for s in retrieved_session_ids if s)
    covered = retrieved_set & gold
    session_recall = len(covered) / len(gold)
    recall_all = 1.0 if covered == gold else 0.0

    # Fact-level: how many individual retrieved facts come from gold sessions
    facts_from_gold = sum(1 for s in retrieved_session_ids if s in gold)
    fact_precision = facts_from_gold / facts_retrieved if facts_retrieved else 0.0

    return RetrievalMetrics(
        recall_all=recall_all,
        session_recall=session_recall,
        fact_precision=fact_precision,
        sessions_covered=len(covered),
        gold_sessions=len(gold),
        total_facts=total_facts,
        facts_retrieved=facts_retrieved,
        facts_from_gold=facts_from_gold,
        selectivity=facts_retrieved / total_facts if total_facts else 0,
    )
