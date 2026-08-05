from arden.integrations.calendar.client import GoogleCalendar


def test_calendar_uses_explicit_account_token(tmp_path):
    token_path = tmp_path / "calendar_user@example.test.json"
    source = GoogleCalendar("user@example.test", token_path)

    assert source.account.account_ref == "user@example.test"
    assert source.token_path == token_path
