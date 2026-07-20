from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build

from ntrp.integrations.base import IntegrationConnectionError
from ntrp.integrations.google_auth.auth import (
    SCOPES_CALENDAR,
    get_google_credentials,
    has_scope,
)
from ntrp.logging import get_logger
from ntrp.search.types import RawItem
from ntrp.settings import NTRP_DIR


def parse_event_datetime(dt_obj: dict) -> datetime | None:
    if "dateTime" in dt_obj:
        dt_str = dt_obj["dateTime"]
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except Exception:
            return None
    elif "date" in dt_obj:
        # All-day event (date only)
        try:
            return datetime.strptime(dt_obj["date"], "%Y-%m-%d").replace(tzinfo=UTC)
        except Exception:
            return None
    return None


def format_event_time(start: datetime | None, end: datetime | None, is_all_day: bool) -> str:
    if not start:
        return ""

    if is_all_day:
        if end and (end - start).days > 1:
            return f"{start.strftime('%Y-%m-%d')} - {end.strftime('%Y-%m-%d')}"
        return start.strftime("%Y-%m-%d") + " (all day)"

    local_start = start.astimezone() if start.tzinfo is not None else start
    date_str = local_start.strftime("%Y-%m-%d")
    time_str = local_start.strftime("%H:%M")
    if end:
        local_end = end.astimezone() if end.tzinfo is not None else end
        time_str += f" - {local_end.strftime('%H:%M')}"
    return f"{date_str} {time_str}"


def _apply_time_update(
    event: dict,
    start: datetime | None,
    end: datetime | None,
    all_day: bool | None,
) -> None:
    if start is None and all_day is None:
        return

    is_all_day = all_day if all_day is not None else "date" in event.get("start", {})

    if start is not None:
        if is_all_day:
            event["start"] = {"date": start.strftime("%Y-%m-%d")}
        else:
            event["start"] = {"dateTime": start.isoformat()}

    if end is not None:
        if is_all_day:
            event["end"] = {"date": end.strftime("%Y-%m-%d")}
        else:
            event["end"] = {"dateTime": end.isoformat()}
    elif start is not None:
        if is_all_day:
            event["end"] = {"date": (start + timedelta(days=1)).strftime("%Y-%m-%d")}
        else:
            event["end"] = {"dateTime": (start + timedelta(hours=1)).isoformat()}


class GoogleCalendar:
    name = "calendar"

    def __init__(
        self,
        token_path: Path | None = None,
        days_back: int = 7,
        days_ahead: int = 30,
    ):
        if token_path:
            self.token_path = token_path
        else:
            self.token_path = NTRP_DIR / "calendar_token.json"

        self.days_back = days_back
        self.days_ahead = days_ahead

        self._service = None
        self._creds = None
        self._events_cache: dict[str, dict] = {}
        self._email_address: str | None = None
        self.auth_error: str | None = None

    def _has_calendar_scope(self) -> bool:
        if self._creds and self._creds.scopes:
            return has_scope(self._creds, SCOPES_CALENDAR[0])
        return False

    def _get_credentials(self):
        if self._creds is None or not self._creds.valid:
            self._creds = get_google_credentials(
                self.token_path,
                require_scopes=SCOPES_CALENDAR,
                integration_id="calendar",
            )
        return self._creds

    def _get_service(self):
        if self._service is None:
            creds = self._get_credentials()
            self._service = build("calendar", "v3", credentials=creds)
        return self._service

    def get_email_address(self) -> str:
        if self._email_address is not None:
            return self._email_address
        try:
            service = self._get_service()
            calendar = service.calendars().get(calendarId="primary").execute()
            self._email_address = calendar.get("id", "")
            return self._email_address
        except IntegrationConnectionError:
            raise
        except Exception:
            return ""

    def verify_connection(self) -> None:
        self._get_service().calendars().get(calendarId="primary").execute()

    def _parse_event(self, event: dict) -> RawItem:
        event_id = event.get("id", "")

        start_obj = event.get("start", {})
        end_obj = event.get("end", {})
        is_all_day = "date" in start_obj

        start = parse_event_datetime(start_obj)
        end = parse_event_datetime(end_obj)

        # Build title and content
        summary = event.get("summary", "(No title)")
        description = event.get("description", "")
        location = event.get("location", "")

        time_str = format_event_time(start, end, is_all_day)

        content_parts = []
        if time_str:
            content_parts.append(f"Time: {time_str}")
        if location:
            content_parts.append(f"Location: {location}")
        if description:
            content_parts.append(f"\n{description}")

        content = "\n".join(content_parts)

        # Attendees
        attendees = [a.get("email", "") for a in event.get("attendees", [])]

        created_at = start or datetime.now(tz=UTC)

        return RawItem(
            source="calendar",
            source_id=event_id,
            title=summary,
            content=content,
            created_at=created_at,
            updated_at=created_at,
            metadata={
                "calendar_id": event.get("organizer", {}).get("email", "primary"),
                "start": start.isoformat() if start else "",
                "end": end.isoformat() if end else "",
                "is_all_day": is_all_day,
                "location": location,
                "status": event.get("status", ""),
                "attendees": attendees,
                "html_link": event.get("htmlLink", ""),
            },
        )

    def get_upcoming(self, days: int = 7, limit: int = 20) -> list[RawItem]:
        service = self._get_service()

        now = datetime.now(tz=UTC)
        time_max = (now + timedelta(days=days)).isoformat()

        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=now.isoformat(),
                timeMax=time_max,
                maxResults=limit,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        events = events_result.get("items", [])
        return [self._parse_event(e) for e in events]

    def get_past(self, days: int = 7, limit: int = 20) -> list[RawItem]:
        service = self._get_service()

        now = datetime.now(tz=UTC)
        time_min = (now - timedelta(days=days)).isoformat()

        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=now.isoformat(),
                maxResults=limit,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        events = events_result.get("items", [])
        return [self._parse_event(e) for e in events]

    def create_event(
        self,
        summary: str,
        start: datetime,
        end: datetime | None = None,
        description: str = "",
        location: str = "",
        attendees: list[str] | None = None,
        all_day: bool = False,
    ) -> str:
        service = self._get_service()

        if end is None:
            end = start + timedelta(hours=1)

        event_body: dict[str, Any] = {
            "summary": summary,
        }

        if all_day:
            event_body["start"] = {"date": start.strftime("%Y-%m-%d")}
            event_body["end"] = {"date": end.strftime("%Y-%m-%d")}
        else:
            event_body["start"] = {"dateTime": start.isoformat()}
            event_body["end"] = {"dateTime": end.isoformat()}

        if description:
            event_body["description"] = description
        if location:
            event_body["location"] = location
        if attendees:
            event_body["attendees"] = [{"email": email} for email in attendees]

        try:
            event = (
                service.events()
                .insert(
                    calendarId="primary",
                    body=event_body,
                )
                .execute()
            )

            event_id = event.get("id", "")
            html_link = event.get("htmlLink", "")
            return f"Created event: {summary} (id: {event_id})\n{html_link}"
        except Exception as e:
            return f"Error creating event: {e}"

    def delete_event(self, event_id: str) -> str:
        service = self._get_service()

        try:
            service.events().delete(
                calendarId="primary",
                eventId=event_id,
            ).execute()
            return f"Deleted event: {event_id}"
        except Exception as e:
            return f"Error deleting event: {e}"

    def update_event(
        self,
        event_id: str,
        summary: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        description: str | None = None,
        location: str | None = None,
        attendees: list[str] | None = None,
        all_day: bool | None = None,
    ) -> str:
        service = self._get_service()

        try:
            # Get existing event
            event = (
                service.events()
                .get(
                    calendarId="primary",
                    eventId=event_id,
                )
                .execute()
            )

            # Update fields if provided
            if summary is not None:
                event["summary"] = summary
            if description is not None:
                event["description"] = description
            if location is not None:
                event["location"] = location
            if attendees is not None:
                event["attendees"] = [{"email": email} for email in attendees]

            # Handle time updates
            _apply_time_update(event, start, end, all_day)

            # Update the event
            updated = (
                service.events()
                .update(
                    calendarId="primary",
                    eventId=event_id,
                    body=event,
                )
                .execute()
            )

            html_link = updated.get("htmlLink", "")
            return f"Updated event: {updated.get('summary', event_id)}\n{html_link}"
        except Exception as e:
            return f"Error updating event: {e}"

    def search(self, query: str, limit: int = 20) -> list[RawItem]:
        service = self._get_service()

        now = datetime.now(tz=UTC)
        time_min = (now - timedelta(days=self.days_back)).isoformat()
        time_max = (now + timedelta(days=self.days_ahead)).isoformat()

        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                maxResults=limit,
                singleEvents=True,
                orderBy="startTime",
                q=query,
            )
            .execute()
        )

        events = events_result.get("items", [])
        return [self._parse_event(e) for e in events]


_logger = get_logger(__name__)


class MultiCalendarSource:
    name = "calendar"

    def __init__(self, token_paths: list[Path], days_back: int, days_ahead: int):
        self.sources: list[GoogleCalendar] = []
        self._errors: dict[str, str] = {}
        connection_error: IntegrationConnectionError | None = None

        for token_path in token_paths:
            try:
                src = GoogleCalendar(
                    token_path=token_path,
                    days_back=days_back,
                    days_ahead=days_ahead,
                )
                src._get_credentials()
                self.sources.append(src)
            except IntegrationConnectionError as exc:
                connection_error = exc
                self._errors[token_path.name] = exc.detail
            except Exception as e:
                self._errors[token_path.name] = str(e)

        if not self.sources and connection_error is not None:
            raise connection_error

    @property
    def errors(self) -> dict[str, str]:
        errors = dict(self._errors)
        for src in self.sources:
            if src.auth_error:
                key = src.get_email_address() or src.token_path.name
                errors[key] = src.auth_error
        return errors

    @property
    def details(self) -> dict:
        return {"accounts": self.list_accounts()}

    def verify_connection(self) -> None:
        if not self.sources:
            raise IntegrationConnectionError(
                integration_id="calendar",
                reason="not_configured",
                detail="No Google Calendar account is connected.",
            )
        last_error: Exception | None = None
        for source in self.sources:
            try:
                source.verify_connection()
                return
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error

    def list_accounts(self) -> list[str]:
        accounts: list[str] = []
        for src in self.sources:
            email = src.get_email_address()
            if email:
                accounts.append(email)
        return accounts

    def _collect(self, fn: Callable[[GoogleCalendar], list[RawItem]], limit: int) -> list[RawItem]:
        items: list[RawItem] = []
        connection_error: IntegrationConnectionError | None = None
        for src in self.sources:
            try:
                items.extend(fn(src))
            except RefreshError as e:
                key = src.get_email_address() or src.token_path.name
                _logger.warning("Calendar auth failed for %s: %s", key, e)
                src.auth_error = str(e)
            except IntegrationConnectionError as exc:
                connection_error = exc
                src.auth_error = exc.detail
            except Exception as e:
                key = src.get_email_address() or src.token_path.name
                _logger.warning("Calendar failed for %s: %s", key, e)
        if not items and connection_error is not None:
            raise connection_error
        items.sort(key=lambda x: x.metadata.get("start", ""))
        return items[:limit]

    def get_upcoming(self, days: int = 7, limit: int = 20) -> list[RawItem]:
        per = max(limit // len(self.sources), 5) if self.sources else limit
        return self._collect(lambda s: s.get_upcoming(days=days, limit=per), limit)

    def get_past(self, days: int = 7, limit: int = 20) -> list[RawItem]:
        per = max(limit // len(self.sources), 5) if self.sources else limit
        return self._collect(lambda s: s.get_past(days=days, limit=per), limit)

    def create_event(
        self,
        account: str,
        summary: str,
        start: datetime,
        end: datetime | None = None,
        description: str = "",
        location: str = "",
        attendees: list[str] | None = None,
        all_day: bool = False,
    ) -> str:
        if not account:
            # Default to first available calendar
            if self.sources:
                return self.sources[0].create_event(
                    summary=summary,
                    start=start,
                    end=end,
                    description=description,
                    location=location,
                    attendees=attendees,
                    all_day=all_day,
                )
            return "Error: no calendar accounts available"

        account_lower = account.lower().strip()
        for src in self.sources:
            email = src.get_email_address().lower()
            if email == account_lower:
                return src.create_event(
                    summary=summary,
                    start=start,
                    end=end,
                    description=description,
                    location=location,
                    attendees=attendees,
                    all_day=all_day,
                )

        accounts = self.list_accounts()
        if accounts:
            return f"Error: account not found. Available: {', '.join(accounts)}"
        return "Error: no Calendar accounts available"

    def delete_event(self, event_id: str) -> str:
        for src in self.sources:
            result = src.delete_event(event_id)
            if not result.startswith("Error"):
                return result
        return f"Error: event not found: {event_id}"

    def update_event(
        self,
        event_id: str,
        summary: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        description: str | None = None,
        location: str | None = None,
        attendees: list[str] | None = None,
        all_day: bool | None = None,
    ) -> str:
        for src in self.sources:
            result = src.update_event(
                event_id=event_id,
                summary=summary,
                start=start,
                end=end,
                description=description,
                location=location,
                attendees=attendees,
                all_day=all_day,
            )
            if not result.startswith("Error"):
                return result
        return f"Error: event not found: {event_id}"

    def search(self, query: str, limit: int = 20) -> list[RawItem]:
        per = max(limit // len(self.sources), 5) if self.sources else limit
        return self._collect(lambda s: s.search(query, limit=per), limit)
