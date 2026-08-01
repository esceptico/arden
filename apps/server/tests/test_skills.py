from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from arden.server.app import app
from arden.server.deps import require_skill_service
from arden.skills.registry import SkillRegistry
from arden.skills.service import SkillService, get_skills_dirs
from arden.skills.tool import CreateSkillInput, UseSkillInput, create_skill, use_skill


def _write_skill(root: Path, name: str, frontmatter: str, body: str = "# Body\n") -> None:
    skill_dir = root / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}---\n\n{body}")


def test_builtin_obsidian_skill_is_not_registered():
    registry = SkillRegistry()
    registry.load(get_skills_dirs())

    assert registry.get("obsidian") is None


def test_registry_rejects_invalid_skill_metadata(tmp_path):
    _write_skill(
        tmp_path,
        "Bad_Name",
        "name: Bad_Name\ndescription: works\n",
    )
    registry = SkillRegistry()

    registry.load([(tmp_path, "area")])

    assert registry.get("Bad_Name") is None
    assert registry.validation_issues[0]["reason"] == "invalid_name"
    assert registry.validation_issues[0]["path"].endswith("Bad_Name/SKILL.md")


def test_registry_loads_skill_governance_metadata(tmp_path):
    _write_skill(
        tmp_path,
        "research-helper",
        (
            "name: research-helper\n"
            "description: Helps with research\n"
            "source: github:example/research-helper\n"
            "version: 2026-05-16\n"
            "reviewed_at: 2026-05-16\n"
        ),
    )
    registry = SkillRegistry()

    registry.load([(tmp_path, "area")])

    skill = registry.get("research-helper")
    assert skill is not None
    assert skill.source == "github:example/research-helper"
    assert skill.version == "2026-05-16"
    assert skill.reviewed_at == "2026-05-16"


def test_registry_renders_skill_xml_with_arguments(tmp_path):
    _write_skill(
        tmp_path,
        "research-helper",
        "name: research-helper\ndescription: Helps with research\n",
        body="# Research helper\nUse <skill_path> for local assets.\n",
    )
    registry = SkillRegistry()
    registry.load([(tmp_path, "area")])

    rendered = registry.render_skill_xml("research-helper", args="Current user request: audit it")

    assert rendered is not None
    assert rendered.startswith(f'<skill name="research-helper" path="{tmp_path / "research-helper"}">')
    assert f"Use {tmp_path / 'research-helper'} for local assets." in rendered
    assert "ARGUMENTS: Current user request: audit it" in rendered


@pytest.mark.asyncio
async def test_skill_tools_return_typed_actionable_failures():
    empty_registry = SkillRegistry()
    use_execution = SimpleNamespace(ctx=SimpleNamespace(services={"skill_registry": empty_registry}))

    missing = await use_skill(use_execution, UseSkillInput(skill="missing"))
    unavailable = await create_skill(
        SimpleNamespace(ctx=SimpleNamespace(services={})),
        CreateSkillInput(name="new-skill", description="Reusable procedure", body="# Procedure"),
    )

    for result, code in ((missing, "not_found"), (unavailable, "not_configured")):
        assert result.is_error
        assert result.outcome is not None and result.outcome.error is not None
        assert result.outcome.error.code == code
        assert result.outcome.error.recovery_action


def test_skill_service_governance_report_marks_cleanup_candidates(tmp_path):
    _write_skill(
        tmp_path,
        "old-helper",
        ("name: old-helper\ndescription: Old helper\nreviewed_at: 2025-01-01\n"),
    )
    registry = SkillRegistry()
    registry.load([(tmp_path, "area")])
    service = SkillService(registry)

    report = service.governance_report(now_date="2026-05-16")

    assert report["summary"]["cleanup_candidate_count"] == 1
    assert report["cleanup_candidates"][0]["name"] == "old-helper"
    assert report["cleanup_candidates"][0]["reason"] == "review_stale"
    assert registry.get("old-helper") is not None


def test_skill_governance_endpoint_returns_report(tmp_path):
    _write_skill(
        tmp_path,
        "old-helper",
        ("name: old-helper\ndescription: Old helper\nreviewed_at: 2025-01-01\n"),
    )
    registry = SkillRegistry()
    registry.load([(tmp_path, "area")])
    service = SkillService(registry)
    app.dependency_overrides[require_skill_service] = lambda: service

    try:
        response = TestClient(app).get("/skills/governance")
    finally:
        app.dependency_overrides.pop(require_skill_service, None)

    assert response.status_code == 200
    assert response.json()["summary"]["cleanup_candidate_count"] == 1
    assert response.json()["cleanup_candidates"][0]["name"] == "old-helper"
