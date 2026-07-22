import json

from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from arden.cli import main
from arden.server.routers.runtime_info import router


def test_agent_surface_manifest_discovers_path_ids(tmp_path):
    from arden.agent_surface.discovery import discover_agent_surface
    from arden.agent_surface.manifest import write_manifest

    skill = tmp_path / "agent" / "skills" / "revenue-definitions" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: revenue-definitions\ndescription: Revenue terms.\n---\nBody\n")
    schedule = tmp_path / "agent" / "schedules" / "memory" / "rebuild.yaml"
    schedule.parent.mkdir(parents=True)
    schedule.write_text('cron: "0 9 * * *"\nprompt: "Rebuild memory."\n')

    manifest = discover_agent_surface(tmp_path)
    path = write_manifest(tmp_path, manifest)

    assert manifest.skills[0].id == "revenue-definitions"
    assert manifest.schedules[0].id == "memory/rebuild"
    assert path == tmp_path / ".arden" / "manifest.json"
    data = json.loads(path.read_text())
    assert data["agent_surface"]["root"] == "agent/"
    assert data["skills"][0]["id"] == "revenue-definitions"
    assert data["schedules"][0]["id"] == "memory/rebuild"


def test_runtime_info_endpoint_returns_manifest(tmp_path, monkeypatch):
    from arden.agent_surface.runtime_info import build_runtime_info

    (tmp_path / "agent" / "skills" / "demo").mkdir(parents=True)
    (tmp_path / "agent" / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill.\n---\nBody\n"
    )
    monkeypatch.chdir(tmp_path)

    test_app = FastAPI()
    test_app.include_router(router)

    response = TestClient(test_app).get("/runtime/info")

    assert response.status_code == 200
    assert response.json() == build_runtime_info(tmp_path).model_dump(mode="json")
    assert not (tmp_path / ".arden" / "manifest.json").exists()


def test_runtime_info_endpoint_reports_active_runtime_tools(tmp_path, monkeypatch):
    class Executor:
        def get_tool_metadata(self):
            return [{"name": "read_file", "policy": {"requires_approval": False}}]

    class Runtime:
        executor = Executor()
        skill_registry = None
        automation_service = None

    monkeypatch.chdir(tmp_path)
    test_app = FastAPI()
    test_app.state.runtime = Runtime()
    test_app.include_router(router)

    response = TestClient(test_app).get("/runtime/info")

    assert response.status_code == 200
    assert response.json()["tools"] == [{"name": "read_file", "policy": {"requires_approval": False}}]
    assert "RUN_STARTED" in response.json()["event_types"]


def test_cli_info_prints_runtime_info(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["info"])

    assert result.exit_code == 0
    assert "Runtime" in result.output
    assert ".arden/manifest.json" in result.output
    assert not (tmp_path / ".arden" / "manifest.json").exists()
