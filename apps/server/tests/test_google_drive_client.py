from pathlib import Path
from types import SimpleNamespace

import pytest
from google.auth.exceptions import RefreshError

from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.google_drive.client import GoogleDriveClient, MultiGoogleDriveClient
from arden.integrations.google_drive.errors import GoogleDrivePayloadError
from arden.integrations.google_drive.models import DrivePageRef


class Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class Files:
    def __init__(self):
        self.kwargs = None
        self.response = {
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

    def list(self, **kwargs):
        self.kwargs = kwargs
        return Request(self.response)


class DriveService:
    def __init__(self):
        self.resource = Files()

    def files(self):
        return self.resource


class Documents:
    def __init__(self):
        self.batch_body = None
        self.created_body = None
        self.get_kwargs = None
        self.read_tabs = [
            {
                "documentTab": {
                    "body": {
                        "content": [
                            {"sectionBreak": {"sectionStyle": {"sectionType": "CONTINUOUS"}}},
                            {"paragraph": {"elements": [{"textRun": {"content": "Old\n"}}]}}
                        ]
                    }
                }
            }
        ]

    def create(self, *, body, fields):
        self.created_body = body
        assert fields == "documentId,title"
        return Request({"documentId": "doc-new", "title": body["title"]})

    def get(self, **kwargs):
        self.get_kwargs = kwargs
        response = {
            "documentId": "doc-1",
            "title": "Roadmap",
            "revisionId": "rev-7",
        }
        if "tabs" in kwargs["fields"]:
            response["tabs"] = self.read_tabs
        return Request(response)

    def batchUpdate(self, *, documentId, body, fields):
        assert documentId == "doc-1"
        assert fields == "documentId"
        self.batch_body = body
        return Request({"documentId": documentId})


class DocsService:
    def __init__(self):
        self.resource = Documents()

    def documents(self):
        return self.resource


class Values:
    def __init__(self):
        self.updated_range = None
        self.body = None

    def update(self, *, spreadsheetId, range, valueInputOption, body, fields):
        assert spreadsheetId == "sheet-1"
        assert valueInputOption == "USER_ENTERED"
        assert fields == "spreadsheetId,updatedRange"
        self.updated_range = range
        self.body = body
        return Request({"spreadsheetId": spreadsheetId, "updatedRange": range})

    def get(self, *, spreadsheetId, range, majorDimension, fields):
        assert spreadsheetId == "sheet-1"
        assert majorDimension == "ROWS"
        assert fields == "range,majorDimension,values"
        return Request(
            {"range": range, "majorDimension": "ROWS", "values": [["Name", "Count"], ["Ada", 2]]}
        )

    def append(self, *, spreadsheetId, range, valueInputOption, insertDataOption, body, fields):
        assert spreadsheetId == "sheet-1"
        assert insertDataOption == "INSERT_ROWS"
        assert fields == "spreadsheetId,updates(updatedRange)"
        return Request({"spreadsheetId": spreadsheetId, "updates": {"updatedRange": range}})


class Spreadsheets:
    def __init__(self):
        self.values_resource = Values()
        self.created_body = None

    def create(self, *, body, fields):
        self.created_body = body
        assert fields == "spreadsheetId,spreadsheetUrl,properties(title)"
        return Request(
            {
                "spreadsheetId": "sheet-new",
                "spreadsheetUrl": "https://sheets.test/sheet-new",
                "properties": {"title": body["properties"]["title"]},
            }
        )

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
        account_ref="user@example.com",
        build_service=build_service,
        credentials=object(),
    )
    return client, services


def test_search_filters_workspace_files_and_qualifies_refs():
    client, services = _client()

    results = client.search("roadmap", kind="doc", limit=10)

    assert [item.ref for item in results.files] == ["user@example.com:doc-1"]
    assert results.has_more is False
    assert "application/vnd.google-apps.document" in services[("drive", "v3")].resource.kwargs["q"]
    assert services[("drive", "v3")].resource.kwargs["pageSize"] == 10


def test_search_exposes_account_bound_pagination_reference():
    client, services = _client()
    services[("drive", "v3")].resource.response["nextPageToken"] = "page-2"
    services[("drive", "v3")].resource.response["incompleteSearch"] = True

    page = client.search("roadmap", limit=10)

    assert isinstance(page.files, tuple)
    assert page.has_more is True
    assert page.next_page_ref == DrivePageRef(
        account_ref="user@example.com",
        provider_token="page-2",
        query="roadmap",
        kind="all",
        limit=10,
    )
    assert page.incomplete_search is True


def test_search_rejects_a_repeated_provider_page_token():
    client, services = _client()
    services[("drive", "v3")].resource.response["nextPageToken"] = "page-2"

    with pytest.raises(IntegrationOperationError, match="current page_ref"):
        client.search("roadmap", page_token="page-2")


@pytest.mark.parametrize(
    "response",
    [
        {"files": [], "unknown": True},
        {
            "files": [
                {
                    "id": "doc-1",
                    "name": "Roadmap",
                    "mimeType": "application/vnd.google-apps.document",
                    "modifiedTime": "2026-07-20T10:00:00Z",
                    "webViewLink": "https://docs.google.com/document/d/doc-1/edit",
                    "unknown": True,
                }
            ]
        },
        {"files": [], "nextPageToken": "invalid\npage"},
    ],
)
def test_search_rejects_unknown_fields_and_inconsistent_pagination(response):
    client, services = _client()
    services[("drive", "v3")].resource.response = response

    with pytest.raises(GoogleDrivePayloadError, match="invalid response for file search"):
        client.search("roadmap")


def test_provider_transport_and_auth_errors_are_safely_translated():
    client, services = _client()
    files = services[("drive", "v3")].resource
    files.response = OSError("private transport detail")

    with pytest.raises(IntegrationOperationError) as operation_error:
        client.search("roadmap")
    assert operation_error.value.code == "provider_error"
    assert operation_error.value.retryable is True
    assert "private transport detail" not in operation_error.value.safe_message

    files.response = RefreshError("expired private token")
    with pytest.raises(IntegrationConnectionError) as connection_error:
        client.search("roadmap")
    assert connection_error.value.reason == "auth_required"
    assert connection_error.value.account_ref == "user@example.com"


def test_document_decoder_preserves_paragraphs_and_tables():
    client, services = _client()
    services[("docs", "v1")].resource.read_tabs = [
        {
            "documentTab": {
                "body": {
                    "content": [
                        {"paragraph": {"elements": [{"textRun": {"content": "Heading\n"}}]}},
                        {
                            "table": {
                                "tableRows": [
                                    {
                                        "tableCells": [
                                            {
                                                "content": [
                                                    {
                                                        "paragraph": {
                                                            "elements": [{"textRun": {"content": "A"}}]
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                "content": [
                                                    {
                                                        "paragraph": {
                                                            "elements": [{"textRun": {"content": "B"}}]
                                                        }
                                                    }
                                                ]
                                            },
                                        ]
                                    }
                                ]
                            }
                        },
                    ]
                }
            }
        }
    ]

    assert client.read_doc("doc-1").text == "Heading\nA\tB"


def test_document_decoder_traverses_all_three_supported_tab_levels():
    client, services = _client()

    def tab(text, children=()):
        value = {
            "documentTab": {
                "body": {
                    "content": [
                        {"paragraph": {"elements": [{"textRun": {"content": text}}]}}
                    ]
                }
            }
        }
        if children:
            value["childTabs"] = list(children)
        return value

    services[("docs", "v1")].resource.read_tabs = [
        tab("Parent", (tab("Child", (tab("Grandchild"),)),)),
    ]

    assert client.read_doc("doc-1").text == "Parent\n\nChild\n\nGrandchild"
    fields = services[("docs", "v1")].resource.get_kwargs["fields"]
    assert fields.count("(") == fields.count(")")
    assert fields.count("childTabs") == 2


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
    assert result.ref == "user@example.com:doc-new"


def test_create_sheet_creates_empty_file_without_values_write():
    client, services = _client()

    result = client.create_sheet("Empty sheet")

    spreadsheets = services[("sheets", "v4")].resource
    assert spreadsheets.created_body == {"properties": {"title": "Empty sheet"}}
    assert spreadsheets.values_resource.body is None
    assert result.ref == "user@example.com:sheet-new"


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
        {"range": "A1:B2", "majorDimension": "ROWS", "values": {}}
    )

    with pytest.raises(GoogleDrivePayloadError, match="invalid response for spreadsheet read"):
        client.read_sheet("sheet-1", "A1:B2")


def test_client_rejects_unknown_document_fields_and_mismatched_returned_ids():
    client, services = _client()
    documents = services[("docs", "v1")].resource
    documents.read_tabs[0]["documentTab"]["body"]["unknown"] = True

    with pytest.raises(GoogleDrivePayloadError, match="invalid response for document read"):
        client.read_doc("doc-1")

    client, services = _client()
    documents = services[("docs", "v1")].resource
    documents.get = lambda **_kwargs: Request(
        {
            "documentId": "different-doc",
            "title": "Roadmap",
            "revisionId": "rev-7",
            "tabs": documents.read_tabs,
        }
    )
    with pytest.raises(GoogleDrivePayloadError, match="invalid response for document read"):
        client.read_doc("doc-1")

    values = services[("sheets", "v4")].resource.values_resource
    values.update = lambda **_kwargs: Request(
        {"spreadsheetId": "different-sheet", "updatedRange": "A1:B2"}
    )
    with pytest.raises(GoogleDrivePayloadError, match="invalid response for spreadsheet update"):
        client.update_sheet("sheet-1", "A1:B2", [["a", 1]], "USER_ENTERED")


def test_client_decodes_an_omitted_values_field_as_an_empty_sheet_range():
    client, services = _client()
    services[("sheets", "v4")].resource.values_resource.get = lambda **_kwargs: Request(
        {"range": "A1:B2", "majorDimension": "ROWS"}
    )

    assert client.read_sheet("sheet-1", "A1:B2").values == ()


def test_multi_account_client_resolves_account_by_email():
    first, _ = _client()
    second = GoogleDriveClient(
        token_path=Path("other.json"),
        account_ref="other@example.com",
        credentials=object(),
    )
    client = MultiGoogleDriveClient([first, second])

    assert client.select_account("user@example.com") is first
    assert client.select_account("other@example.com") is second
    with pytest.raises(IntegrationOperationError, match="Google Drive account not found"):
        client.select_account("OTHER@example.com")


def test_multi_account_verification_checks_only_the_requested_account():
    calls: list[str] = []
    client = object.__new__(MultiGoogleDriveClient)
    client._clients = {
        "first@example.com": SimpleNamespace(verify_connection=lambda: calls.append("first")),
        "second@example.com": SimpleNamespace(verify_connection=lambda: calls.append("second")),
    }

    client.verify_account("second@example.com")

    assert calls == ["second"]


def test_drive_refs_must_be_account_qualified_even_with_one_account():
    first, _ = _client()
    client = MultiGoogleDriveClient([first])

    assert client.resolve_ref("user@example.com:doc-1") == (first, "doc-1")
    with pytest.raises(IntegrationOperationError, match="account-qualified"):
        client.resolve_ref("doc-1")


def test_multi_account_error_names_available_account_emails():
    first, _ = _client()
    second = GoogleDriveClient(
        token_path=Path("other.json"),
        account_ref="other@example.com",
        credentials=object(),
    )
    client = MultiGoogleDriveClient([first, second])

    try:
        client.select_account()
    except IntegrationOperationError as exc:
        assert str(exc) == "Specify a Google Drive account by exact email: user@example.com, other@example.com"
    else:
        raise AssertionError("expected account selection error")


def test_multi_account_search_requires_exact_account_and_round_trips_page_ref():
    first, services = _client()
    second = GoogleDriveClient(
        token_path=Path("other.json"),
        account_ref="other@example.com",
        credentials=object(),
    )
    client = MultiGoogleDriveClient([first, second])

    with pytest.raises(IntegrationOperationError, match="Specify a Google Drive account"):
        client.search("roadmap")

    services[("drive", "v3")].resource.response["nextPageToken"] = "page-2"
    first_page = client.search("roadmap", account_ref="user@example.com")
    assert first_page.next_page_ref is not None

    services[("drive", "v3")].resource.response["nextPageToken"] = "page-3"
    client.search("changed query", kind="sheet", limit=1, page_ref=first_page.next_page_ref.encode())
    assert services[("drive", "v3")].resource.kwargs["pageToken"] == "page-2"
    assert "fullText contains 'roadmap'" in services[("drive", "v3")].resource.kwargs["q"]
    assert services[("drive", "v3")].resource.kwargs["pageSize"] == 20

    with pytest.raises(IntegrationOperationError, match="different account"):
        client.search(
            "roadmap",
            account_ref="other@example.com",
            page_ref=first_page.next_page_ref.encode(),
        )
