import ast
import json
import textwrap
from typing import Any

from pydantic import BaseModel, Field

from arden.orchestra.engine import Orchestra

_SCRIPT_FILENAME = "<workflow-script>"
# The script body is wrapped as the body of `async def __workflow__():`, which
# occupies source line 1. Shift compiled line numbers back by this so the errors
# the model sees point at the lines it actually wrote.
_WRAPPER_OFFSET = 1


def _normalize(script: str) -> str:
    return script.strip() or "return None"


async def run_script(orchestra: Orchestra, script: str, args: dict) -> Any:
    """Execute a trusted, application-shipped orchestration preset.

    The script body runs as the body of an async function with the combinators in
    scope (no imports needed): `agent`, `parallel`, `pipeline`, `phase`, `log`,
    plus `args`, `json`, and pydantic `BaseModel`/`Field` for optional inline
    schemas. It uses `await` and `return`s the result.

    This is not a sandbox and must never receive model-authored or user-saved
    source. The workflow tool enforces that boundary before calling this helper.
    """
    source = f"async def __workflow__():\n{textwrap.indent(_normalize(script), '    ')}\n"
    try:
        tree = ast.parse(source, _SCRIPT_FILENAME, "exec")
    except SyntaxError as exc:
        if exc.lineno is not None:
            exc.lineno = max(1, exc.lineno - _WRAPPER_OFFSET)
        raise
    ast.increment_lineno(tree, -_WRAPPER_OFFSET)
    tree.body[0].lineno = 1  # synthetic wrapper frame; keep a valid line number
    namespace: dict[str, Any] = {
        "o": orchestra,
        "args": args,
        "agent": orchestra.agent,
        "parallel": orchestra.parallel,
        "pipeline": orchestra.pipeline,
        "phase": orchestra.phase,
        "log": orchestra.log,
        "budget": orchestra.budget_view,
        "json": json,
        "BaseModel": BaseModel,
        "Field": Field,
    }
    exec(compile(tree, _SCRIPT_FILENAME, "exec"), namespace)
    return await namespace["__workflow__"]()
