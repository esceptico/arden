from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from arden.integrations.calendar.tools import CalendarInput
from arden.integrations.gmail.tools import EmailsInput
from arden.integrations.slack.tools import SlackSearchInput
from arden.tools.core.collections import format_timestamp, paginate
from arden.tools.workflow import _render


def test_page_cursor_continues_without_overlap():
    first = paginate([1, 2, 3, 4, 5], limit=2)
    second = paginate([1, 2, 3, 4, 5], limit=2, cursor=first.next_cursor)

    assert first.to_dict() == {"items": [1, 2], "total": 5, "has_more": True, "next_cursor": first.next_cursor}
    assert second.items == (3, 4)


def test_invalid_cursor_is_rejected():
    with pytest.raises(ValueError, match="Invalid pagination cursor"):
        paginate([1], limit=1, cursor="broken")


def test_format_timestamp_requires_and_preserves_offset():
    assert format_timestamp(datetime(2026, 7, 20, 12, tzinfo=UTC)) == "2026-07-20T12:00:00+00:00"
    with pytest.raises(ValueError, match="timezone-aware"):
        format_timestamp(datetime(2026, 7, 20, 12))


def test_collection_inputs_reject_invalid_enums_and_bounds():
    with pytest.raises(ValidationError):
        SlackSearchInput(query="status", scope="everything")
    with pytest.raises(ValidationError):
        EmailsInput(limit=0)
    with pytest.raises(ValidationError):
        CalendarInput(limit=101)


def test_workflow_result_uses_compact_stable_summary():
    rendered = _render({"z": [2, 1], "a": {"status": "ok"}})

    assert rendered == "a:\n  status: ok\nz:\n  - 2\n  - 1"
    assert "{" not in rendered
