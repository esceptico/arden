import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from arden.integrations.gmail.client import GmailSource
from arden.integrations.google_auth.auth import (
    CREDENTIALS_PATH,
    GoogleServiceChoice,
    add_google_account,
    discover_gmail_tokens,
)
from arden.server.runtime import Runtime, get_runtime
from arden.settings import ARDEN_DIR

router = APIRouter(prefix="/gmail", tags=["gmail"])


class GmailAddRequest(BaseModel):
    service_choice: GoogleServiceChoice = "all"


@router.get("/accounts")
async def gmail_accounts():
    accounts = []
    token_paths = discover_gmail_tokens()

    for token_path in token_paths:
        try:
            src = GmailSource(token_path=token_path)
            email = src.get_email_address()
            has_send = src.has_send_scope()
            accounts.append(
                {
                    "email": email,
                    "token_file": token_path.name,
                    "has_send_scope": has_send,
                }
            )
        except Exception as e:
            name = token_path.stem
            email = name.removeprefix("gmail_token_") if name.startswith("gmail_token_") else None
            accounts.append(
                {
                    "email": email,
                    "token_file": token_path.name,
                    "error": str(e),
                }
            )

    return {"accounts": accounts}


def _google_oauth_detail(exc: Exception) -> str:
    text = str(exc)
    lower = text.lower()
    if isinstance(exc, FileNotFoundError):
        return (
            f"Google credentials not found at {CREDENTIALS_PATH}. "
            "Download OAuth Desktop app credentials from Google Cloud Console."
        )
    if "access_denied" in lower or "test user" in lower:
        return "Google OAuth was denied. If this is a Testing app, add this Google account as a test user."
    if "redirect" in lower and ("mismatch" in lower or "uri" in lower):
        return "Google OAuth redirect URI mismatch. Use OAuth client type Desktop app, not Web application."
    if "403" in lower or "api has not been used" in lower or "access not configured" in lower:
        return "Google API returned 403. Enable the Gmail API and/or Google Calendar API for this Google Cloud area."
    if "missing google oauth scope" in lower or ("insufficient" in lower and "scope" in lower):
        return f"Missing Google OAuth scope: {text}. Re-run setup with the required Google service choice."
    return text


@router.post("/add")
async def gmail_add(req: GmailAddRequest | None = None, runtime: Runtime = Depends(get_runtime)):
    service_choice = req.service_choice if req else "all"
    try:
        result = await asyncio.to_thread(add_google_account, service_choice)
        await runtime.sync_google_sources()
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=_google_oauth_detail(e))


@router.delete("/{token_file}")
async def gmail_remove(token_file: str, runtime: Runtime = Depends(get_runtime)):
    # Security: validate filename and ensure path stays within ARDEN_DIR
    if not token_file.startswith("gmail_token") or not token_file.endswith(".json"):
        raise HTTPException(status_code=400, detail="Invalid token file name")

    token_path = (ARDEN_DIR / token_file).resolve()
    if not token_path.is_relative_to(ARDEN_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Invalid token file path")
    if not token_path.exists():
        raise HTTPException(status_code=404, detail="Token file not found")

    try:
        email = None
        try:
            src = GmailSource(token_path=token_path)
            email = src.get_email_address()
        except Exception:
            pass

        token_path.unlink()
        await runtime.sync_google_sources()

        return {"email": email, "status": "removed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
