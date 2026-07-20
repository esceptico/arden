from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from googleapiclient.discovery import build

from ntrp.integrations.google_auth.auth import SCOPES_DRIVE, get_google_credentials
from ntrp.integrations.google_drive.render import flatten_google_doc

DriveKind = Literal["all", "doc", "sheet"]
DocEditOperation = Literal["append", "replace_all"]
ValueInputOption = Literal["RAW", "USER_ENTERED"]

_DOC_MIME = "application/vnd.google-apps.document"
_SHEET_MIME = "application/vnd.google-apps.spreadsheet"


@dataclass(frozen=True)
class DriveFile:
    ref: str
    account_id: str
    file_id: str
    name: str
    kind: Literal["document", "spreadsheet"]
    modified_time: str | None
    url: str | None


class GoogleDriveClient:
    def __init__(
        self,
        token_path: Path,
        account_id: str,
        email: str | None,
        *,
        build_service: Callable[..., Any] = build,
        credentials: Any | None = None,
    ):
        self.token_path = token_path
        self.account_id = account_id
        self.email = email
        self._build_service = build_service
        self._credentials = credentials
        self._services: dict[tuple[str, str], Any] = {}

    def _creds(self):
        if self._credentials is None:
            self._credentials = get_google_credentials(
                self.token_path,
                require_scopes=SCOPES_DRIVE,
                integration_id="google_drive",
            )
        return self._credentials

    def _service(self, api: str, version: str):
        key = (api, version)
        if key not in self._services:
            self._services[key] = self._build_service(
                api,
                version,
                credentials=self._creds(),
                cache_discovery=False,
            )
        return self._services[key]

    @staticmethod
    def _escape_query(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    def search(self, query: str, *, kind: DriveKind = "all", limit: int = 20) -> list[DriveFile]:
        mime_types = {"doc": [_DOC_MIME], "sheet": [_SHEET_MIME], "all": [_DOC_MIME, _SHEET_MIME]}[kind]
        mime_query = " or ".join(f"mimeType = '{mime}'" for mime in mime_types)
        q = f"trashed = false and ({mime_query})"
        if query.strip():
            q += f" and fullText contains '{self._escape_query(query.strip())}'"
        response = (
            self._service("drive", "v3")
            .files()
            .list(
                q=q,
                pageSize=max(1, min(limit, 100)),
                orderBy="modifiedTime desc",
                fields="files(id,name,mimeType,modifiedTime,webViewLink)",
            )
            .execute()
        )
        out: list[DriveFile] = []
        for item in response.get("files", []):
            mime = item.get("mimeType")
            if mime not in {_DOC_MIME, _SHEET_MIME}:
                continue
            file_id = str(item.get("id") or "")
            if not file_id:
                continue
            out.append(
                DriveFile(
                    ref=f"{self.account_id}:{file_id}",
                    account_id=self.account_id,
                    file_id=file_id,
                    name=str(item.get("name") or "Untitled"),
                    kind="document" if mime == _DOC_MIME else "spreadsheet",
                    modified_time=item.get("modifiedTime"),
                    url=item.get("webViewLink"),
                )
            )
        return out

    def verify_connection(self) -> None:
        self._service("drive", "v3").files().list(pageSize=1, fields="files(id)").execute()

    def read_doc(self, document_id: str) -> dict:
        document = (
            self._service("docs", "v1")
            .documents()
            .get(documentId=document_id, includeTabsContent=True)
            .execute()
        )
        return {
            "ref": f"{self.account_id}:{document_id}",
            "title": document.get("title") or "Untitled",
            "text": flatten_google_doc(document),
            "revision_id": document.get("revisionId"),
            "url": f"https://docs.google.com/document/d/{document_id}/edit",
        }

    def create_doc(self, title: str) -> dict:
        documents = self._service("docs", "v1").documents()
        created = documents.create(body={"title": title}).execute()
        document_id = str(created["documentId"])
        return {
            "ref": f"{self.account_id}:{document_id}",
            "title": title,
            "url": f"https://docs.google.com/document/d/{document_id}/edit",
        }

    def edit_doc(self, document_id: str, *, operation: DocEditOperation, text: str, match: str | None) -> dict:
        documents = self._service("docs", "v1").documents()
        current = documents.get(documentId=document_id, includeTabsContent=True).execute()
        if operation == "append":
            requests = [{"insertText": {"endOfSegmentLocation": {}, "text": text}}]
        elif operation == "replace_all" and match:
            requests = [
                {
                    "replaceAllText": {
                        "containsText": {"text": match, "matchCase": True},
                        "replaceText": text,
                    }
                }
            ]
        else:
            raise ValueError("replace_all requires a non-empty match")
        body: dict[str, Any] = {"requests": requests}
        if revision_id := current.get("revisionId"):
            body["writeControl"] = {"requiredRevisionId": revision_id}
        documents.batchUpdate(documentId=document_id, body=body).execute()
        return {
            "ref": f"{self.account_id}:{document_id}",
            "title": current.get("title") or "Untitled",
            "url": f"https://docs.google.com/document/d/{document_id}/edit",
        }

    def read_sheet(self, spreadsheet_id: str, range_name: str) -> dict:
        values = (
            self._service("sheets", "v4")
            .spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_name)
            .execute()
        )
        return {
            "ref": f"{self.account_id}:{spreadsheet_id}",
            "range": values.get("range") or range_name,
            "values": values.get("values", []),
            "url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
        }

    def create_sheet(self, title: str) -> dict:
        spreadsheets = self._service("sheets", "v4").spreadsheets()
        created = spreadsheets.create(body={"properties": {"title": title}}).execute()
        spreadsheet_id = str(created["spreadsheetId"])
        return {
            "ref": f"{self.account_id}:{spreadsheet_id}",
            "title": title,
            "url": created.get("spreadsheetUrl") or f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
        }

    def update_sheet(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[Any]],
        value_input_option: ValueInputOption,
    ) -> dict:
        return (
            self._service("sheets", "v4")
            .spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption=value_input_option,
                body={"values": values},
            )
            .execute()
        )

    def append_sheet_rows(
        self,
        spreadsheet_id: str,
        range_name: str,
        rows: list[list[Any]],
        value_input_option: ValueInputOption,
    ) -> dict:
        return (
            self._service("sheets", "v4")
            .spreadsheets()
            .values()
            .append(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption=value_input_option,
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
            )
            .execute()
        )


class MultiGoogleDriveClient:
    def __init__(self, clients: list[GoogleDriveClient]):
        self.clients = {client.account_id: client for client in clients}

    def list_accounts(self) -> list[str]:
        return [client.email or client.account_id for client in self.clients.values()]

    def verify_connection(self) -> None:
        for client in self.clients.values():
            client.verify_connection()

    def search(
        self,
        query: str,
        *,
        kind: DriveKind = "all",
        account_id: str | None = None,
        limit: int = 20,
    ) -> list[DriveFile]:
        clients = [self._account(account_id)] if account_id else list(self.clients.values())
        results = [item for client in clients for item in client.search(query, kind=kind, limit=limit)]
        results.sort(key=lambda item: (item.modified_time or "", item.ref), reverse=True)
        return results[:limit]

    def _account(self, account_ref: str) -> GoogleDriveClient:
        if account_ref in self.clients:
            return self.clients[account_ref]
        normalized = account_ref.casefold()
        matches = [
            client
            for client in self.clients.values()
            if client.email and client.email.casefold() == normalized
        ]
        if len(matches) == 1:
            return matches[0]
        available = ", ".join(self.list_accounts())
        raise ValueError(f"Unknown Google account: {account_ref}. Available: {available}")

    def select_account(self, account_id: str | None = None) -> GoogleDriveClient:
        if account_id:
            return self._account(account_id)
        if len(self.clients) == 1:
            return next(iter(self.clients.values()))
        raise ValueError(f"Specify a Google account by email: {', '.join(self.list_accounts())}")

    def resolve_ref(self, ref: str) -> tuple[GoogleDriveClient, str]:
        if ":" in ref:
            account_id, file_id = ref.split(":", 1)
            if account_id in self.clients and file_id:
                return self.clients[account_id], file_id
        if len(self.clients) == 1 and ref:
            return next(iter(self.clients.values())), ref
        raise ValueError("Use an account-qualified Google Drive reference")
