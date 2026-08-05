from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from arden.integrations.google_drive.models import (
    DriveDocument,
    DriveFile,
    DriveFilePage,
    DrivePageRef,
    DriveSheetMutationReceipt,
    DriveSheetRange,
)
from arden.integrations.google_drive.tools import (
    DRIVE_TOOLS,
    DriveCreateDocInput,
    DriveCreateSheetInput,
    DriveEditDocInput,
    DriveReadDocInput,
    DriveReadSheetInput,
    DriveSearchInput,
    SheetWriteInput,
    drive_append_sheet_rows,
    drive_read_doc,
    drive_read_sheet,
    drive_search,
    drive_update_sheet,
)
from arden.integrations.mutations import IDEMPOTENCY_LEDGER_SERVICE, IdempotencyLedger
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ToolAction


class Drive:
    def resolve_ref(self, ref):
        return self, ref.split(":", 1)[-1]

    def read_doc(self, document_id):
        return DriveDocument(
            file=self._file(document_id, "Roadmap", "document"),
            text="Plan",
            revision_id="rev-1",
        )

    def read_sheet(self, spreadsheet_id, range_name):
        return DriveSheetRange(
            file=self._file(spreadsheet_id, f"Sheet {range_name}", "spreadsheet"),
            range_name=range_name,
            values=(("Name", "Count"), ("Ada", 2)),
        )

    def update_sheet(self, _spreadsheet_id, range_name, _values, _value_input_option):
        return DriveSheetMutationReceipt(
            file_ref="user@example.com:sheet-1",
            acknowledged_range=range_name,
        )

    def append_sheet_rows(self, _spreadsheet_id, range_name, _rows, _value_input_option):
        return DriveSheetMutationReceipt(
            file_ref="user@example.com:sheet-1",
            acknowledged_range=range_name,
        )

    def search(self, _query, *, kind, account_ref, limit, page_ref):
        assert kind == "all"
        assert account_ref is None
        assert limit == 20
        assert page_ref is None
        return DriveFilePage(
            files=(self._file("doc-1", "Roadmap", "document", modified=True),),
            next_page_ref=DrivePageRef(
                account_ref="user@example.com",
                provider_token="page-2",
                query="roadmap",
                kind="all",
                limit=20,
            ),
        )

    @staticmethod
    def _file(file_id, name, kind, *, modified=False):
        url_kind = "document" if kind == "document" else "spreadsheets"
        return DriveFile(
            ref=f"user@example.com:{file_id}",
            account_ref="user@example.com",
            file_id=file_id,
            name=name,
            kind=kind,
            modified_at=datetime(2026, 7, 20, tzinfo=UTC) if modified else None,
            url=f"https://docs.google.com/{url_kind}/d/{file_id}/edit",
        )


def _execution(tmp_path=None):
    drive = Drive()
    services = {}
    if tmp_path is not None:
        services[IDEMPOTENCY_LEDGER_SERVICE] = IdempotencyLedger(tmp_path / "idempotency.sqlite3")
    ctx = SimpleNamespace(get_client=lambda _name, _type=None: drive, services=services)
    return ToolExecution(tool_id="call-1", tool_name="drive_read_doc", ctx=ctx)


def test_drive_write_tools_require_approval():
    write_names = {
        "drive_create_doc",
        "drive_edit_doc",
        "drive_create_sheet",
        "drive_update_sheet",
        "drive_append_sheet_rows",
    }
    for name in write_names:
        registered = DRIVE_TOOLS[name]
        assert registered.policy.action == ToolAction.WRITE
        assert registered.policy.requires_approval is True
        assert registered.policy.permissions == frozenset({"google_drive"})


def test_create_inputs_do_not_accept_file_content():
    assert set(DriveCreateDocInput.model_fields) == {"title", "account", "idempotency_key"}
    assert set(DriveCreateSheetInput.model_fields) == {"title", "account", "idempotency_key"}


def test_create_tool_descriptions_require_a_followup_write():
    assert "empty" in DRIVE_TOOLS["drive_create_doc"].description.lower()
    assert "drive_edit_doc" in DRIVE_TOOLS["drive_create_doc"].description
    assert "empty" in DRIVE_TOOLS["drive_create_sheet"].description.lower()
    assert "drive_update_sheet" in DRIVE_TOOLS["drive_create_sheet"].description


def test_drive_search_description_explains_exact_continuation_and_safe_writes():
    description = DRIVE_TOOLS["drive_search"].description

    assert "only its exact page_ref" in description
    assert "idempotency_key" in description
    assert "approval preview" in description


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "roadmap", "limit": "20"},
        {"query": "roadmap", "unknown": True},
        {"query": "roadmap", "account": "not-an-email"},
    ],
)
def test_search_input_rejects_coercion_unknown_fields_and_invalid_accounts(payload):
    with pytest.raises(ValidationError):
        DriveSearchInput.model_validate(payload)


def test_search_input_requires_canonical_account_bound_page_ref():
    page_ref = DrivePageRef(
        account_ref="user@example.com",
        provider_token="page-2",
        query="roadmap",
        kind="all",
        limit=20,
    ).encode()

    assert DriveSearchInput(page_ref=page_ref).page_ref == page_ref
    with pytest.raises(ValidationError, match="exact Google Drive page reference"):
        DriveSearchInput(query="roadmap", page_ref="page-2")
    with pytest.raises(ValidationError, match="page_ref must be passed alone"):
        DriveSearchInput(
            query="roadmap",
            account="other@example.com",
            page_ref=page_ref,
        )


def test_file_inputs_reject_unqualified_refs():
    with pytest.raises(ValidationError, match="account-qualified"):
        DriveReadDocInput(document_ref="doc-1")
    with pytest.raises(ValidationError, match="account-qualified"):
        DriveReadSheetInput(spreadsheet_ref="sheet-1")


def test_write_inputs_reject_ignored_and_empty_mutations():
    with pytest.raises(ValidationError, match="append does not accept match"):
        DriveEditDocInput(
            document_ref="user@example.com:doc-1",
            operation="append",
            text="content",
            match="ignored",
            idempotency_key="drive-edit-1",
        )
    with pytest.raises(ValidationError, match="empty rows"):
        SheetWriteInput(
            spreadsheet_ref="user@example.com:sheet-1",
            range="A1",
            values=[[]],
            idempotency_key="drive-write-1",
        )


async def test_search_uses_typed_pagination_state():
    result = await drive_search(_execution(), DriveSearchInput(query="roadmap"))

    assert "Roadmap" in result.content
    assert result.data == {
        "count": 1,
        "next_page_ref": DrivePageRef(
            account_ref="user@example.com",
            provider_token="page-2",
            query="roadmap",
            kind="all",
            limit=20,
        ).encode(),
        "incomplete_search": False,
    }
    assert result.preview == "1 files (possibly capped)"
    assert f"Continue with page_ref: {result.data['next_page_ref']}" in result.content


async def test_search_keeps_an_empty_provider_page_chainable():
    class EmptyPageDrive(Drive):
        def search(self, *_args, **_kwargs):
            return DriveFilePage(
                files=(),
                next_page_ref=DrivePageRef(
                    account_ref="user@example.com",
                    provider_token="page-2",
                    query="roadmap",
                    kind="all",
                    limit=20,
                ),
            )

    execution = _execution()
    execution.ctx.get_client = lambda _name, _type=None: EmptyPageDrive()

    result = await drive_search(execution, DriveSearchInput(query="roadmap"))

    assert result.data["next_page_ref"] is not None
    assert f"Continue with page_ref: {result.data['next_page_ref']}" in result.content


async def test_read_doc_returns_source_reference():
    result = await drive_read_doc(
        _execution(), DriveReadDocInput(document_ref="user@example.com:doc-1")
    )

    assert result.content == "Plan"
    assert result.source_refs[0].provider == "google_drive"
    assert result.source_refs[0].ref == "user@example.com:doc-1"


async def test_read_sheet_returns_compact_table_and_structured_rows():
    result = await drive_read_sheet(
        _execution(),
        DriveReadSheetInput(spreadsheet_ref="user@example.com:sheet-1", range="A1:B2"),
    )

    assert result.content == "Range: A1:B2\nName | Count\nAda | 2"
    assert result.data == {
        "range": "A1:B2",
        "values": [["Name", "Count"], ["Ada", 2]],
        "row_count": 2,
    }


async def test_sheet_mutations_consume_typed_receipts(tmp_path):
    args = SheetWriteInput(
        spreadsheet_ref="user@example.com:sheet-1",
        range="A3:B3",
        values=[["Grace", 3]],
        idempotency_key="drive-write-1",
    )

    update = await drive_update_sheet(_execution(tmp_path), args)
    append = await drive_append_sheet_rows(
        _execution(tmp_path), args.model_copy(update={"idempotency_key": "drive-write-2"})
    )

    assert update.outcome is not None
    assert update.outcome.receipt == "A3:B3"
    assert update.outcome.verification is not None
    assert update.outcome.verification.observed == "Drive acknowledged A3:B3"
    assert append.outcome is not None
    assert append.outcome.receipt == "A3:B3"
    assert append.outcome.verification is not None
    assert append.outcome.verification.observed == "Drive acknowledged append to A3:B3"
