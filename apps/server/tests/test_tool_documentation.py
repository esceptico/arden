from pathlib import Path

from arden.automation.builtins import FACT_MAINTENANCE_PROMPT, WIKI_MAINTENANCE_PROMPT
from arden.core.prompts import BASE_SYSTEM_PROMPT
from arden.server.schemas import CreateAutomationRequest
from arden.tools.area import AreaPagePatchInput, AreaPageWriteInput
from arden.tools.automation import AutomationCreateInput
from arden.tools.directives import DirectivesSetInput
from arden.tools.files import FileEditInput, WriteFileInput
from arden.tools.wiki import (
    WikiArchivePageInput,
    WikiCreatePageInput,
    WikiEditPageInput,
    WikiMovePageInput,
    WikiPatchPageInput,
    WikiPublishGeneratedInput,
)
from arden.wiki.maintenance.runner import WikiMaintenanceDecision


def test_create_automation_prompt_is_required_by_both_input_schemas():
    assert CreateAutomationRequest.model_fields["prompt"].is_required()
    assert AutomationCreateInput.model_fields["prompt"].is_required()


def test_create_automation_idempotency_key_is_required_by_both_input_schemas():
    assert CreateAutomationRequest.model_fields["idempotency_key"].is_required()
    assert AutomationCreateInput.model_fields["idempotency_key"].is_required()


def test_agent_wiki_mutations_hide_backend_revision_hashes():
    internal_fields = {
        "blob_id",
        "commit_id",
        "expected_head",
        "expected_sha256",
        "expected_version",
        "head",
        "sha256",
        "version",
    }
    for input_model in (
        WikiCreatePageInput,
        WikiEditPageInput,
        WikiPatchPageInput,
        WikiArchivePageInput,
        WikiMovePageInput,
        WikiPublishGeneratedInput,
        AreaPagePatchInput,
        AreaPageWriteInput,
        WriteFileInput,
        FileEditInput,
        DirectivesSetInput,
    ):
        assert internal_fields.isdisjoint(input_model.model_fields)


def test_agent_wiki_guidance_uses_paths_and_fresh_reads_not_backend_tokens():
    server_root = Path(__file__).parents[1]
    repository_root = Path(__file__).parents[3]
    guidance = "\n".join(
        (
            BASE_SYSTEM_PROMPT,
            FACT_MAINTENANCE_PROMPT,
            WIKI_MAINTENANCE_PROMPT,
            (server_root / "skills/wiki-automation/SKILL.md").read_text(),
            (repository_root / "docs/guides/memory.mdx").read_text(),
            (repository_root / "docs/guides/tools.mdx").read_text(),
        )
    )

    assert "exact managed path" in guidance
    assert "fresh list/read" in guidance
    for forbidden in ("page ID", "page_id", "expected_head", "expected_version", "repository_head"):
        assert forbidden not in guidance


def test_wiki_maintenance_agent_decides_without_a_user_review_outcome():
    assert WikiMaintenanceDecision.model_json_schema()["properties"]["outcome"]["enum"] == ["no_change", "updates"]
    assert "needs_review" not in WIKI_MAINTENANCE_PROMPT
