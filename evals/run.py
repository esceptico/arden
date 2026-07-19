import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable

from evals.report import EventEvalResult
from evals.runtime_case import RuntimeCase

EventCase = Callable[[RuntimeCase], Awaitable[None]]


async def run_event_case(name: str, case: EventCase, runtime_case: RuntimeCase) -> EventEvalResult:
    try:
        await case(runtime_case)
    except AssertionError as exc:
        return EventEvalResult(name=name, passed=False, events=runtime_case.events, error=str(exc))
    except Exception as exc:
        return EventEvalResult(name=name, passed=False, events=runtime_case.events, error=f"{type(exc).__name__}: {exc}")
    return EventEvalResult(name=name, passed=True, events=runtime_case.events)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run ntrp eval suites.")
    parser.add_argument(
        "--suite",
        choices=("harness-reliability",),
        default="harness-reliability",
        help="Hermetic eval suite to run (default: harness-reliability).",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable results.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    from evals.scenarios.harness_reliability import run_harness_reliability_journeys

    results = asyncio.run(run_harness_reliability_journeys())
    if args.json:
        print(json.dumps([result.to_dict() for result in results], indent=2))
    else:
        for result in results:
            suffix = f": {result.detail}" if result.detail else ""
            print(f"{'PASS' if result.passed else 'FAIL'} {result.name}{suffix}")
    return 0 if all(result.passed for result in results) else 1
