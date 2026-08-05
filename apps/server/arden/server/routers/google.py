import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.google_auth.accounts import (
    GoogleAccount,
    GoogleAccountStore,
    GoogleAccountStoreSnapshot,
    GoogleService,
)
from arden.integrations.google_auth.auth import (
    authorize_google_service,
    execute_google_account_revocation,
    google_account_store,
    prepare_google_account_revocation,
)
from arden.server.runtime import Runtime, get_runtime

router = APIRouter(prefix="/google", tags=["google"])


class GoogleConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    account_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    account_ref: str | None = Field(
        default=None,
        min_length=3,
        max_length=320,
        pattern=r"^[^@\s]+@[^@\s]+$",
    )


def _account_payload(account: GoogleAccount) -> dict:
    return {
        "id": account.id,
        "email": account.email,
        "services": sorted(account.services),
        "scopes": list(account.scopes),
    }


async def _sync_or_restore(
    runtime: Runtime,
    store: GoogleAccountStore,
    snapshot: GoogleAccountStoreSnapshot,
) -> None:
    try:
        await runtime.sync_google_sources()
    except BaseException as sync_error:
        try:
            store.restore(snapshot)
            await runtime.sync_google_sources()
        except BaseException as rollback_error:
            raise BaseExceptionGroup(
                "Google account runtime sync and rollback both failed",
                [sync_error, rollback_error],
            ) from sync_error
        raise


@router.get("/accounts")
async def google_accounts():
    return {"accounts": [_account_payload(account) for account in google_account_store().list_accounts()]}


@router.post("/{service}/connect")
async def connect_google_service(
    service: GoogleService,
    req: GoogleConnectRequest | None = None,
    runtime: Runtime = Depends(get_runtime),
):
    store = google_account_store()
    snapshot = store.snapshot()
    try:
        account = await asyncio.to_thread(
            authorize_google_service,
            service,
            account_id=req.account_id if req else None,
            expected_account_ref=req.account_ref if req else None,
            store=store,
        )
        await _sync_or_restore(runtime, store, snapshot)
        return {"status": "connected", "account": _account_payload(account)}
    except IntegrationConnectionError as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    except IntegrationOperationError as exc:
        status = 400 if exc.code == "configuration_error" else 502
        raise HTTPException(status_code=status, detail=exc.safe_message) from exc


@router.delete("/{service}/accounts/{account_id}")
async def disconnect_google_service(
    service: GoogleService,
    account_id: str,
    runtime: Runtime = Depends(get_runtime),
):
    store = google_account_store()
    snapshot = store.snapshot()
    try:
        account = store.disconnect_service(account_id, service)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _sync_or_restore(runtime, store, snapshot)
    return {"status": "disconnected", "account": _account_payload(account)}


@router.delete("/accounts/{account_id}")
async def remove_google_account(account_id: str, runtime: Runtime = Depends(get_runtime)):
    store = google_account_store()
    account = next((item for item in store.list_accounts() if item.id == account_id), None)
    if account is None:
        raise HTTPException(status_code=404, detail=f"Unknown Google account: {account_id}")
    try:
        revocation = await asyncio.to_thread(prepare_google_account_revocation, account, store)
    except IntegrationOperationError as exc:
        status = 409
        raise HTTPException(status_code=status, detail=exc.safe_message) from exc
    snapshot = store.snapshot()
    store.remove_account(account_id)
    await _sync_or_restore(runtime, store, snapshot)
    try:
        await asyncio.to_thread(execute_google_account_revocation, revocation)
    except IntegrationOperationError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"The Google account was removed locally. {exc.safe_message}",
        ) from exc
    return {"status": "removed", "account_id": account_id}
