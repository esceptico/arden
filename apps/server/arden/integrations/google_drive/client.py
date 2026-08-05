"""Google Drive transport clients with a strict typed provider boundary."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build

from arden.integrations.base import (
    IntegrationConnectionError,
    IntegrationOperationError,
)
from arden.integrations.google_auth.accounts import validate_google_account_email
from arden.integrations.google_auth.auth import SCOPES_DRIVE, get_google_credentials
from arden.integrations.google_drive.document_payloads import (
    decode_created_document,
    decode_document,
    decode_document_mutation,
    decode_document_state,
)
from arden.integrations.google_drive.errors import (
    KNOWN_GOOGLE_DRIVE_ERRORS,
    raise_google_drive_error,
)
from arden.integrations.google_drive.models import (
    DocEditOperation,
    DriveCell,
    DriveDocument,
    DriveFile,
    DriveFilePage,
    DriveKind,
    DrivePageRef,
    DriveSheetMutationReceipt,
    DriveSheetRange,
    ValueInputOption,
    split_file_ref,
)
from arden.integrations.google_drive.payloads import (
    decode_connection_check,
    decode_created_sheet,
    decode_file_page,
    decode_sheet_append,
    decode_sheet_range,
    decode_sheet_update,
)

_DOC_MIME = "application/vnd.google-apps.document"
_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
_PARAGRAPH_FIELDS = "paragraph(elements(textRun(content)))"
_TABLE_FIELDS = "table(tableRows(tableCells(content(paragraph(elements(textRun(content)))))))"
_TABLE_OF_CONTENTS_FIELDS = "tableOfContents(content(paragraph(elements(textRun(content)))))"
_SECTION_BREAK_FIELDS = "sectionBreak(sectionStyle(sectionType))"
_BODY_FIELDS = (
    f"body(content({_PARAGRAPH_FIELDS},{_TABLE_FIELDS},"
    f"{_TABLE_OF_CONTENTS_FIELDS},{_SECTION_BREAK_FIELDS}))"
)
_TAB_DOCUMENT_FIELDS = f"documentTab({_BODY_FIELDS})"
_TAB_LEVEL_THREE_FIELDS = _TAB_DOCUMENT_FIELDS
_TAB_LEVEL_TWO_FIELDS = f"{_TAB_DOCUMENT_FIELDS},childTabs({_TAB_LEVEL_THREE_FIELDS})"
_TAB_LEVEL_ONE_FIELDS = f"{_TAB_DOCUMENT_FIELDS},childTabs({_TAB_LEVEL_TWO_FIELDS})"
_DOCUMENT_CONTENT_FIELDS = f"documentId,title,revisionId,tabs({_TAB_LEVEL_ONE_FIELDS})"


def _limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 100:
        raise ValueError("Google Drive search limit must be an integer from 1 to 100")
    return value


class GoogleDriveClient:
    def __init__(
        self,
        token_path: Path,
        account_ref: str,
        *,
        build_service: Callable[..., Any] = build,
        credentials: Any | None = None,
    ):
        try:
            self.account_ref = validate_google_account_email(account_ref)
        except ValueError as exc:
            raise ValueError("Google Drive account reference must be an exact email address") from exc
        self.token_path = token_path
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

    def _execute(self, operation: str, request: Callable[[], Any]) -> object:
        try:
            return request().execute()
        except (IntegrationConnectionError, IntegrationOperationError):
            raise
        except KNOWN_GOOGLE_DRIVE_ERRORS as exc:
            raise_google_drive_error(exc, account_ref=self.account_ref)

    @staticmethod
    def _escape_query(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    def search(
        self,
        query: str,
        *,
        kind: DriveKind = "all",
        limit: int = 20,
        page_token: str | None = None,
    ) -> DriveFilePage:
        if not isinstance(query, str):
            raise ValueError("Google Drive query must be a string")
        if query and query != query.strip():
            raise ValueError("Google Drive query must not have surrounding whitespace")
        if kind not in {"all", "doc", "sheet"}:
            raise ValueError("Google Drive search kind is invalid")
        page_size = _limit(limit)
        mime_types = {
            "doc": (_DOC_MIME,),
            "sheet": (_SHEET_MIME,),
            "all": (_DOC_MIME, _SHEET_MIME),
        }[kind]
        mime_query = " or ".join(f"mimeType = '{mime}'" for mime in mime_types)
        drive_query = f"trashed = false and ({mime_query})"
        if query:
            drive_query += f" and fullText contains '{self._escape_query(query)}'"
        request: dict[str, object] = {
            "q": drive_query,
            "pageSize": page_size,
            "orderBy": "modifiedTime desc",
            "fields": "nextPageToken,incompleteSearch,files(id,name,mimeType,modifiedTime,webViewLink)",
        }
        if page_token is not None:
            DrivePageRef(
                account_ref=self.account_ref,
                provider_token=page_token,
                query=query,
                kind=kind,
                limit=page_size,
            )
            request["pageToken"] = page_token
        raw = self._execute(
            "file search",
            lambda: self._service("drive", "v3").files().list(**request),
        )
        page = decode_file_page(
            raw,
            self.account_ref,
            query=query,
            kind=kind,
            limit=page_size,
        )
        if page.next_page_ref is not None and page.next_page_ref.provider_token == page_token:
            raise IntegrationOperationError(
                code="invalid_provider_payload",
                safe_message="Google Drive returned the current page_ref again.",
            )
        return page

    def verify_connection(self) -> None:
        raw = self._execute(
            "connection verification",
            lambda: self._service("drive", "v3").files().list(pageSize=1, fields="files(id)"),
        )
        decode_connection_check(raw)

    def read_doc(self, document_id: str) -> DriveDocument:
        raw = self._execute(
            "document read",
            lambda: self._service("docs", "v1")
            .documents()
            .get(
                documentId=document_id,
                includeTabsContent=True,
                fields=_DOCUMENT_CONTENT_FIELDS,
            ),
        )
        return decode_document(raw, self.account_ref, document_id)

    def create_doc(self, title: str) -> DriveFile:
        raw = self._execute(
            "document creation",
            lambda: self._service("docs", "v1")
            .documents()
            .create(body={"title": title}, fields="documentId,title"),
        )
        return decode_created_document(raw, self.account_ref, title)

    def edit_doc(
        self,
        document_id: str,
        *,
        operation: DocEditOperation,
        text: str,
        match: str | None,
    ) -> DriveFile:
        raw_current = self._execute(
            "document edit",
            lambda: self._service("docs", "v1").documents().get(
                documentId=document_id,
                fields="documentId,title,revisionId",
            ),
        )
        current = decode_document_state(
            raw_current,
            self.account_ref,
            document_id,
            operation="document edit",
        )
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
        body = {
            "requests": requests,
            "writeControl": {"requiredRevisionId": current.revision_id},
        }
        raw_receipt = self._execute(
            "document edit",
            lambda: self._service("docs", "v1").documents().batchUpdate(
                documentId=document_id,
                body=body,
                fields="documentId",
            ),
        )
        decode_document_mutation(raw_receipt, document_id)
        return current.file

    def read_sheet(self, spreadsheet_id: str, range_name: str) -> DriveSheetRange:
        raw = self._execute(
            "spreadsheet read",
            lambda: self._service("sheets", "v4")
            .spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                majorDimension="ROWS",
                fields="range,majorDimension,values",
            ),
        )
        return decode_sheet_range(raw, self.account_ref, spreadsheet_id)

    def create_sheet(self, title: str) -> DriveFile:
        raw = self._execute(
            "spreadsheet creation",
            lambda: self._service("sheets", "v4")
            .spreadsheets()
            .create(
                body={"properties": {"title": title}},
                fields="spreadsheetId,spreadsheetUrl,properties(title)",
            ),
        )
        return decode_created_sheet(raw, self.account_ref, title)

    def update_sheet(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[DriveCell]],
        value_input_option: ValueInputOption,
    ) -> DriveSheetMutationReceipt:
        raw = self._execute(
            "spreadsheet update",
            lambda: self._service("sheets", "v4")
            .spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption=value_input_option,
                body={"values": values},
                fields="spreadsheetId,updatedRange",
            ),
        )
        return decode_sheet_update(raw, self.account_ref, spreadsheet_id)

    def append_sheet_rows(
        self,
        spreadsheet_id: str,
        range_name: str,
        rows: list[list[DriveCell]],
        value_input_option: ValueInputOption,
    ) -> DriveSheetMutationReceipt:
        raw = self._execute(
            "spreadsheet append",
            lambda: self._service("sheets", "v4")
            .spreadsheets()
            .values()
            .append(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption=value_input_option,
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
                fields="spreadsheetId,updates(updatedRange)",
            ),
        )
        return decode_sheet_append(raw, self.account_ref, spreadsheet_id)


class MultiGoogleDriveClient:
    def __init__(self, clients: list[GoogleDriveClient]):
        account_keys = [client.account_ref.casefold() for client in clients]
        if len(account_keys) != len(set(account_keys)):
            raise IntegrationOperationError(
                code="configuration_error",
                safe_message="Google Drive account configuration contains a duplicate account.",
            )
        self._clients = {client.account_ref: client for client in clients}

    def _require_clients(self) -> None:
        if not self._clients:
            raise IntegrationConnectionError(
                integration_id="google_drive",
                reason="not_configured",
                detail="No Google Drive account is connected.",
            )

    def list_accounts(self) -> tuple[str, ...]:
        return tuple(self._clients)

    def verify_connection(self) -> None:
        self._require_clients()
        for client in self._clients.values():
            client.verify_connection()

    def verify_account(self, account_ref: str) -> None:
        self._account(account_ref).verify_connection()

    def search(
        self,
        query: str,
        *,
        kind: DriveKind = "all",
        account_ref: str | None = None,
        limit: int = 20,
        page_ref: str | None = None,
    ) -> DriveFilePage:
        self._require_clients()
        page = DrivePageRef.decode(page_ref) if page_ref is not None else None
        if page is not None and account_ref is not None and page.account_ref != account_ref:
            raise IntegrationOperationError(
                code="invalid_ref",
                safe_message="Google Drive page_ref belongs to a different account.",
            )
        client = self.select_account(page.account_ref if page is not None else account_ref)
        return client.search(
            page.query if page is not None else query,
            kind=page.kind if page is not None else kind,
            limit=page.limit if page is not None else _limit(limit),
            page_token=page.provider_token if page is not None else None,
        )

    def _account(self, account_ref: str) -> GoogleDriveClient:
        client = self._clients.get(account_ref)
        if client is not None:
            return client
        available = ", ".join(self.list_accounts())
        raise IntegrationOperationError(
            code="invalid_ref",
            safe_message=f"Google Drive account not found: {account_ref}. Available: {available}",
        )

    def select_account(self, account_ref: str | None = None) -> GoogleDriveClient:
        self._require_clients()
        if account_ref is not None:
            return self._account(account_ref)
        if len(self._clients) == 1:
            return next(iter(self._clients.values()))
        raise IntegrationOperationError(
            code="invalid_ref",
            safe_message=f"Specify a Google Drive account by exact email: {', '.join(self.list_accounts())}",
        )

    def resolve_ref(self, ref: str) -> tuple[GoogleDriveClient, str]:
        self._require_clients()
        try:
            account_ref, file_id = split_file_ref(ref)
        except ValueError as exc:
            raise IntegrationOperationError(
                code="invalid_ref",
                safe_message="Google Drive file references must be exact and account-qualified.",
            ) from exc
        return self._account(account_ref), file_id
