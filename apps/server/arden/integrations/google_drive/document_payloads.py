"""Strict Google Docs payload decoding."""

from dataclasses import dataclass

from arden.integrations.google_drive.errors import GoogleDrivePayloadError
from arden.integrations.google_drive.models import (
    DriveDocument,
    DriveDocumentState,
    DriveFile,
    qualify_file_ref,
)

_MAX_DOCUMENT_NODES = 100_000
_MAX_DOCUMENT_TEXT_CHARS = 5_000_000


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


def _optional_children(payload: dict[str, object], operation: str) -> list[object]:
    if "childTabs" not in payload:
        return []
    value = payload["childTabs"]
    if not isinstance(value, list):
        raise GoogleDrivePayloadError(operation)
    return value


@dataclass(slots=True)
class _DocumentBudget:
    nodes: int = 0
    text_chars: int = 0

    def enter(self, operation: str) -> None:
        self.nodes += 1
        if self.nodes > _MAX_DOCUMENT_NODES:
            raise GoogleDrivePayloadError(operation)

    def text(self, value: str, operation: str) -> str:
        self.text_chars += len(value)
        if self.text_chars > _MAX_DOCUMENT_TEXT_CHARS:
            raise GoogleDrivePayloadError(operation)
        return value


def _paragraph_text(raw: object, operation: str, budget: _DocumentBudget) -> str:
    paragraph = _object(
        raw,
        operation,
        allowed=frozenset({"elements"}),
        required=frozenset({"elements"}),
    )
    elements = paragraph["elements"]
    if not isinstance(elements, list):
        raise GoogleDrivePayloadError(operation)
    chunks: list[str] = []
    for raw_element in elements:
        budget.enter(operation)
        element = _object(raw_element, operation, allowed=frozenset({"textRun"}))
        if "textRun" not in element:
            continue
        text_run = _object(
            element["textRun"],
            operation,
            allowed=frozenset({"content"}),
            required=frozenset({"content"}),
        )
        content = text_run["content"]
        if not isinstance(content, str):
            raise GoogleDrivePayloadError(operation)
        chunks.append(budget.text(content, operation))
    return "".join(chunks)


def _structural_text(raw_elements: object, operation: str, budget: _DocumentBudget) -> str:
    if not isinstance(raw_elements, list):
        raise GoogleDrivePayloadError(operation)
    chunks: list[str] = []
    for raw_element in raw_elements:
        budget.enter(operation)
        element = _object(
            raw_element,
            operation,
            allowed=frozenset({"paragraph", "table", "tableOfContents", "sectionBreak"}),
        )
        if len(element) != 1:
            raise GoogleDrivePayloadError(operation)
        if "paragraph" in element:
            chunks.append(_paragraph_text(element["paragraph"], operation, budget))
            continue
        if "tableOfContents" in element:
            toc = _object(
                element["tableOfContents"],
                operation,
                allowed=frozenset({"content"}),
                required=frozenset({"content"}),
            )
            chunks.append(_structural_text(toc["content"], operation, budget))
            continue
        if "sectionBreak" in element:
            section_break = _object(
                element["sectionBreak"],
                operation,
                allowed=frozenset({"sectionStyle"}),
            )
            if "sectionStyle" in section_break:
                section_style = _object(
                    section_break["sectionStyle"],
                    operation,
                    allowed=frozenset({"sectionType"}),
                )
                if "sectionType" in section_style and section_style["sectionType"] not in {
                    "SECTION_TYPE_UNSPECIFIED",
                    "CONTINUOUS",
                    "NEXT_PAGE",
                }:
                    raise GoogleDrivePayloadError(operation)
            continue

        table = _object(
            element["table"],
            operation,
            allowed=frozenset({"tableRows"}),
            required=frozenset({"tableRows"}),
        )
        raw_rows = table["tableRows"]
        if not isinstance(raw_rows, list):
            raise GoogleDrivePayloadError(operation)
        rows: list[str] = []
        for raw_row in raw_rows:
            budget.enter(operation)
            row = _object(
                raw_row,
                operation,
                allowed=frozenset({"tableCells"}),
                required=frozenset({"tableCells"}),
            )
            raw_cells = row["tableCells"]
            if not isinstance(raw_cells, list):
                raise GoogleDrivePayloadError(operation)
            cells: list[str] = []
            for raw_cell in raw_cells:
                budget.enter(operation)
                cell = _object(
                    raw_cell,
                    operation,
                    allowed=frozenset({"content"}),
                    required=frozenset({"content"}),
                )
                cell_text = _structural_text(cell["content"], operation, budget)
                cells.append(cell_text.strip().replace("\n", " "))
            rows.append("\t".join(cells))
        chunks.append("\n".join(rows))
    return "".join(chunks)


def _tab_text_parts(
    raw: object,
    operation: str,
    budget: _DocumentBudget,
    *,
    depth: int,
) -> list[str]:
    budget.enter(operation)
    tab = _object(
        raw,
        operation,
        allowed=frozenset({"documentTab", "childTabs"}),
        required=frozenset({"documentTab"}),
    )
    document_tab = _object(
        tab["documentTab"],
        operation,
        allowed=frozenset({"body"}),
        required=frozenset({"body"}),
    )
    body = _object(
        document_tab["body"],
        operation,
        allowed=frozenset({"content"}),
        required=frozenset({"content"}),
    )
    text = _structural_text(body["content"], operation, budget).strip()
    parts = [text] if text else []
    children = _optional_children(tab, operation)
    if len(children) > 100 or (depth == 3 and children):
        raise GoogleDrivePayloadError(operation)
    for child in children:
        parts.extend(_tab_text_parts(child, operation, budget, depth=depth + 1))
    return parts


def _document_file(
    payload: dict[str, object],
    account_ref: str,
    expected_document_id: str,
    operation: str,
) -> DriveFile:
    document_id = _required_string(payload, "documentId", operation, max_chars=512)
    if document_id != expected_document_id:
        raise GoogleDrivePayloadError(operation)
    try:
        return DriveFile(
            ref=qualify_file_ref(account_ref, document_id),
            account_ref=account_ref,
            file_id=document_id,
            name=_required_string(payload, "title", operation, max_chars=1_024),
            kind="document",
            modified_at=None,
            url=f"https://docs.google.com/document/d/{document_id}/edit",
        )
    except ValueError as exc:
        raise GoogleDrivePayloadError(operation) from exc


def decode_document_state(
    raw: object,
    account_ref: str,
    expected_document_id: str,
    *,
    operation: str = "document metadata",
) -> DriveDocumentState:
    payload = _object(
        raw,
        operation,
        allowed=frozenset({"documentId", "title", "revisionId"}),
        required=frozenset({"documentId", "title", "revisionId"}),
    )
    file = _document_file(payload, account_ref, expected_document_id, operation)
    revision_id = _required_string(payload, "revisionId", operation, max_chars=512)
    try:
        return DriveDocumentState(file=file, revision_id=revision_id)
    except ValueError as exc:
        raise GoogleDrivePayloadError(operation) from exc


def decode_created_document(raw: object, account_ref: str, expected_title: str) -> DriveFile:
    operation = "document creation"
    payload = _object(
        raw,
        operation,
        allowed=frozenset({"documentId", "title"}),
        required=frozenset({"documentId", "title"}),
    )
    document_id = _required_string(payload, "documentId", operation, max_chars=512)
    if payload["title"] != expected_title:
        raise GoogleDrivePayloadError(operation)
    return _document_file(payload, account_ref, document_id, operation)


def decode_document(raw: object, account_ref: str, expected_document_id: str) -> DriveDocument:
    operation = "document read"
    payload = _object(
        raw,
        operation,
        allowed=frozenset({"documentId", "title", "revisionId", "tabs"}),
        required=frozenset({"documentId", "title", "revisionId", "tabs"}),
    )
    raw_tabs = payload["tabs"]
    if not isinstance(raw_tabs, list) or not raw_tabs or len(raw_tabs) > 100:
        raise GoogleDrivePayloadError(operation)
    budget = _DocumentBudget()
    parts: list[str] = []
    for raw_tab in raw_tabs:
        parts.extend(_tab_text_parts(raw_tab, operation, budget, depth=1))
    file = _document_file(payload, account_ref, expected_document_id, operation)
    revision_id = _required_string(payload, "revisionId", operation, max_chars=512)
    try:
        return DriveDocument(file=file, text="\n\n".join(parts), revision_id=revision_id)
    except ValueError as exc:
        raise GoogleDrivePayloadError(operation) from exc


def decode_document_mutation(raw: object, expected_document_id: str) -> None:
    operation = "document edit"
    payload = _object(
        raw,
        operation,
        allowed=frozenset({"documentId"}),
        required=frozenset({"documentId"}),
    )
    if _required_string(payload, "documentId", operation, max_chars=512) != expected_document_id:
        raise GoogleDrivePayloadError(operation)
