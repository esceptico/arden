import base64
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.header import decode_header as decode_rfc2047
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any

import markdown
from googleapiclient.discovery import build

from ntrp.core.prompts import env
from ntrp.integrations.google_auth.auth import (
    SCOPES_ALL,
    SCOPES_GMAIL_SEND,
    get_google_credentials,
    has_scope,
)
from ntrp.logging import get_logger
from ntrp.search.types import RawItem
from ntrp.settings import NTRP_DIR


@dataclass(frozen=True)
class SourceItem:
    identity: str
    title: str
    source: str
    account: str = ""
    timestamp: datetime | None = None
    preview: str | None = None


@dataclass(frozen=True)
class ReadEmailResult:
    content: str
    account: str


EMAIL_HTML_TEMPLATE = env.from_string("""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 800px;
            margin: 0;
            padding: 20px;
        }
        table {
            border-collapse: collapse;
            width: 100%;
            margin: 16px 0;
        }
        th, td {
            border: 1px solid #ddd;
            padding: 8px 12px;
            text-align: left;
        }
        th {
            background: #f5f5f5;
            font-weight: 600;
        }
        pre {
            background: #f5f5f5;
            padding: 12px;
            border-radius: 4px;
            overflow-x: auto;
            margin: 16px 0;
        }
        code {
            background: #f5f5f5;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Courier New', Consolas, monospace;
            font-size: 0.9em;
        }
        pre code {
            background: transparent;
            padding: 0;
        }
        h1, h2, h3 {
            margin-top: 24px;
            margin-bottom: 12px;
            color: #111;
            font-weight: 600;
        }
        h1 { font-size: 24px; }
        h2 { font-size: 20px; }
        h3 { font-size: 18px; }
        ul, ol {
            margin: 12px 0;
            padding-left: 24px;
        }
        li {
            margin: 4px 0;
        }
        p {
            margin: 12px 0;
        }
        a {
            color: #0066cc;
            text-decoration: none;
        }
        a:hover {
            text-decoration: underline;
        }
        strong {
            font-weight: 600;
        }
        blockquote {
            border-left: 4px solid #ddd;
            margin: 16px 0;
            padding-left: 16px;
            color: #666;
        }
    </style>
</head>
<body>
{{ content }}
</body>
</html>""")


def decode_base64_body(data: str) -> str:
    try:
        decoded = base64.urlsafe_b64decode(data)
        return decoded.decode("utf-8", errors="replace")
    except Exception:
        return ""  # Invalid base64 data - return empty string


def find_email_parts(part: dict[str, Any]) -> tuple[str, str]:
    """
    Recursively extract text/plain and text/html from MIME structure.

    Returns:
        Tuple of (plain_text, html_text)
    """
    plain_content = ""
    html_content = ""
    mime_type = part.get("mimeType", "")

    if mime_type.startswith("multipart/"):
        for sub_part in part.get("parts", []):
            plain, html = find_email_parts(sub_part)
            plain_content += plain
            html_content += html
    elif mime_type == "text/plain":
        body = part.get("body", {})
        if "data" in body:
            plain_content = decode_base64_body(body["data"])
    elif mime_type == "text/html":
        body = part.get("body", {})
        if "data" in body:
            html_content = decode_base64_body(body["data"])

    return plain_content, html_content


def html_to_plain(html: str) -> str:
    if not html:
        return ""

    text = html
    # Remove script/style blocks
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.I)
    # Convert br/p to newlines
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Unescape HTML entities
    text = unescape(text)
    # Normalize whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def decode_email_header(value: str | None) -> str:
    """Decode RFC-2047 encoded headers (=?utf-8?Q?...?=)."""
    if not value:
        return ""

    parts = decode_rfc2047(value)
    decoded = []
    for part, enc in parts:
        if isinstance(part, bytes):
            part = part.decode(enc or "utf-8", errors="replace")
        decoded.append(part)
    return "".join(decoded).strip()


def extract_headers(headers: list[dict]) -> dict[str, str]:
    """Extract headers into a dict with lowercase keys."""
    return {h.get("name", "").lower(): h.get("value", "") for h in headers}


def parse_email_date(headers: list[dict], fallback_ms: int) -> datetime:
    """Parse email date from headers or fallback to internalDate."""
    header_dict = extract_headers(headers)
    date_str = header_dict.get("date")

    if date_str:
        try:
            parsed = parsedate_to_datetime(date_str)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed
        except Exception:
            pass

    # Fallback to Gmail's internalDate (milliseconds)
    try:
        return datetime.fromtimestamp(fallback_ms / 1000, tz=UTC)
    except (ValueError, OSError):
        return datetime.now(tz=UTC)


class GmailSource:
    name = "gmail"

    def __init__(
        self,
        token_path: Path | None = None,
        days_back: int = 30,
    ):
        self.token_path = token_path or (NTRP_DIR / "gmail_token.json")
        self.days_back = days_back

        self._service = None
        self._creds = None
        self._emails_cache: dict[str, dict] = {}  # id -> raw email
        self._email_address: str | None = None

    def _get_credentials(self):
        if self._creds is None or not self._creds.valid:
            self._creds = get_google_credentials(self.token_path, scopes=SCOPES_ALL)
        return self._creds

    def has_send_scope(self) -> bool:
        try:
            creds = self._get_credentials()
            return has_scope(creds, SCOPES_GMAIL_SEND[0])
        except Exception:
            return False  # Token invalid or network error - assume no send scope

    def _get_service(self):
        if self._service is None:
            creds = self._get_credentials()
            self._service = build("gmail", "v1", credentials=creds)
        return self._service

    def get_email_address(self) -> str:
        if self._email_address is not None:
            return self._email_address
        try:
            service = self._get_service()
            profile = service.users().getProfile(userId="me").execute()
            self._email_address = profile.get("emailAddress", "")
            return self._email_address
        except Exception:
            return ""  # API error fetching profile - return empty email

    def send(self, to: str, subject: str, body: str, from_email: str | None = None, html: bool = False) -> str:
        if not to:
            return "Error: recipient is required"

        if not self.has_send_scope():
            return "Error: Gmail token lacks send permission. Run `ntrp gmail add` to re-authenticate with send scope."

        body_text = body or ""

        if html:
            # Create multipart message with both plain text and HTML
            message = MIMEMultipart("alternative")
            message["to"] = to
            message["subject"] = subject or "(no subject)"
            if from_email:
                message["from"] = from_email

            # Add plain text version (strip markdown)
            plain_part = MIMEText(body_text, "plain")
            message.attach(plain_part)

            # Convert markdown to HTML
            html_body = self._markdown_to_html(body_text)
            html_part = MIMEText(html_body, "html")
            message.attach(html_part)
        else:
            message = MIMEText(body_text)
            message["to"] = to
            message["subject"] = subject or "(no subject)"
            if from_email:
                message["from"] = from_email

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

        try:
            service = self._get_service()
            sent = (
                service.users()
                .messages()
                .send(
                    userId="me",
                    body={"raw": raw},
                )
                .execute()
            )
            msg_id = sent.get("id", "")
            return f"Sent email to {to}" + (f" (id: {msg_id})" if msg_id else "")
        except Exception as e:
            return f"Error sending email: {e}"

    def _markdown_to_html(self, markdown_text: str) -> str:
        # Convert markdown to HTML with common extensions
        md = markdown.Markdown(
            extensions=[
                "extra",  # tables, fenced code, etc.
                "nl2br",  # newline to <br>
                "sane_lists",  # better list handling
                "codehilite",  # code highlighting
            ]
        )
        content = md.convert(markdown_text)

        return EMAIL_HTML_TEMPLATE.render(content=content)

    def _fetch_message_metadata(self, msg_id: str) -> dict | None:
        cache_key = f"meta:{msg_id}"
        if cache_key in self._emails_cache:
            return self._emails_cache[cache_key]

        try:
            service = self._get_service()
            msg = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=msg_id,
                    format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date"],
                )
                .execute()
            )
            self._emails_cache[cache_key] = msg
            return msg
        except Exception:
            return None  # API error - message not found or permission denied

    def _fetch_message_full(self, msg_id: str) -> dict | None:
        cache_key = f"full:{msg_id}"
        if cache_key in self._emails_cache:
            return self._emails_cache[cache_key]

        try:
            service = self._get_service()
            msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
            self._emails_cache[cache_key] = msg
            return msg
        except Exception:
            return None  # API error fetching full message

    def _build_raw_item(self, raw: dict, content: str) -> RawItem:
        msg_id = raw.get("id", "")
        payload = raw.get("payload", {})
        headers = payload.get("headers", [])
        header_dict = extract_headers(headers)

        internal_date = int(raw.get("internalDate", 0))
        email_date = parse_email_date(headers, internal_date)

        subject = decode_email_header(header_dict.get("subject", ""))
        sender = decode_email_header(header_dict.get("from", ""))
        title = subject if subject else f"Email from {sender}"

        return RawItem(
            source="gmail",
            source_id=msg_id,
            title=title,
            content=content,
            created_at=email_date,
            updated_at=email_date,
            metadata={
                "account": self.get_email_address(),
                "thread_id": raw.get("threadId", ""),
                "labels": raw.get("labelIds", []),
                "from": sender,
                "to": decode_email_header(header_dict.get("to", "")),
                "subject": subject,
                "snippet": raw.get("snippet", ""),
            },
        )

    def _parse_metadata(self, raw: dict) -> RawItem:
        return self._build_raw_item(raw, raw.get("snippet", ""))

    def _parse_full_message(self, raw: dict) -> RawItem:
        payload = raw.get("payload", {})
        plain_text, html_text = find_email_parts(payload)
        content = plain_text.strip() if plain_text.strip() else html_to_plain(html_text)
        if not content:
            content = raw.get("snippet", "")
        return self._build_raw_item(raw, content)

    def read(self, source_id: str) -> str | None:
        msg = self._fetch_message_full(source_id)
        if not msg:
            return None

        item = self._parse_full_message(msg)

        # Format nicely
        meta = item.metadata
        lines = [
            f"From: {meta.get('from', '')}",
            f"To: {meta.get('to', '')}",
            f"Subject: {meta.get('subject', '')}",
            f"Date: {item.created_at.strftime('%Y-%m-%d %H:%M')}",
            "",
            item.content,
        ]
        return "\n".join(lines)

    def search(self, query: str, limit: int = 50) -> list[RawItem]:
        """
        Search emails using Gmail's native search (metadata only).

        Gmail does server-side search - no need to download content.

        Args:
            query: Gmail search query (same syntax as Gmail search bar)
            limit: Max results to return

        Returns:
            List of RawItems with metadata only (snippet as content)
        """
        service = self._get_service()

        result = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                maxResults=limit,
            )
            .execute()
        )

        items = []
        for msg_meta in result.get("messages", []):
            msg = self._fetch_message_metadata(msg_meta["id"])
            if msg:
                items.append(self._parse_metadata(msg))

        return items

    def list_recent(self, days: int = 7, limit: int = 50) -> list[SourceItem]:
        """Get recent emails."""
        service = self._get_service()

        query = f"newer_than:{days}d"
        result = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                maxResults=limit,
            )
            .execute()
        )

        items = []
        for msg_meta in result.get("messages", []):
            msg = self._fetch_message_metadata(msg_meta["id"])
            if msg:
                raw_item = self._parse_metadata(msg)
                items.append(
                    SourceItem(
                        identity=raw_item.source_id,
                        title=raw_item.title,
                        source=self.name,
                        account=str(raw_item.metadata.get("account") or ""),
                        timestamp=raw_item.created_at,
                        preview=raw_item.metadata.get("snippet"),
                    )
                )

        return items


_logger = get_logger(__name__)


class MultiGmailSource:
    """Wrapper for multiple Gmail accounts."""

    name = "gmail"

    def __init__(self, token_paths: list[Path], days_back: int):
        self.sources: list[GmailSource] = []
        self._errors: dict[str, str] = {}

        for token_path in token_paths:
            try:
                src = GmailSource(token_path=token_path, days_back=days_back)
                src._get_credentials()
                self.sources.append(src)
            except Exception as e:
                self._errors[token_path.name] = str(e)

        self._days = days_back

    @property
    def errors(self) -> dict[str, str]:
        return self._errors

    @property
    def details(self) -> dict:
        return {"accounts": self.list_accounts(), "days": self._days}

    def list_accounts(self) -> list[str]:
        accounts: list[str] = []
        for src in self.sources:
            email = src.get_email_address()
            if email:
                accounts.append(email)
        return accounts

    def send_email(self, account: str, to: str, subject: str, body: str, html: bool = False) -> str:
        if not account:
            return "Error: account is required"

        account_lower = account.lower().strip()
        for src in self.sources:
            email = src.get_email_address().lower()
            if email == account_lower:
                return src.send(to=to, subject=subject, body=body, from_email=account, html=html)

        accounts = self.list_accounts()
        if accounts:
            return f"Error: account not found. Available: {', '.join(accounts)}"
        return "Error: no Gmail accounts available"

    def read(self, source_id: str) -> ReadEmailResult | None:
        for src in self.sources:
            result = src.read(source_id)
            if result and not result.startswith("Email not found"):
                return ReadEmailResult(content=result, account=src.get_email_address())
        return None

    def _handle_source_error(self, src: GmailSource, e: Exception) -> None:
        key = src.get_email_address() or src.token_path.name
        _logger.warning("Gmail failed for %s: %s", key, e)
        self._errors[key] = str(e)

    def search(self, query: str, limit: int = 50) -> list[RawItem]:
        items: list[RawItem] = []
        per_account = max(limit // len(self.sources), 10) if self.sources else limit
        for src in self.sources:
            try:
                items.extend(src.search(query, limit=per_account))
            except Exception as e:
                self._handle_source_error(src, e)
        items.sort(key=lambda x: x.updated_at, reverse=True)
        return items[:limit]

    def list_recent(self, days: int = 7, limit: int = 50) -> list[SourceItem]:
        items: list[SourceItem] = []
        per_account = max(limit // len(self.sources), 5) if self.sources else limit
        for src in self.sources:
            try:
                items.extend(src.list_recent(days=days, limit=per_account))
            except Exception as e:
                self._handle_source_error(src, e)
        items.sort(key=lambda x: x.timestamp or datetime.min.replace(tzinfo=UTC), reverse=True)
        return items[:limit]
