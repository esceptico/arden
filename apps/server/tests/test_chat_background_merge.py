from arden.services.chat import _merge_background_messages


def test_background_merge_appends_background_tail_to_saved_prefix():
    current = [{"role": "user", "content": "before"}]
    background = [
        {"role": "user", "content": "before"},
        {"role": "user", "content": "background request"},
        {"role": "assistant", "content": "background result"},
    ]

    assert _merge_background_messages(current, background) == background


def test_background_merge_preserves_newer_session_messages():
    base = [{"role": "user", "content": "before"}]
    current = [
        *base,
        {"role": "user", "content": "new request"},
        {"role": "assistant", "content": "new result"},
    ]
    background = [
        *base,
        {"role": "user", "content": "background request"},
        {"role": "assistant", "content": "background result"},
    ]

    assert _merge_background_messages(current, background) == [
        *current,
        {"role": "user", "content": "background request"},
        {"role": "assistant", "content": "background result"},
    ]


def test_background_merge_does_not_duplicate_already_saved_background_tail():
    background = [
        {"role": "user", "content": "before"},
        {"role": "user", "content": "background request"},
        {"role": "assistant", "content": "background result"},
    ]
    current = [
        *background,
        {"role": "user", "content": "new request"},
    ]

    assert _merge_background_messages(current, background) == current
