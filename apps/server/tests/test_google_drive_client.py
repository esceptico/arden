from pathlib import Path

import pytest

from arden.integrations.google_drive.client import GoogleDriveClient, GoogleDrivePayloadError, MultiGoogleDriveClient
from arden.integrations.google_drive.render import flatten_google_doc


class Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class Files:
    def __init__(self):
        self.kwargs = None

    def list(self, **kwargs):
        self.kwargs = kwargs
        return Request(
            {
                "files": [
                    {
                        "id": "doc-1",
                        "name": "Roadmap",
                        "mimeType": "application/vnd.google-apps.document",
                        "modifiedTime": "2026-07-20T10:00:00Z",
                        "webViewLink": "https://docs.google.com/document/d/doc-1/edit",
                    }
                ]
            }
        )


class DriveService:
    def __init__(self):
        self.resource = Files()

    def files(self):
        return self.resource


class Documents:
    def __init__(self):
        self.batch_body = None
        self.created_body = None

    def create(self, *, body):
        self.created_body = body
        return Request({"documentId": "doc-new"})

    def get(self, **_kwargs):
        return Request(
            {
                "documentId": "doc-1",
                "title": "Roadmap",
                "revisionId": "rev-7",
                "body": {"content": [{"endIndex": 5, "paragraph": {"elements": [{"textRun": {"content": "Old\n"}}]}}]},
            }
        )

    def batchUpdate(self, *, documentId, body):
        assert documentId == "doc-1"
        self.batch_body = body
        return Request({})


class DocsService:
    def __init__(self):
        self.resource = Documents()

    def documents(self):
        return self.resource


class Values:
    def __init__(self):
        self.updated_range = None
        self.body = None

    def update(self, *, spreadsheetId, range, valueInputOption, body):
        assert spreadsheetId == "sheet-1"
        assert valueInputOption == "USER_ENTERED"
        self.updated_range = range
        self.body = body
        return Request({"updatedRange": range})

    def get(self, *, spreadsheetId, range):
        assert spreadsheetId == "sheet-1"
        return Request({"range": range, "values": [["Name", "Count"], ["Ada", 2]]})

    def append(self, *, spreadsheetId, range, valueInputOption, insertDataOption, body):
        assert spreadsheetId == "sheet-1"
        assert insertDataOption == "INSERT_ROWS"
        return Request({"updates": {"updatedRange": range}})


class Spreadsheets:
    def __init__(self):
        self.values_resource = Values()
        self.created_body = None

    def create(self, *, body):
        self.created_body = body
        return Request({"spreadsheetId": "sheet-new", "spreadsheetUrl": "https://sheets.test/sheet-new"})

    def values(self):
        return self.values_resource


class SheetsService:
    def __init__(self):
        self.resource = Spreadsheets()

    def spreadsheets(self):
        return self.resource


def _client():
    services = {
        ("drive", "v3"): DriveService(),
        ("docs", "v1"): DocsService(),
        ("sheets", "v4"): SheetsService(),
    }

    def build_service(api, version, **_kwargs):
        return services[(api, version)]

    client = GoogleDriveClient(
        token_path=Path("token.json"),
        account_id="acct-1",
        email="user@example.com",
        build_service=build_service,
        credentials=object(),
    )
    return client, services


def test_search_filters_workspace_files_and_qualifies_refs():
    client, services = _client()

    results = client.search("roadmap", kind="doc", limit=10)

    assert [item.ref for item in results] == ["acct-1:doc-1"]
    assert "application/vnd.google-apps.document" in services[("drive", "v3")].resource.kwargs["q"]
    assert services[("drive", "v3")].resource.kwargs["pageSize"] == 10


def test_flatten_google_doc_preserves_paragraphs_and_tables():
    payload = {
        "body": {
            "content": [
                {"paragraph": {"elements": [{"textRun": {"content": "Heading\n"}}]}},
                {
                    "table": {
                        "tableRows": [
                            {
                                "tableCells": [
                                    {"content": [{"paragraph": {"elements": [{"textRun": {"content": "A"}}]}}]},
                                    {"content": [{"paragraph": {"elements": [{"textRun": {"content": "B"}}]}}]},
                                ]
                            }
                        ]
                    }
                },
            ]
        }
    }

    assert flatten_google_doc(payload) == "Heading\nA\tB"


def test_edit_doc_uses_required_revision_control():
    client, services = _client()

    client.edit_doc("doc-1", operation="append", text="Next", match=None)

    body = services[("docs", "v1")].resource.batch_body
    assert body["writeControl"] == {"requiredRevisionId": "rev-7"}
    assert body["requests"] == [{"insertText": {"endOfSegmentLocation": {}, "text": "Next"}}]


def test_sheet_update_writes_only_requested_range():
    client, services = _client()

    receipt = client.update_sheet("sheet-1", "Data!A2:B2", [["a", 2]], "USER_ENTERED")

    values = services[("sheets", "v4")].resource.values_resource
    assert values.updated_range == "Data!A2:B2"
    assert values.body == {"values": [["a", 2]]}
    assert receipt.acknowledged_range == "Data!A2:B2"


def test_create_doc_creates_empty_file_without_content_write():
    client, services = _client()

    result = client.create_doc("Empty document")

    documents = services[("docs", "v1")].resource
    assert documents.created_body == {"title": "Empty document"}
    assert documents.batch_body is None
    assert result.ref == "acct-1:doc-new"


def test_create_sheet_creates_empty_file_without_values_write():
    client, services = _client()

    result = client.create_sheet("Empty sheet")

    spreadsheets = services[("sheets", "v4")].resource
    assert spreadsheets.created_body == {"properties": {"title": "Empty sheet"}}
    assert spreadsheets.values_resource.body is None
    assert result.ref == "acct-1:sheet-new"


def test_client_parses_provider_responses_into_immutable_envelopes():
    client, _ = _client()

    document = client.read_doc("doc-1")
    sheet_range = client.read_sheet("sheet-1", "A1:B2")
    append_receipt = client.append_sheet_rows("sheet-1", "A3:B3", [["Grace", 3]], "USER_ENTERED")

    assert document.file.name == "Roadmap"
    assert document.text == "Old"
    assert sheet_range.range_name == "A1:B2"
    assert sheet_range.values == (("Name", "Count"), ("Ada", 2))
    assert append_receipt.acknowledged_range == "A3:B3"


def test_client_rejects_malformed_successful_provider_payload():
    client, services = _client()
    services[("sheets", "v4")].resource.values_resource.get = lambda **_kwargs: Request(
        {"range": "A1:B2", "values": {}}
    )

    with pytest.raises(GoogleDrivePayloadError, match="invalid response for spreadsheet read"):
        client.read_sheet("sheet-1", "A1:B2")


def test_client_decodes_an_omitted_values_field_as_an_empty_sheet_range():
    client, services = _client()
    services[("sheets", "v4")].resource.values_resource.get = lambda **_kwargs: Request({"range": "A1:B2"})

    assert client.read_sheet("sheet-1", "A1:B2").values == ()


def test_multi_account_client_resolves_account_by_email():
    first, _ = _client()
    second = GoogleDriveClient(
        token_path=Path("other.json"),
        account_id="acct-2",
        email="other@example.com",
        credentials=object(),
    )
    client = MultiGoogleDriveClient([first, second])

    assert client.select_account("user@example.com") is first
    assert client.select_account("OTHER@example.com") is second


def test_drive_refs_must_be_account_qualified_even_with_one_account():
    first, _ = _client()
    client = MultiGoogleDriveClient([first])

    assert client.resolve_ref("acct-1:doc-1") == (first, "doc-1")
    with pytest.raises(ValueError, match="account-qualified"):
        client.resolve_ref("doc-1")


def test_multi_account_error_names_available_account_emails():
    first, _ = _client()
    second = GoogleDriveClient(
        token_path=Path("other.json"),
        account_id="acct-2",
        email="other@example.com",
        credentials=object(),
    )
    client = MultiGoogleDriveClient([first, second])

    try:
        client.select_account()
    except ValueError as exc:
        assert str(exc) == "Specify a Google account by email: user@example.com, other@example.com"
    else:
        raise AssertionError("expected account selection error")
