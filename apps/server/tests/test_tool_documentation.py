from pathlib import Path

ROOT = Path(__file__).parents[3]


def test_tool_guide_documents_canonical_mutation_workflows():
    guide = (ROOT / "docs/guides/tools.mdx").read_text()

    assert "search → inspect → preview → mutate → verify" in guide
    assert "reply_email" in guide
    assert "idempotency_key" in guide
    assert "raw_ref" in guide
    assert "source_refs" in guide
    assert "MCP recovery" in guide


def test_add_tool_skill_requires_harness_safety_contract():
    skill = (ROOT / "apps/server/skills/add-tool/SKILL.md").read_text()

    for required in (
        "ToolResult.failure",
        "recovery_action",
        "ToolSourceRef",
        "idempotency_key",
        "ToolOutcome",
        "verification",
    ):
        assert required in skill
