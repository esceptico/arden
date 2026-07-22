from fastapi import Depends, HTTPException, Request

from arden.server.bus import BusRegistry
from arden.server.runtime import Runtime, get_runtime
from arden.server.runtime.automation import AutomationRuntime
from arden.server.runtime.knowledge import KnowledgeRuntime
from arden.server.state import RunRegistry
from arden.tools.executor import ToolExecutor


def require_session_service(runtime: Runtime = Depends(get_runtime)):
    if not runtime.session_service:
        raise HTTPException(status_code=503, detail="Session service not available")
    return runtime.session_service


def require_config_service(runtime: Runtime = Depends(get_runtime)):
    if not runtime.config_service:
        raise HTTPException(status_code=503, detail="Config service not available")
    return runtime.config_service


def require_automation_service(runtime: Runtime = Depends(get_runtime)):
    if not runtime.automation_service:
        raise HTTPException(status_code=503, detail="Automations not available")
    return runtime.automation_service


def require_notifier_service(runtime: Runtime = Depends(get_runtime)):
    if not runtime.notifier_service:
        raise HTTPException(status_code=503, detail="Notifier service not available")
    return runtime.notifier_service


def require_skill_service(runtime: Runtime = Depends(get_runtime)):
    if not runtime.skill_service:
        raise HTTPException(status_code=503, detail="Skill service not available")
    return runtime.skill_service


def require_run_registry(runtime: Runtime = Depends(get_runtime)) -> RunRegistry:
    return runtime.run_registry


def get_bus_registry(request: Request) -> BusRegistry:
    return request.app.state.bus_registry


def require_knowledge_runtime(runtime: Runtime = Depends(get_runtime)) -> KnowledgeRuntime:
    if not runtime.knowledge:
        raise HTTPException(status_code=503, detail="Knowledge runtime not available")
    return runtime.knowledge


def require_automation_runtime(runtime: Runtime = Depends(get_runtime)) -> AutomationRuntime:
    if not runtime.automation:
        raise HTTPException(status_code=503, detail="Automation runtime not available")
    return runtime.automation


def require_tool_executor(runtime: Runtime = Depends(get_runtime)) -> ToolExecutor:
    if not runtime.executor:
        raise HTTPException(status_code=503, detail="Tool executor not available")
    return runtime.executor
