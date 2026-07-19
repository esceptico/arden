from ntrp.core.prompts import build_system_blocks


def _text(blocks: list[dict]) -> str:
    return "\n".join(b["text"] for b in blocks if b.get("type") == "text")


def test_build_system_blocks_injects_todo_override():
    blocks = build_system_blocks(
        source_details={},
        todo_override={
            "items": [
                {"content": "buy milk", "status": "pending"},
                {"content": "ship it", "status": "in_progress"},
            ],
            "explanation": None,
        },
    )
    text = _text(blocks)
    assert "TODO LIST (edited by the user)" in text
    assert "[pending] buy milk" in text
    assert "[in_progress] ship it" in text


def test_build_system_blocks_omits_todo_block_when_no_override():
    assert "TODO LIST (edited by the user)" not in _text(build_system_blocks(source_details={}))
    assert "TODO LIST (edited by the user)" not in _text(
        build_system_blocks(source_details={}, todo_override={"items": []})
    )


def test_build_system_blocks_native_deferred_prompt_omits_group_loader():
    text = _text(build_system_blocks(source_details={}, native_deferred_tools=True))

    assert "tool_search" in text
    assert "load_tools" not in text
    assert "load the relevant group" not in text


def test_build_system_blocks_records_deterministic_context_manifest():
    first = []
    second = []

    build_system_blocks(source_details={}, memory_context="Known preference", context_manifest=first)
    build_system_blocks(source_details={}, memory_context="Known preference", context_manifest=second)

    memory = next(entry for entry in first if entry.content_type == "memory_context")
    assert memory.source == "memory"
    assert memory.ref == "resident_profile"
    assert memory.selection_reason == "activated for this session"
    assert memory.size_bytes == len(b"## MEMORY CONTEXT\nKnown preference")
    assert [entry.context_id for entry in first] == [entry.context_id for entry in second]
