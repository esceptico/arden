from pathlib import Path

from fastapi import APIRouter, Request

from arden.agent_surface.models import RuntimeInfo
from arden.agent_surface.runtime_info import build_runtime_info

router = APIRouter(tags=["runtime"])


@router.get("/runtime/info", response_model=RuntimeInfo)
async def runtime_info(request: Request):
    runtime = getattr(request.app.state, "runtime", None)
    return build_runtime_info(Path.cwd(), runtime=runtime)
