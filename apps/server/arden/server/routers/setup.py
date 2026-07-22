import asyncio
import json
import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException
from google.oauth2.credentials import Credentials
from pydantic import BaseModel

from arden.integrations.google_auth.accounts import GoogleService
from arden.integrations.google_auth.auth import (
    SCOPES_CALENDAR,
    discover_calendar_tokens,
    discover_gmail_tokens,
    google_account_store,
    google_credentials_status,
    import_google_credentials_file,
    save_google_credentials_json,
    scopes_for_google_choice,
    scopes_for_google_service,
)
from arden.integrations.slack.client import SlackClient
from arden.server.routers.mcp import list_mcp_servers
from arden.server.runtime import Runtime, get_runtime
from arden.settings import mask_api_key

router = APIRouter(prefix="/setup", tags=["setup"])


class GooglePreflightRequest(BaseModel):
    service_choice: str | None = None
    integration_id: GoogleService | None = None


class SlackVerifyRequest(BaseModel):
    service_id: Literal["slack_bot_token", "slack_user_token"]
    api_key: str


def _provider_status(provider) -> dict:
    return {
        "id": provider.id,
        "label": provider.label,
        "kind": provider.kind,
        "status": provider.health.status,
        "detail": provider.health.detail,
        "tool_count": provider.tool_count,
    }


def _token_email_from_name(token_path: Path) -> str | None:
    name = token_path.stem
    if name.startswith("gmail_token_"):
        return name.removeprefix("gmail_token_")
    return None


def _load_token_credentials_passive(token_path: Path) -> tuple[Credentials | None, str | None]:
    """Load authorized-user token metadata without refreshing or starting OAuth."""
    try:
        data = json.loads(token_path.read_text())
        creds = Credentials.from_authorized_user_info(data)
    except Exception as e:
        return None, str(e)

    # Status endpoints must be passive: do not refresh and do not fall back to
    # InstalledAppFlow. Surface unusable tokens as health errors instead.
    if not creds.valid and not creds.refresh_token:
        return creds, "Google token is invalid and has no refresh token. Re-run setup."
    return creds, None


def _gmail_accounts() -> list[dict]:
    accounts = []
    for token_path in discover_gmail_tokens():
        creds, error = _load_token_credentials_passive(token_path)
        accounts.append(
            {
                "email": _token_email_from_name(token_path),
                "token_file": token_path.name,
                "has_send_scope": bool(
                    creds and creds.scopes and "https://www.googleapis.com/auth/gmail.send" in creds.scopes
                ),
                "error": error,
            }
        )
    return accounts


def _calendar_token_statuses() -> list[dict]:
    statuses = []
    for token_path in discover_calendar_tokens():
        creds, error = _load_token_credentials_passive(token_path)
        statuses.append(
            {
                "token_file": token_path.name,
                "has_calendar_scope": bool(creds and creds.scopes and SCOPES_CALENDAR[0] in creds.scopes),
                "error": error,
            }
        )
    return statuses


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
            "enabled": bool(getattr(runtime.config, "google", False)),
            "credentials": google_credentials_status(),
            "accounts": _gmail_accounts(),
            "google_accounts": _google_accounts(),
            "calendar_tokens": _calendar_token_statuses(),
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
async def setup_google_credentials(req: dict = Body(...)):
    path = req.get("path")
    json_payload = req.get("json")
    if bool(path) == bool(json_payload):
        raise HTTPException(status_code=400, detail="Provide exactly one of path or json")
    if path is not None and not isinstance(path, str):
        raise HTTPException(status_code=400, detail="path must be a string")
    if json_payload is not None and not isinstance(json_payload, dict):
        raise HTTPException(status_code=400, detail="json must be an object")
    try:
        if path:
            await asyncio.to_thread(import_google_credentials_file, path)
        else:
            save_google_credentials_json(json_payload or {})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "saved", "credentials": google_credentials_status()}


@router.post("/google/preflight")
async def setup_google_preflight(req: GooglePreflightRequest):
    try:
        if req.integration_id is not None:
            scopes = scopes_for_google_service(req.integration_id)
        elif req.service_choice is not None:
            scopes = scopes_for_google_choice(req.service_choice)
        else:
            raise ValueError("integration_id is required")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    credentials = google_credentials_status()
    warnings = []
    if not credentials["exists"]:
        warnings.append(credentials["error"])
    elif not credentials["valid"]:
        warnings.append(credentials["error"] or "Google credentials are invalid")
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
        data = await client.auth_test(token_kind)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not verify Slack token: {e}")

    return {
        "ok": True,
        "token_kind": token_kind,
        "team": data.get("team"),
        "team_id": data.get("team_id"),
        "user": data.get("user_id") or data.get("user"),
        "bot_id": data.get("bot_id"),
    }
