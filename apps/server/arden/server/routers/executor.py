import asyncio
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel

from arden.constants import (
    DEVICE_SKILL_MAX_BODY_BYTES,
    DEVICE_SKILL_MAX_COUNT,
    DEVICE_SKILL_MAX_DESCRIPTION_CHARS,
    EXECUTOR_STREAM_KEEPALIVE_SECONDS,
)
from arden.execution.devices import ExecutorDevice
from arden.execution.gateway import ExecutorGateway, StaleLeaseError
from arden.execution.models import InvocationConflictError, InvocationStatus
from arden.server.middleware import SSEStreamingResponse
from arden.server.runtime import Runtime, get_runtime
from arden.skills.service import _SKILL_NAME_RE

router = APIRouter(prefix="/executor", tags=["executor"])


def require_executor_gateway(runtime: Runtime = Depends(get_runtime)) -> ExecutorGateway:
    if not runtime.executor_gateway:
        raise HTTPException(status_code=503, detail="Executor gateway not available")
    return runtime.executor_gateway


async def require_executor_device(
    authorization: str = Header(default=""),
    gateway: ExecutorGateway = Depends(require_executor_gateway),
) -> ExecutorDevice:
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing executor token")
    device = await gateway.devices.authenticate(token)
    if device is None:
        raise HTTPException(status_code=401, detail="Unknown or revoked executor token")
    return device


class EnrollRequest(BaseModel):
    name: str
    capabilities: list[str] = []


class EnrollResponse(BaseModel):
    executor_id: str
    token: str


@router.post("/enroll", response_model=EnrollResponse)
async def enroll(body: EnrollRequest, gateway: ExecutorGateway = Depends(require_executor_gateway)):
    device, token = await gateway.devices.enroll(name=body.name, capabilities=body.capabilities)
    return EnrollResponse(executor_id=device.executor_id, token=token)


@router.get("/devices")
async def list_devices(gateway: ExecutorGateway = Depends(require_executor_gateway)):
    return {
        "devices": [
            {
                "executor_id": device.executor_id,
                "name": device.name,
                "capabilities": list(device.capabilities),
                "enrolled_at": device.enrolled_at.isoformat(),
                "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
                "revoked_at": device.revoked_at.isoformat() if device.revoked_at else None,
                "connected": gateway.is_connected(device.executor_id),
            }
            for device in await gateway.devices.list_devices()
        ]
    }


@router.post("/devices/{executor_id}/revoke")
async def revoke_device(executor_id: str, gateway: ExecutorGateway = Depends(require_executor_gateway)):
    await gateway.revoke(executor_id)
    return {"ok": True}


def _sse(event: str, data: dict) -> str:
    # `kind` rides inside the payload because the desktop's shared SSE frame
    # parser surfaces only `data:` lines, not event names.
    payload = {"kind": event, **data}
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


@router.get("/stream")
async def stream(
    request: Request,
    cursor: int = Query(default=0),
    device: ExecutorDevice = Depends(require_executor_device),
    gateway: ExecutorGateway = Depends(require_executor_gateway),
):
    lease = await gateway.connect(device)

    async def generate():
        try:
            yield _sse(
                "lease",
                {
                    "lease_id": lease.lease_id,
                    "executor_id": device.executor_id,
                    "expires_at": lease.expires_at.isoformat(),
                },
            )
            last_seq = cursor
            while True:
                if await request.is_disconnected():
                    return
                # A revoke or a superseding reconnect clears/replaces the
                # stream owner; this stream must stop delivering commands
                # the moment it no longer owns the connection.
                if gateway.stream_owner(device.executor_id) != lease.lease_id:
                    return
                commands = await gateway.pending_commands(device.executor_id, last_seq)
                for command in commands:
                    yield _sse(
                        "command",
                        {
                            "seq": command.seq,
                            "type": command.command_type,
                            "invocation_id": command.invocation_id,
                            "payload": command.payload,
                        },
                    )
                    last_seq = command.seq
                if not commands:
                    yield _sse("keepalive", {"seq": last_seq})
                await gateway.wait_for_commands(device.executor_id, timeout=EXECUTOR_STREAM_KEEPALIVE_SECONDS)
        except asyncio.CancelledError:
            raise
        finally:
            gateway.disconnect(device.executor_id, lease.lease_id)

    return SSEStreamingResponse(generate(), media_type="text/event-stream")


class StartedRequest(BaseModel):
    lease_id: str
    invocation_id: str


@router.post("/started")
async def started(
    body: StartedRequest,
    device: ExecutorDevice = Depends(require_executor_device),
    gateway: ExecutorGateway = Depends(require_executor_gateway),
):
    try:
        record = await gateway.accept_started(device, body.lease_id, body.invocation_id)
    except StaleLeaseError:
        raise HTTPException(status_code=403, detail="Stale executor lease") from None
    except InvocationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown invocation: {body.invocation_id}") from None
    return {"invocation_id": record.invocation_id, "status": record.status, "cancel_requested": record.cancel_requested}


class ResultRequest(BaseModel):
    lease_id: str
    invocation_id: str
    status: InvocationStatus
    result_payload: str
    error_code: str | None = None


@router.post("/results")
async def results(
    body: ResultRequest,
    device: ExecutorDevice = Depends(require_executor_device),
    gateway: ExecutorGateway = Depends(require_executor_gateway),
):
    try:
        record = await gateway.accept_result(
            device,
            body.lease_id,
            invocation_id=body.invocation_id,
            status=body.status,
            result_payload=body.result_payload,
            error_code=body.error_code,
        )
    except StaleLeaseError:
        raise HTTPException(status_code=403, detail="Stale executor lease") from None
    except InvocationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown invocation: {body.invocation_id}") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"invocation_id": record.invocation_id, "status": record.status}


class DeviceSkillAd(BaseModel):
    name: str
    description: str = ""
    sha256: str


class SkillsAdvertisement(BaseModel):
    lease_id: str
    skills: list[DeviceSkillAd]


class DeviceSkillBody(BaseModel):
    name: str
    sha256: str
    body: str


class SkillBodiesRequest(BaseModel):
    lease_id: str
    skills: list[DeviceSkillBody]


def _validated_skill_ads(skills: list[DeviceSkillAd]) -> list[dict]:
    if len(skills) > DEVICE_SKILL_MAX_COUNT:
        raise HTTPException(status_code=413, detail=f"At most {DEVICE_SKILL_MAX_COUNT} device skills are accepted.")
    validated = []
    for skill in skills:
        if not _SKILL_NAME_RE.fullmatch(skill.name):
            continue
        validated.append(
            {
                "name": skill.name,
                "description": skill.description[:DEVICE_SKILL_MAX_DESCRIPTION_CHARS],
                "sha256": skill.sha256,
            }
        )
    return validated


@router.post("/skills")
async def advertise_skills(
    body: SkillsAdvertisement,
    device: ExecutorDevice = Depends(require_executor_device),
    gateway: ExecutorGateway = Depends(require_executor_gateway),
    runtime: Runtime = Depends(get_runtime),
):
    """Authoritative snapshot of the device's skills; responds with the names
    whose SKILL.md body the server still needs."""
    if not runtime.stores:
        raise HTTPException(status_code=503, detail="Stores not available")
    try:
        await gateway.heartbeat(device, body.lease_id)
    except StaleLeaseError:
        raise HTTPException(status_code=403, detail="Stale executor lease") from None
    needed = await runtime.stores.device_skills.replace_snapshot(device.executor_id, _validated_skill_ads(body.skills))
    await runtime.hydrate_device_skills()
    return {"need": needed}


@router.post("/skills/bodies")
async def upload_skill_bodies(
    body: SkillBodiesRequest,
    device: ExecutorDevice = Depends(require_executor_device),
    gateway: ExecutorGateway = Depends(require_executor_gateway),
    runtime: Runtime = Depends(get_runtime),
):
    if not runtime.stores:
        raise HTTPException(status_code=503, detail="Stores not available")
    try:
        await gateway.heartbeat(device, body.lease_id)
    except StaleLeaseError:
        raise HTTPException(status_code=403, detail="Stale executor lease") from None
    bodies = [
        {"name": entry.name, "sha256": entry.sha256, "body": entry.body}
        for entry in body.skills
        if len(entry.body.encode("utf-8")) <= DEVICE_SKILL_MAX_BODY_BYTES
    ]
    await runtime.stores.device_skills.save_bodies(device.executor_id, bodies)
    await runtime.hydrate_device_skills()
    return {"stored": [entry["name"] for entry in bodies]}


class HeartbeatRequest(BaseModel):
    lease_id: str
    acked_seq: int | None = None


@router.post("/heartbeat")
async def heartbeat(
    body: HeartbeatRequest,
    device: ExecutorDevice = Depends(require_executor_device),
    gateway: ExecutorGateway = Depends(require_executor_gateway),
):
    try:
        lease = await gateway.heartbeat(device, body.lease_id, acked_seq=body.acked_seq)
    except StaleLeaseError:
        raise HTTPException(status_code=403, detail="Stale executor lease") from None
    return {"lease_id": lease.lease_id, "expires_at": lease.expires_at.isoformat()}
