import argparse
import asyncio
import importlib.util
import json
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import httpx

from evals.assertions import ErgonomicsThresholds, assert_ergonomics_thresholds
from evals.client import parse_sse_events
from evals.metrics import compute_agent_ergonomics_metrics
from evals.report import EventEvalResult
from evals.runtime_case import RuntimeCase

EventCase = Callable[[RuntimeCase], Awaitable[None]]
CASES_DIR = Path(__file__).with_name("cases")


@dataclass(frozen=True)
class DiscoveredCase:
    name: str
    path: Path
    run: EventCase
    expected_tools: frozenset[str]
    thresholds: ErgonomicsThresholds
    description: str = ""


def _load_case_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"arden_eval_{path.stem.replace('.', '_')}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load eval case: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def discover_event_cases(cases_dir: Path = CASES_DIR) -> list[DiscoveredCase]:
    discovered: list[DiscoveredCase] = []
    for path in sorted(cases_dir.glob("*.eval.py")):
        module = _load_case_module(path)
        metadata = getattr(module, "CASE", {})
        functions = [
            (name, value)
            for name, value in vars(module).items()
            if name.startswith("test_") and asyncio.iscoroutinefunction(value)
        ]
        if len(functions) != 1:
            raise ValueError(f"{path.name} must define exactly one async test_* function")
        name, function = functions[0]
        threshold_values = dict(metadata.get("thresholds") or {})
        discovered.append(
            DiscoveredCase(
                name=name.removeprefix("test_"),
                path=path,
                run=function,
                expected_tools=frozenset(metadata.get("expected_tools") or ()),
                thresholds=ErgonomicsThresholds(**threshold_values),
                description=str(metadata.get("description") or ""),
            )
        )
    return discovered


async def run_event_case(name: str, case: EventCase, runtime_case: RuntimeCase) -> EventEvalResult:
    try:
        await case(runtime_case)
    except AssertionError as exc:
        return EventEvalResult(name=name, passed=False, events=runtime_case.events, error=str(exc))
    except Exception as exc:
        return EventEvalResult(name=name, passed=False, events=runtime_case.events, error=f"{type(exc).__name__}: {exc}")
    return EventEvalResult(name=name, passed=True, events=runtime_case.events)


class ProviderRuntimeClient:
    def __init__(self, base_url: str, *, api_key: str | None = None, timeout: float = 120.0):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    async def close(self) -> None:
        await self.client.aclose()

    async def create_session(self, name: str) -> str:
        response = await self.client.post("/sessions", json={"name": name})
        response.raise_for_status()
        return str(response.json()["session_id"])

    async def send(self, session_id: str, prompt: str) -> list[dict]:
        response = await self.client.post(
            "/chat/message",
            json={"message": prompt, "session_id": session_id, "skip_approvals": False},
        )
        response.raise_for_status()
        async with self.client.stream("GET", f"/chat/events/{session_id}", params={"stream": "false", "after_seq": 0}) as stream:
            stream.raise_for_status()
            raw = ""
            async for chunk in stream.aiter_text():
                raw += chunk
            return parse_sse_events(raw)


async def run_provider_cases(cases: list[DiscoveredCase], client: ProviderRuntimeClient) -> list[EventEvalResult]:
    results: list[EventEvalResult] = []
    for case in cases:
        session_id = await client.create_session(f"eval-{case.name}")

        async def send(prompt: str, *, _session_id: str = session_id) -> list[dict]:
            return await client.send(_session_id, prompt)

        runtime_case = RuntimeCase(send)
        started = time.perf_counter()
        result = await run_event_case(case.name, case.run, runtime_case)
        metrics = compute_agent_ergonomics_metrics(
            runtime_case.events,
            expected_tools=case.expected_tools,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        if result.passed:
            try:
                assert_ergonomics_thresholds(metrics, case.thresholds)
            except AssertionError as exc:
                result.passed = False
                result.error = str(exc)
        result.metrics = metrics.to_dict()
        results.append(result)
    return results


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run arden eval suites.")
    parser.add_argument(
        "--suite",
        choices=("harness-reliability", "agent-ergonomics"),
        default="harness-reliability",
        help="Hermetic eval suite to run (default: harness-reliability).",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable results.")
    parser.add_argument("--dry-run", action="store_true", help="Discover and validate cases without calling a provider.")
    parser.add_argument(
        "--base-url",
        default=os.getenv("ARDEN_EVAL_BASE_URL"),
        help="Running Arden server for agent-ergonomics (or ARDEN_EVAL_BASE_URL).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.suite == "harness-reliability":
        from evals.scenarios.harness_reliability import run_harness_reliability_journeys

        results = asyncio.run(run_harness_reliability_journeys())
    else:
        cases = discover_event_cases()
        if args.dry_run:
            payload = [
                {
                    "name": case.name,
                    "path": str(case.path.relative_to(Path(__file__).parent.parent)),
                    "description": case.description,
                    "expected_tools": sorted(case.expected_tools),
                    "thresholds": case.thresholds.__dict__,
                }
                for case in cases
            ]
            print(json.dumps(payload, indent=2) if args.json else "\n".join(f"DISCOVERED {row['name']}" for row in payload))
            return 0
        if not args.base_url:
            _parser().error("agent-ergonomics requires --base-url or ARDEN_EVAL_BASE_URL (or use --dry-run)")

        async def run_live() -> list[EventEvalResult]:
            client = ProviderRuntimeClient(args.base_url, api_key=os.getenv("ARDEN_EVAL_API_KEY"))
            try:
                return await run_provider_cases(cases, client)
            finally:
                await client.close()

        results = asyncio.run(run_live())
    if args.json:
        print(json.dumps([result.to_dict() for result in results], indent=2))
    else:
        for result in results:
            suffix = f": {result.detail}" if result.detail else ""
            print(f"{'PASS' if result.passed else 'FAIL'} {result.name}{suffix}")
    return 0 if all(result.passed for result in results) else 1
