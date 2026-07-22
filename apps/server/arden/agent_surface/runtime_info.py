from importlib.metadata import version
from pathlib import Path

from arden.agent_surface.discovery import discover_agent_surface
from arden.agent_surface.models import RuntimeInfo
from arden.events.sse import EventType
from arden.tools.deferred import DEFERRED_GROUP_LABELS, DEFERRED_GROUP_ORDER


def build_runtime_info(root: Path | str = ".", runtime=None) -> RuntimeInfo:
    manifest = discover_agent_surface(root)
    data = manifest.model_dump(mode="json")
    data["version"] = version("arden")
    if runtime is not None:
        executor = getattr(runtime, "executor", None)
        if executor is not None:
            data["tools"] = executor.get_tool_metadata()
        data["deferred_tool_groups"] = [
            {"id": group, "model_name": DEFERRED_GROUP_LABELS.get(group, group), "path": "", "source": "runtime"}
            for group in DEFERRED_GROUP_ORDER
        ]
        data["event_types"] = [event.value for event in EventType]
    return RuntimeInfo(**data)
