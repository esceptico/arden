from pathlib import Path

from ntrp.integrations.google_drive.client import GoogleDriveClient
from ntrp.integrations.google_drive.render import flatten_google_doc


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


class Spreadsheets:
    def __init__(self):
        self.values_resource = Values()

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

    client.update_sheet("sheet-1", "Data!A2:B2", [["a", 2]], "USER_ENTERED")

    values = services[("sheets", "v4")].resource.values_resource
    assert values.updated_range == "Data!A2:B2"
    assert values.body == {"values": [["a", 2]]}
