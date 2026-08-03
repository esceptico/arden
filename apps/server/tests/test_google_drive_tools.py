from types import SimpleNamespace

from arden.integrations.google_drive.tools import (
    DRIVE_TOOLS,
    DriveCreateDocInput,
    DriveCreateSheetInput,
    DriveReadDocInput,
    DriveReadSheetInput,
    drive_read_doc,
    drive_read_sheet,
)
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ToolAction


class Drive:
    def resolve_ref(self, ref):
        return self, ref.split(":", 1)[-1]

    def read_doc(self, document_id):
        return {
            "ref": f"acct:{document_id}",
            "title": "Roadmap",
            "text": "Plan",
            "revision_id": "rev-1",
            "url": "https://docs.google.com/document/d/doc-1/edit",
        }

    def read_sheet(self, spreadsheet_id, range_name):
        return {
            "ref": f"acct:{spreadsheet_id}",
            "range": range_name,
            "values": [["Name", "Count"], ["Ada", 2]],
            "url": "https://docs.google.com/spreadsheets/d/sheet-1/edit",
        }


def _execution():
    drive = Drive()
    ctx = SimpleNamespace(get_client=lambda _name, _type=None: drive)
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
