from arden.integrations.telegram.notifier import _send


class _Response:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb):
        return None


class _Session:
    def __init__(self):
        self.requests = []

    def post(self, url, *, json):
        self.requests.append((url, json))
        return _Response()


async def test_send_uses_entities_without_markdown_parse_mode():
    session = _Session()

    await _send(session, "**Bold**", "token", "chat")

    assert len(session.requests) == 1
    url, payload = session.requests[0]
    assert url == "https://api.telegram.org/bottoken/sendMessage"
    assert payload["chat_id"] == "chat"
    assert payload["text"] == "Bold"
    assert payload["entities"] == [{"type": "bold", "offset": 0, "length": 4}]
    assert "parse_mode" not in payload
