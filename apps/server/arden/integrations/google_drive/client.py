from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from googleapiclient.discovery import build

from arden.integrations.base import IntegrationOperationError
from arden.integrations.google_auth.auth import SCOPES_DRIVE, get_google_credentials
from arden.integrations.google_drive.render import flatten_google_doc

DriveKind = Literal["all", "doc", "sheet"]
DocEditOperation = Literal["append", "replace_all"]
ValueInputOption = Literal["RAW", "USER_ENTERED"]

_DOC_MIME = "application/vnd.google-apps.document"
_SHEET_MIME = "application/vnd.google-apps.spreadsheet"

type DriveCell = str | int | float | bool | None


class GoogleDrivePayloadError(IntegrationOperationError):
    """Google Drive returned a successful response that violates its contract."""

    def __init__(self, operation: str):
        super().__init__(
            code="invalid_provider_payload",
            safe_message=f"Google Drive returned an invalid response for {operation}.",
        )


@dataclass(frozen=True)
class DriveFile:
    ref: str
    account_id: str
    file_id: str
    name: str
    kind: Literal["document", "spreadsheet"]
    modified_time: str | None
    url: str


@dataclass(frozen=True)
class DriveDocument:
    file: DriveFile
    text: str
    revision_id: str


@dataclass(frozen=True)
class DriveSheetRange:
    file: DriveFile
    range_name: str
    values: tuple[tuple[DriveCell, ...], ...]


@dataclass(frozen=True)
class DriveSheetMutationReceipt:
    acknowledged_range: str


def _mapping(value: object, operation: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise GoogleDrivePayloadError(operation)
    return value


def _required_string(payload: dict[str, object], field: str, operation: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise GoogleDrivePayloadError(operation)
    return value


def _required_values(payload: dict[str, object], operation: str) -> tuple[tuple[DriveCell, ...], ...]:
    if "values" not in payload:
        # Sheets omits this field when the requested range contains no data.
        return ()
    raw_rows = payload["values"]
    if not isinstance(raw_rows, list):
        raise GoogleDrivePayloadError(operation)
    rows: list[tuple[DriveCell, ...]] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, list) or any(
            not isinstance(cell, (str, int, float, bool)) and cell is not None for cell in raw_row
        ):
            raise GoogleDrivePayloadError(operation)
        rows.append(tuple(raw_row))
    return tuple(rows)


def _sheet_mutation_receipt(payload: dict[str, object], operation: str) -> DriveSheetMutationReceipt:
    return DriveSheetMutationReceipt(acknowledged_range=_required_string(payload, "updatedRange", operation))


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
        response = _mapping(
            self._service("drive", "v3")
            .files()
            .list(
                q=q,
                pageSize=max(1, min(limit, 100)),
                orderBy="modifiedTime desc",
                fields="files(id,name,mimeType,modifiedTime,webViewLink)",
            )
            .execute(),
            "file search",
        )
        raw_files = response.get("files")
        if not isinstance(raw_files, list):
            raise GoogleDrivePayloadError("file search")
        out: list[DriveFile] = []
        for raw_item in raw_files:
            item = _mapping(raw_item, "file search")
            mime = _required_string(item, "mimeType", "file search")
            if mime not in {_DOC_MIME, _SHEET_MIME}:
                raise GoogleDrivePayloadError("file search")
            file_id = _required_string(item, "id", "file search")
            out.append(
                DriveFile(
                    ref=f"{self.account_id}:{file_id}",
                    account_id=self.account_id,
                    file_id=file_id,
                    name=_required_string(item, "name", "file search"),
                    kind="document" if mime == _DOC_MIME else "spreadsheet",
                    modified_time=_required_string(item, "modifiedTime", "file search"),
                    url=_required_string(item, "webViewLink", "file search"),
                )
            )
        return out

    def verify_connection(self) -> None:
        self._service("drive", "v3").files().list(pageSize=1, fields="files(id)").execute()

    def read_doc(self, document_id: str) -> DriveDocument:
        document = _mapping(
            self._service("docs", "v1").documents().get(documentId=document_id, includeTabsContent=True).execute(),
            "document read",
        )
        return DriveDocument(
            file=DriveFile(
                ref=f"{self.account_id}:{document_id}",
                account_id=self.account_id,
                file_id=document_id,
                name=_required_string(document, "title", "document read"),
                kind="document",
                modified_time=None,
                url=f"https://docs.google.com/document/d/{document_id}/edit",
            ),
            text=flatten_google_doc(document),
            revision_id=_required_string(document, "revisionId", "document read"),
        )

    def create_doc(self, title: str) -> DriveFile:
        documents = self._service("docs", "v1").documents()
        created = _mapping(documents.create(body={"title": title}).execute(), "document creation")
        document_id = _required_string(created, "documentId", "document creation")
        return DriveFile(
            ref=f"{self.account_id}:{document_id}",
            account_id=self.account_id,
            file_id=document_id,
            name=title,
            kind="document",
            modified_time=None,
            url=f"https://docs.google.com/document/d/{document_id}/edit",
        )

    def edit_doc(self, document_id: str, *, operation: DocEditOperation, text: str, match: str | None) -> DriveFile:
        documents = self._service("docs", "v1").documents()
        current = _mapping(documents.get(documentId=document_id, includeTabsContent=True).execute(), "document edit")
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
        body["writeControl"] = {"requiredRevisionId": _required_string(current, "revisionId", "document edit")}
        documents.batchUpdate(documentId=document_id, body=body).execute()
        return DriveFile(
            ref=f"{self.account_id}:{document_id}",
            account_id=self.account_id,
            file_id=document_id,
            name=_required_string(current, "title", "document edit"),
            kind="document",
            modified_time=None,
            url=f"https://docs.google.com/document/d/{document_id}/edit",
        )

    def read_sheet(self, spreadsheet_id: str, range_name: str) -> DriveSheetRange:
        values = _mapping(
            self._service("sheets", "v4")
            .spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_name)
            .execute(),
            "spreadsheet read",
        )
        returned_range = _required_string(values, "range", "spreadsheet read")
        return DriveSheetRange(
            file=DriveFile(
                ref=f"{self.account_id}:{spreadsheet_id}",
                account_id=self.account_id,
                file_id=spreadsheet_id,
                name=f"Sheet {returned_range}",
                kind="spreadsheet",
                modified_time=None,
                url=f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
            ),
            range_name=returned_range,
            values=_required_values(values, "spreadsheet read"),
        )

    def create_sheet(self, title: str) -> DriveFile:
        spreadsheets = self._service("sheets", "v4").spreadsheets()
        created = _mapping(spreadsheets.create(body={"properties": {"title": title}}).execute(), "spreadsheet creation")
        spreadsheet_id = _required_string(created, "spreadsheetId", "spreadsheet creation")
        return DriveFile(
            ref=f"{self.account_id}:{spreadsheet_id}",
            account_id=self.account_id,
            file_id=spreadsheet_id,
            name=title,
            kind="spreadsheet",
            modified_time=None,
            url=_required_string(created, "spreadsheetUrl", "spreadsheet creation"),
        )

    def update_sheet(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[Any]],
        value_input_option: ValueInputOption,
    ) -> DriveSheetMutationReceipt:
        response = _mapping(
            self._service("sheets", "v4")
            .spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption=value_input_option,
                body={"values": values},
            )
            .execute(),
            "spreadsheet update",
        )
        return _sheet_mutation_receipt(response, "spreadsheet update")

    def append_sheet_rows(
        self,
        spreadsheet_id: str,
        range_name: str,
        rows: list[list[Any]],
        value_input_option: ValueInputOption,
    ) -> DriveSheetMutationReceipt:
        response = _mapping(
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
            .execute(),
            "spreadsheet append",
        )
        return _sheet_mutation_receipt(_mapping(response.get("updates"), "spreadsheet append"), "spreadsheet append")


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
        matches = [client for client in self.clients.values() if client.email and client.email.casefold() == normalized]
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
        raise ValueError("Use an account-qualified Google Drive reference")
