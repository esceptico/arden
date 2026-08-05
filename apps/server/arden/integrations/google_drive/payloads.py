"""Strict Google Drive and Sheets payload decoding."""

import math
from datetime import datetime

from arden.integrations.google_drive.errors import GoogleDrivePayloadError
from arden.integrations.google_drive.models import (
    DriveCell,
    DriveFile,
    DriveFilePage,
    DrivePageRef,
    DriveSheetMutationReceipt,
    DriveSheetRange,
    qualify_file_ref,
)

_DOC_MIME = "application/vnd.google-apps.document"
_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
_MAX_PAGE_TOKEN_CHARS = 4_096
_MAX_SHEET_ROWS = 10_000
_MAX_SHEET_CELLS = 100_000


def _object(
    raw: object,
    operation: str,
    *,
    allowed: frozenset[str],
    required: frozenset[str] = frozenset(),
) -> dict[str, object]:
    if type(raw) is not dict or any(not isinstance(key, str) for key in raw):
        raise GoogleDrivePayloadError(operation)
    payload = raw
    if set(payload) - allowed or required - set(payload):
        raise GoogleDrivePayloadError(operation)
    return payload


def _required_string(payload: dict[str, object], field: str, operation: str, *, max_chars: int) -> str:
    value = payload[field]
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > max_chars:
        raise GoogleDrivePayloadError(operation)
    return value


def _optional_page_token(payload: dict[str, object], operation: str) -> str | None:
    if "nextPageToken" not in payload:
        return None
    value = payload["nextPageToken"]
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\r" in value
        or "\n" in value
        or len(value) > _MAX_PAGE_TOKEN_CHARS
    ):
        raise GoogleDrivePayloadError(operation)
    return value


def _optional_items(payload: dict[str, object], field: str, operation: str) -> list[object]:
    if field not in payload:
        return []
    value = payload[field]
    if not isinstance(value, list):
        raise GoogleDrivePayloadError(operation)
    return value


def _optional_false(payload: dict[str, object], field: str, operation: str) -> bool:
    if field not in payload:
        return False
    value = payload[field]
    if not isinstance(value, bool):
        raise GoogleDrivePayloadError(operation)
    return value


def _timestamp(value: object, operation: str) -> datetime:
    if not isinstance(value, str) or not value or value != value.strip():
        raise GoogleDrivePayloadError(operation)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GoogleDrivePayloadError(operation) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GoogleDrivePayloadError(operation)
    return parsed


def decode_file_page(
    raw: object,
    account_ref: str,
    *,
    query: str,
    kind: str,
    limit: int,
) -> DriveFilePage:
    operation = "file search"
    payload = _object(
        raw,
        operation,
        allowed=frozenset({"files", "nextPageToken", "incompleteSearch"}),
    )
    raw_files = _optional_items(payload, "files", operation)
    if len(raw_files) > 100:
        raise GoogleDrivePayloadError(operation)

    files: list[DriveFile] = []
    for raw_file in raw_files:
        item = _object(
            raw_file,
            operation,
            allowed=frozenset({"id", "name", "mimeType", "modifiedTime", "webViewLink"}),
            required=frozenset({"id", "name", "mimeType", "modifiedTime", "webViewLink"}),
        )
        mime = _required_string(item, "mimeType", operation, max_chars=128)
        if mime not in {_DOC_MIME, _SHEET_MIME}:
            raise GoogleDrivePayloadError(operation)
        file_id = _required_string(item, "id", operation, max_chars=512)
        try:
            file = DriveFile(
                ref=qualify_file_ref(account_ref, file_id),
                account_ref=account_ref,
                file_id=file_id,
                name=_required_string(item, "name", operation, max_chars=1_024),
                kind="document" if mime == _DOC_MIME else "spreadsheet",
                modified_at=_timestamp(item["modifiedTime"], operation),
                url=_required_string(item, "webViewLink", operation, max_chars=4_096),
            )
        except ValueError as exc:
            raise GoogleDrivePayloadError(operation) from exc
        files.append(file)

    next_page_token = _optional_page_token(payload, operation)
    try:
        return DriveFilePage(
            files=tuple(files),
            next_page_ref=(
                DrivePageRef(
                    account_ref=account_ref,
                    provider_token=next_page_token,
                    query=query,
                    kind=kind,
                    limit=limit,
                )
                if next_page_token is not None
                else None
            ),
            incomplete_search=_optional_false(payload, "incompleteSearch", operation),
        )
    except ValueError as exc:
        raise GoogleDrivePayloadError(operation) from exc


def decode_connection_check(raw: object) -> None:
    operation = "connection verification"
    payload = _object(raw, operation, allowed=frozenset({"files"}))
    raw_files = _optional_items(payload, "files", operation)
    if len(raw_files) > 1:
        raise GoogleDrivePayloadError(operation)
    for raw_file in raw_files:
        item = _object(
            raw_file,
            operation,
            allowed=frozenset({"id"}),
            required=frozenset({"id"}),
        )
        _required_string(item, "id", operation, max_chars=512)


def _sheet_values(payload: dict[str, object], operation: str) -> tuple[tuple[DriveCell, ...], ...]:
    if "values" not in payload:
        return ()
    raw_rows = payload["values"]
    if not isinstance(raw_rows, list) or len(raw_rows) > _MAX_SHEET_ROWS:
        raise GoogleDrivePayloadError(operation)
    rows: list[tuple[DriveCell, ...]] = []
    cell_count = 0
    for raw_row in raw_rows:
        if not isinstance(raw_row, list):
            raise GoogleDrivePayloadError(operation)
        cells: list[DriveCell] = []
        for cell in raw_row:
            if cell is not None and type(cell) not in {str, int, float, bool}:
                raise GoogleDrivePayloadError(operation)
            if isinstance(cell, float) and not math.isfinite(cell):
                raise GoogleDrivePayloadError(operation)
            cells.append(cell)
        cell_count += len(cells)
        if cell_count > _MAX_SHEET_CELLS:
            raise GoogleDrivePayloadError(operation)
        rows.append(tuple(cells))
    return tuple(rows)


def decode_sheet_range(
    raw: object,
    account_ref: str,
    expected_spreadsheet_id: str,
) -> DriveSheetRange:
    operation = "spreadsheet read"
    payload = _object(
        raw,
        operation,
        allowed=frozenset({"range", "majorDimension", "values"}),
        required=frozenset({"range", "majorDimension"}),
    )
    if payload["majorDimension"] != "ROWS":
        raise GoogleDrivePayloadError(operation)
    returned_range = _required_string(payload, "range", operation, max_chars=512)
    try:
        file = DriveFile(
            ref=qualify_file_ref(account_ref, expected_spreadsheet_id),
            account_ref=account_ref,
            file_id=expected_spreadsheet_id,
            name=f"Sheet {returned_range}",
            kind="spreadsheet",
            modified_at=None,
            url=f"https://docs.google.com/spreadsheets/d/{expected_spreadsheet_id}/edit",
        )
        return DriveSheetRange(
            file=file,
            range_name=returned_range,
            values=_sheet_values(payload, operation),
        )
    except ValueError as exc:
        raise GoogleDrivePayloadError(operation) from exc


def decode_created_sheet(raw: object, account_ref: str, expected_title: str) -> DriveFile:
    operation = "spreadsheet creation"
    payload = _object(
        raw,
        operation,
        allowed=frozenset({"spreadsheetId", "spreadsheetUrl", "properties"}),
        required=frozenset({"spreadsheetId", "spreadsheetUrl", "properties"}),
    )
    properties = _object(
        payload["properties"],
        operation,
        allowed=frozenset({"title"}),
        required=frozenset({"title"}),
    )
    title = _required_string(properties, "title", operation, max_chars=1_024)
    if title != expected_title:
        raise GoogleDrivePayloadError(operation)
    spreadsheet_id = _required_string(payload, "spreadsheetId", operation, max_chars=512)
    try:
        return DriveFile(
            ref=qualify_file_ref(account_ref, spreadsheet_id),
            account_ref=account_ref,
            file_id=spreadsheet_id,
            name=title,
            kind="spreadsheet",
            modified_at=None,
            url=_required_string(payload, "spreadsheetUrl", operation, max_chars=4_096),
        )
    except ValueError as exc:
        raise GoogleDrivePayloadError(operation) from exc


def _sheet_receipt(
    *,
    spreadsheet_id: object,
    acknowledged_range: object,
    account_ref: str,
    expected_spreadsheet_id: str,
    operation: str,
) -> DriveSheetMutationReceipt:
    if (
        not isinstance(spreadsheet_id, str)
        or not spreadsheet_id
        or spreadsheet_id != spreadsheet_id.strip()
        or len(spreadsheet_id) > 512
    ):
        raise GoogleDrivePayloadError(operation)
    if spreadsheet_id != expected_spreadsheet_id:
        raise GoogleDrivePayloadError(operation)
    if (
        not isinstance(acknowledged_range, str)
        or not acknowledged_range
        or acknowledged_range != acknowledged_range.strip()
        or len(acknowledged_range) > 512
    ):
        raise GoogleDrivePayloadError(operation)
    try:
        return DriveSheetMutationReceipt(
            file_ref=qualify_file_ref(account_ref, spreadsheet_id),
            acknowledged_range=acknowledged_range,
        )
    except ValueError as exc:
        raise GoogleDrivePayloadError(operation) from exc


def decode_sheet_update(
    raw: object,
    account_ref: str,
    expected_spreadsheet_id: str,
) -> DriveSheetMutationReceipt:
    operation = "spreadsheet update"
    payload = _object(
        raw,
        operation,
        allowed=frozenset({"spreadsheetId", "updatedRange"}),
        required=frozenset({"spreadsheetId", "updatedRange"}),
    )
    return _sheet_receipt(
        spreadsheet_id=payload["spreadsheetId"],
        acknowledged_range=payload["updatedRange"],
        account_ref=account_ref,
        expected_spreadsheet_id=expected_spreadsheet_id,
        operation=operation,
    )


def decode_sheet_append(
    raw: object,
    account_ref: str,
    expected_spreadsheet_id: str,
) -> DriveSheetMutationReceipt:
    operation = "spreadsheet append"
    payload = _object(
        raw,
        operation,
        allowed=frozenset({"spreadsheetId", "updates"}),
        required=frozenset({"spreadsheetId", "updates"}),
    )
    updates = _object(
        payload["updates"],
        operation,
        allowed=frozenset({"updatedRange"}),
        required=frozenset({"updatedRange"}),
    )
    return _sheet_receipt(
        spreadsheet_id=payload["spreadsheetId"],
        acknowledged_range=updates["updatedRange"],
        account_ref=account_ref,
        expected_spreadsheet_id=expected_spreadsheet_id,
        operation=operation,
    )
