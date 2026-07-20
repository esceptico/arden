from types import SimpleNamespace

from ntrp.integrations.google_drive.tools import (
    DRIVE_TOOLS,
    CreateGoogleDocInput,
    CreateGoogleSheetInput,
    ReadGoogleDocInput,
    read_google_doc,
)
from ntrp.tools.core.context import ToolExecution
from ntrp.tools.core.types import ToolAction


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


def _execution():
    drive = Drive()
    ctx = SimpleNamespace(get_client=lambda _name, _type=None: drive)
    return ToolExecution(tool_id="call-1", tool_name="read_google_doc", ctx=ctx)


def test_drive_write_tools_require_approval():
    write_names = {
        "create_google_doc",
        "edit_google_doc",
        "create_google_sheet",
        "update_google_sheet",
        "append_google_sheet_rows",
    }
    for name in write_names:
        registered = DRIVE_TOOLS[name]
        assert registered.policy.action == ToolAction.WRITE
        assert registered.policy.requires_approval is True
        assert registered.policy.permissions == frozenset({"google_drive"})


def test_create_inputs_do_not_accept_file_content():
    assert set(CreateGoogleDocInput.model_fields) == {"title", "account", "idempotency_key"}
    assert set(CreateGoogleSheetInput.model_fields) == {"title", "account", "idempotency_key"}


def test_create_tool_descriptions_require_a_followup_write():
    assert "empty" in DRIVE_TOOLS["create_google_doc"].description.lower()
    assert "edit_google_doc" in DRIVE_TOOLS["create_google_doc"].description
    assert "empty" in DRIVE_TOOLS["create_google_sheet"].description.lower()
    assert "update_google_sheet" in DRIVE_TOOLS["create_google_sheet"].description


async def test_read_doc_returns_source_reference():
    result = await read_google_doc(_execution(), ReadGoogleDocInput(document_ref="acct:doc-1"))

    assert result.content == "Plan"
    assert result.source_refs[0].provider == "google_drive"
    assert result.source_refs[0].ref == "acct:doc-1"
