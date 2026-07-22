import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from arden.integrations.google_auth.accounts import GoogleAccount, GoogleService
from arden.integrations.google_auth.auth import (
    authorize_google_service,
    google_account_store,
    revoke_google_account,
)
from arden.server.runtime import Runtime, get_runtime

router = APIRouter(prefix="/google", tags=["google"])


class GoogleConnectRequest(BaseModel):
    account_id: str | None = None


def _account_payload(account: GoogleAccount) -> dict:
    return {
        "id": account.id,
        "email": account.email,
        "services": sorted(account.services),
        "scopes": list(account.scopes),
    }


@router.get("/accounts")
async def google_accounts():
    return {"accounts": [_account_payload(account) for account in google_account_store().list_accounts()]}


@router.post("/{service}/connect")
async def connect_google_service(
    service: GoogleService,
    req: GoogleConnectRequest | None = None,
    runtime: Runtime = Depends(get_runtime),
):
    try:
        account = await asyncio.to_thread(
            authorize_google_service,
            service,
            account_id=req.account_id if req else None,
        )
        await runtime.sync_google_sources()
        return {"status": "connected", "account": _account_payload(account)}
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{service}/accounts/{account_id}")
async def disconnect_google_service(
    service: GoogleService,
    account_id: str,
    runtime: Runtime = Depends(get_runtime),
):
    store = google_account_store()
    try:
        account = store.disconnect_service(account_id, service)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await runtime.sync_google_sources()
    return {"status": "disconnected", "account": _account_payload(account)}


@router.delete("/accounts/{account_id}")
async def remove_google_account(account_id: str, runtime: Runtime = Depends(get_runtime)):
    store = google_account_store()
    account = next((item for item in store.list_accounts() if item.id == account_id), None)
    if account is None:
        raise HTTPException(status_code=404, detail=f"Unknown Google account: {account_id}")
    warning = None
    try:
        await asyncio.to_thread(revoke_google_account, account, store)
    except Exception as exc:
        warning = str(exc)
    store.remove_account(account_id)
    await runtime.sync_google_sources()
    return {"status": "removed", "account_id": account_id, "warning": warning}


@router.post("/{service}/verify")
async def verify_google_service(service: GoogleService, runtime: Runtime = Depends(get_runtime)):
    await runtime.sync_google_sources()
    descriptor = await runtime.connection_service.verify_connection(service)
    return {"status": descriptor.state, "integration_id": service}
