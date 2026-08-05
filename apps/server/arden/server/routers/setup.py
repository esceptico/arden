import asyncio
import os
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.google_auth.accounts import GoogleService
from arden.integrations.google_auth.auth import google_account_store, scopes_for_google_service
from arden.integrations.google_auth.credentials import (
    GoogleCredentialsFileError,
    GoogleOAuthClientDocument,
    google_credentials_status,
    import_google_credentials_file,
    save_google_credentials_json,
)
from arden.integrations.slack.client import SlackClient
from arden.server.routers.mcp import list_mcp_servers
from arden.server.runtime import Runtime, get_runtime
from arden.settings import mask_api_key

router = APIRouter(prefix="/setup", tags=["setup"])


class GooglePreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    integration_id: GoogleService


class SlackVerifyRequest(BaseModel):
    service_id: Literal["slack_bot_token", "slack_user_token"]
    api_key: str


class GoogleCredentialsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str | None = Field(default=None, min_length=1)
    document: GoogleOAuthClientDocument | None = Field(default=None, alias="json")

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "GoogleCredentialsRequest":
        if (self.path is None) == (self.document is None):
            raise ValueError("Provide exactly one of path or json")
        if self.path is not None and not self.path.strip():
            raise ValueError("path must not be blank")
        return self


def _google_credentials_request(payload: object = Body(...)) -> GoogleCredentialsRequest:
    try:
        return GoogleCredentialsRequest.model_validate(payload, strict=True)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="Invalid Google Desktop OAuth credentials request.") from exc


def _provider_status(provider) -> dict:
    return {
        "id": provider.id,
        "label": provider.label,
        "kind": provider.kind,
        "status": provider.health.status,
        "detail": provider.health.detail,
        "tool_count": provider.tool_count,
    }


def _google_accounts() -> list[dict]:
    return [
        {
            "id": account.id,
            "email": account.email,
            "services": sorted(account.services),
            "scopes": list(account.scopes),
        }
        for account in google_account_store().list_accounts()
    ]


def _slack_services(runtime: Runtime) -> list[dict]:
    services = []
    config = runtime.config
    for integration in runtime.integrations.integrations.values():
        for field in integration.service_fields:
            if field.key not in {"slack_bot_token", "slack_user_token"}:
                continue
            key = getattr(config, field.key, None)
            from_env = bool(field.env_var and os.environ.get(field.env_var))
            services.append(
                {
                    "id": field.key,
                    "name": field.label,
                    "connected": bool(key),
                    "key_hint": mask_api_key(key),
                    "from_env": from_env,
                }
            )
    return services


@router.get("/status")
async def setup_status(runtime: Runtime = Depends(get_runtime)):
    native_providers = [_provider_status(provider) for provider in runtime.integrations.list_providers()]
    mcp_providers = (
        [_provider_status(provider) for provider in runtime.mcp_manager.list_providers()] if runtime.mcp_manager else []
    )
    mcp_servers = await list_mcp_servers(runtime)
    return {
        "google": {
            "enabled": any(
                runtime.config.integration_enabled(service) for service in ("gmail", "calendar", "google_drive")
            ),
            "credentials": google_credentials_status(),
            "google_accounts": _google_accounts(),
            "provider_statuses": [p for p in native_providers if p["id"] in {"gmail", "calendar", "google_drive"}],
        },
        "slack": {
            "services": _slack_services(runtime),
            "provider_status": next((p for p in native_providers if p["id"] == "slack"), None),
        },
        "mcp": {
            "servers": mcp_servers["servers"],
            "provider_statuses": mcp_providers,
        },
    }


@router.post("/google/credentials")
async def setup_google_credentials(req: GoogleCredentialsRequest = Depends(_google_credentials_request)):
    try:
        if req.path is not None:
            await asyncio.to_thread(import_google_credentials_file, req.path)
        else:
            assert req.document is not None
            save_google_credentials_json(req.document)
    except GoogleCredentialsFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "saved", "credentials": google_credentials_status()}


@router.post("/google/preflight")
async def setup_google_preflight(req: GooglePreflightRequest):
    scopes = scopes_for_google_service(req.integration_id)
    credentials = google_credentials_status()
    warnings = []
    if not credentials.exists:
        warnings.append(credentials.error)
    elif not credentials.valid:
        warnings.append(credentials.error or "Google credentials are invalid")
    return {"ok": not warnings, "credentials": credentials, "scopes": scopes, "warnings": warnings}


@router.post("/slack/verify")
async def setup_slack_verify(req: SlackVerifyRequest):
    if req.service_id == "slack_bot_token":
        if not req.api_key.startswith("xoxb-"):
            raise HTTPException(status_code=400, detail="slack_bot_token requires xoxb-")
        token_kind = "bot"
        client = SlackClient(bot_token=req.api_key)
    else:
        if not req.api_key.startswith("xoxp-"):
            raise HTTPException(status_code=400, detail="slack_user_token requires xoxp-")
        token_kind = "user"
        client = SlackClient(user_token=req.api_key)

    try:
        data = await client.transport.auth_test(token_kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (IntegrationConnectionError, IntegrationOperationError) as exc:
        raise HTTPException(status_code=400, detail=f"Could not verify Slack token: {exc}") from exc

    return {
        "ok": True,
        "token_kind": token_kind,
        "team": data.team_name,
        "team_id": data.team_ref,
        "user": data.user_ref,
        "bot_id": data.bot_ref,
    }
