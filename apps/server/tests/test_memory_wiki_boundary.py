from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from arden.memory.artifacts import ArtifactMemoryStore
from arden.memory.file_store import CanonicalFileRole, FilePageStore
from arden.memory.page_edit_service import PageEditService
from arden.memory.page_events import page_revision
from arden.memory.vault_index import VaultIndexer
from arden.server.deps import require_knowledge_runtime
from arden.server.routers.memory import router as memory_router


class _Reconciler:
    async def reconcile_page_edit(self, _analysis):
        return ()


@pytest.mark.asyncio
async def test_legacy_memory_surfaces_exclude_the_managed_wiki_subtree(tmp_path: Path):
    vault = tmp_path / "memory"
    (vault / "wiki" / "pages").mkdir(parents=True)
    (vault / "note.md").write_text("# Note\n", encoding="utf-8")
    managed = vault / "wiki" / "pages" / "topic.md"
    managed.write_text("# Managed topic\n", encoding="utf-8")

    artifacts = ArtifactMemoryStore(vault)
    assert [artifact.path for artifact in artifacts.list_artifacts()] == ["note.md"]
    assert "wiki/" not in artifacts.list_directories()
    with pytest.raises(FileNotFoundError):
        artifacts.read_artifact("wiki/pages/topic.md")
    with pytest.raises(FileNotFoundError):
        artifacts.read_resource_bytes("wiki/pages/topic.md")

    store = FilePageStore(vault)
    await store.open()
    try:
        assert "wiki/pages/topic.md" not in store._editable_page_bytes()
        assert not any(path.is_relative_to(vault / "wiki") for path in store._scan_files())
        with pytest.raises(ValueError, match="managed wiki"):
            store._validate_caller_files(
                {Path("wiki/pages/topic.md"): b"legacy write"},
                {Path("wiki/pages/topic.md"): CanonicalFileRole.USER_PAGE},
            )

        managed.write_text("# Changed managed topic\n", encoding="utf-8")
        assert await store.refresh_from_disk() == []

        assert [entry.path for entry in VaultIndexer(vault).entries if entry.path.startswith("wiki/")] == []

        service = PageEditService(vault, store, reconciler=_Reconciler())
        app = FastAPI()
        app.include_router(memory_router)
        knowledge = SimpleNamespace(page_edit_service=service, artifact_store=artifacts)
        app.dependency_overrides[require_knowledge_runtime] = lambda: knowledge
        response = TestClient(app).post(
            "/admin/memory/page-edits/preview",
            json={
                "path": "wiki/pages/topic.md",
                "base_revision": page_revision(managed.read_bytes()),
                "content": "# Attempted legacy edit\n",
            },
        )
        assert response.status_code == 403
    finally:
        await store.close()
