from pathlib import Path

from arden.server.schemas import CreateAutomationRequest
from arden.tools.automation import CreateAutomationInput

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


def test_builtin_skill_docs_match_current_automation_and_tool_contracts():
    propose = (ROOT / "apps/server/skills/propose-automation/SKILL.md").read_text()
    add_skill = (ROOT / "apps/server/skills/add-skill/SKILL.md").read_text()
    add_tool = (ROOT / "apps/server/skills/add-tool/SKILL.md").read_text()
    loop = (ROOT / "apps/server/skills/loop/SKILL.md").read_text()
    mermaid = (ROOT / "apps/server/skills/mermaid/SKILL.md").read_text()
    wiki = (ROOT / "apps/server/skills/wiki-automation/SKILL.md").read_text()

    assert CreateAutomationRequest.model_fields["prompt"].is_required()
    assert CreateAutomationInput.model_fields["prompt"].is_required()
    assert "**`prompt`**: the full instructions" in propose
    assert "`tool_scope`" in propose
    assert "`auto_approve` does not grant tools" in propose
    assert "`agent/skills/`, `.agents/skills/`, or `.skills/`" in add_skill
    assert "max 48 chars" in add_skill
    assert 'services["memory"]' not in add_tool
    assert 'services["facts"]' in add_tool
    assert "ToolResult.failure(" in add_tool
    assert "`1h30m`, `2d12h`" in loop
    assert "`ReadFile`" not in mermaid
    assert "`read_file`" in mermaid
    assert "`not_found` result" in wiki


def test_automation_docs_use_current_prompt_and_trigger_contracts():
    guide = (ROOT / "docs/guides/automations.mdx").read_text()
    api = (ROOT / "docs/api-reference/automations.mdx").read_text()

    for document in (guide, api):
        assert '"prompt":' in document
        assert "idle_minutes" in document
        assert "every_n" in document
    assert '"trigger_type": "time"' in api
    assert '"trigger_type": "event"' in api
    assert '"type": "message"' in api
    assert '"channels": ["alerts"]' in api
    assert '"prompt": "New standalone task instructions"' in api
    assert "This setting does not grant tools" in api
