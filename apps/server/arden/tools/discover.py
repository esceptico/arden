import importlib.util
import sys
from collections.abc import Mapping
from pathlib import Path

from arden.logging import get_logger
from arden.settings import ARDEN_DIR
from arden.tools.core.base import Tool

_logger = get_logger(__name__)

RESERVED_USER_TOOL_NAMES = frozenset(
    {
        "research_outline",
        "research_cover",
        "research_track_source",
        "research_curate",
        "research_verify_claim",
        "research_question",
        "research_track_search",
        "write_research_artifact",
        "append_research_artifact",
        "read_research_artifact",
        "list_research_artifacts",
    }
)


def discover_user_tools(
    tools_dir: Path | None = None,
    *,
    reserved_names: frozenset[str] = RESERVED_USER_TOOL_NAMES,
) -> dict[str, Tool]:
    if tools_dir is None:
        tools_dir = ARDEN_DIR / "tools"
    if not tools_dir.is_dir():
        return {}

    tools: dict[str, Tool] = {}

    for path in sorted(tools_dir.glob("*.py")):
        try:
            module_name = f"arden_user_tools.{path.stem}"
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            module_tools = module.__dict__.get("tools")
            if module_tools is None:
                continue
            if not isinstance(module_tools, Mapping):
                _logger.warning("User tool file %s exports non-mapping 'tools'", path.name)
                continue
            for name, candidate in module_tools.items():
                if not isinstance(name, str) or not isinstance(candidate, Tool):
                    _logger.warning("User tool %r from %s skipped — expected dict[str, Tool]", name, path.name)
                    continue
                if name in tools:
                    _logger.warning("User tool %r from %s skipped — duplicate user tool", name, path.name)
                    continue
                if name in reserved_names:
                    _logger.warning("User tool %r from %s skipped — reserved tool name", name, path.name)
                    continue
                tools[name] = candidate

        except Exception:
            _logger.warning("Failed to load user tool from %s", path.name, exc_info=True)

    return tools
