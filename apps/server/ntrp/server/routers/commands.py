from hashlib import sha256

from fastapi import APIRouter, Depends, HTTPException

from ntrp.commands.models import CommandOutcome, CommandRunRequest, CommandRunResponse
from ntrp.commands.prompt import command_context
from ntrp.server.bus import BusRegistry
from ntrp.server.deps import get_bus_registry
from ntrp.server.runtime import Runtime, get_runtime
from ntrp.services.chat import ChatIdempotencyConflict, submit_chat_message
from ntrp.tools.deferred import tool_schema_names

router = APIRouter(prefix="/command", tags=["command"])


def _session_id(client_id: str) -> str:
    digest = sha256(client_id.encode()).hexdigest()[:24]
    return f"command_{digest}"


def command_tool_scope(runtime: Runtime) -> tuple[str, ...]:
    executor = runtime.executor
    if executor is None:
        raise RuntimeError("Tool executor is not initialized")
    return tuple(sorted(tool_schema_names(executor.get_tools(command_eligible=True))))


@router.post("/runs", response_model=CommandRunResponse)
async def start_command_run(
    request: CommandRunRequest,
    runtime: Runtime = Depends(get_runtime),
    buses: BusRegistry = Depends(get_bus_registry),
) -> CommandRunResponse:
    session_service = runtime.session_service
    if session_service is None:
        raise HTTPException(status_code=503, detail="Session service is not initialized")

    session_id = _session_id(request.client_id)
    if await session_service.load(session_id) is None:
        state = session_service.create(
            session_id=session_id,
            session_type="agent",
            agent_type="command_sidecar",
            agent_status="running",
        )
        await session_service.save(state, [])

    try:
        result = await submit_chat_message(
            runtime.run_registry,
            lambda: runtime.build_chat_deps(),
            buses,
            message=request.query,
            session_id=session_id,
            skip_approvals=False,
            context=command_context(request.current_destination),
            client_id=request.client_id,
            session_service=session_service,
            tool_scope=command_tool_scope(runtime),
            output_schema=CommandOutcome,
        )
    except ChatIdempotencyConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": exc.message, "client_id": exc.client_id},
        ) from exc

    return CommandRunResponse.model_validate(result)
