import os
import subprocess
import sys
from pathlib import Path

import pytest
from evals.scenarios.harness_reliability import run_harness_reliability_journeys


def test_eval_module_has_working_cli_help():
    root = Path(__file__).parents[3]
    env = {**os.environ, "PYTHONPATH": f"{root / 'apps/server'}:{root}"}

    result = subprocess.run(
        [sys.executable, "-m", "evals", "--help"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "harness-reliability" in result.stdout


@pytest.mark.asyncio
async def test_harness_reliability_journeys_are_hermetic_and_green():
    results = await run_harness_reliability_journeys()

    assert [result.name for result in results] == [
        "approved_mutation",
        "malformed_repair",
        "scope_denial",
        "restart_resume",
        "background_exactly_once",
        "failed_postcondition",
        "partial_completion",
    ]
    assert all(result.passed for result in results), [result.to_dict() for result in results]
