from arden.integrations.calendar import client as calendar_client


def test_calendar_never_falls_back_to_gmail_token(monkeypatch, tmp_path):
    monkeypatch.setattr(calendar_client, "ARDEN_DIR", tmp_path)
    (tmp_path / "gmail_token_user@example.com.json").write_text("{}")

    source = calendar_client.GoogleCalendar()

    assert source.token_path == tmp_path / "calendar_token.json"
