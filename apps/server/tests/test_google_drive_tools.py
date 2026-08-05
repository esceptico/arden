from types import SimpleNamespace

from arden.integrations.google_drive.client import DriveDocument, DriveFile, DriveSheetMutationReceipt, DriveSheetRange
from arden.integrations.google_drive.tools import (
    DRIVE_TOOLS,
    DriveCreateDocInput,
    DriveCreateSheetInput,
    DriveReadDocInput,
    DriveReadSheetInput,
    SheetWriteInput,
    drive_append_sheet_rows,
    drive_read_doc,
    drive_read_sheet,
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
        return DriveSheetMutationReceipt(acknowledged_range=range_name)

    def append_sheet_rows(self, _spreadsheet_id, range_name, _rows, _value_input_option):
        return DriveSheetMutationReceipt(acknowledged_range=range_name)

    @staticmethod
    def _file(file_id, name, kind):
        url_kind = "document" if kind == "document" else "spreadsheets"
        return DriveFile(
            ref=f"acct:{file_id}",
            account_id="acct",
            file_id=file_id,
            name=name,
            kind=kind,
            modified_time=None,
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


async def test_read_doc_returns_source_reference():
    result = await drive_read_doc(_execution(), DriveReadDocInput(document_ref="acct:doc-1"))

    assert result.content == "Plan"
    assert result.source_refs[0].provider == "google_drive"
    assert result.source_refs[0].ref == "acct:doc-1"


async def test_read_sheet_returns_compact_table_and_structured_rows():
    result = await drive_read_sheet(_execution(), DriveReadSheetInput(spreadsheet_ref="acct:sheet-1", range="A1:B2"))

    assert result.content == "Range: A1:B2\nName | Count\nAda | 2"
    assert result.data == {
        "range": "A1:B2",
        "values": [["Name", "Count"], ["Ada", 2]],
        "row_count": 2,
    }


async def test_sheet_mutations_consume_typed_receipts(tmp_path):
    args = SheetWriteInput(
        spreadsheet_ref="acct:sheet-1",
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
