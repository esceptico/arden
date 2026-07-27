from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from arden.config import Config
from arden.memory.artifacts import ArtifactMemoryStore
from arden.memory.file_store import CanonicalFileRole, FilePageStore
from arden.memory.page_edit_service import PageEditService
from arden.memory.page_events import page_revision
from arden.memory.vault_index import VaultIndexer
from arden.revisions import ManagedFileRepository
from arden.server.deps import require_knowledge_runtime
from arden.server.routers.memory import router as memory_router
from arden.server.runtime.core import Runtime
from arden.wiki import WikiService


class _Reconciler:
    def __init__(self, answer=()):
        self.answer = answer

    async def reconcile_page_edit(self, _analysis):
        return self.answer


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


@pytest.mark.asyncio
async def test_memory_api_projects_managed_wiki_pages_without_legacy_writes(tmp_path: Path):
    vault = tmp_path / "memory"
    (vault / "topics").mkdir(parents=True)
    legacy = vault / "topics" / "managed.md"
    legacy.write_text("# Legacy collision\nLegacy-only text.\n", encoding="utf-8")
    retry_legacy = vault / "topics" / "retry.md"
    retry_legacy.write_text("# Retry collision\n", encoding="utf-8")

    store = FilePageStore(vault)
    await store.open()
    try:
        reconciler = _Reconciler()
        service = PageEditService(vault, store, reconciler=reconciler)
        preview = await service.preview(
            path="topics/managed.md",
            base_revision=page_revision(legacy.read_bytes()),
            content=b"# Attempted legacy edit\n",
            actor="user:desktop",
        )
        reconciler.answer = None
        retry_preview = await service.preview(
            path="topics/retry.md",
            base_revision=page_revision(retry_legacy.read_bytes()),
            content=b"# Retry candidate\n",
            actor="user:desktop",
        )
        retry_pending = await service.apply(retry_preview.id, decisions={}, save_as_pending=True)
        reconciler.answer = ()
        wiki = WikiService(
            ManagedFileRepository(vault / "wiki" / "pages", history_root=vault / "wiki" / ".wiki-history")
        )
        managed = wiki.create_page(
            page_id="managed-page",
            path="topics/managed.md",
            title="Managed page",
            body=b"Managed body [[Wiki home]].\n",
            metadata={"kind": "topic", "summary": "Managed summary"},
        )
        wiki.create_page(
            page_id="wiki-readme",
            path="README.md",
            title="Wiki home",
            body=b"Wiki home body [[Managed page]].\n",
        )
        wiki.create_page(page_id="retry-page", path="topics/retry.md", title="Retry page")

        def disable_legacy_writes_after_cutover() -> None:
            if wiki.repository.head is not None:
                raise PermissionError("legacy memory page writes are disabled after managed wiki cutover")

        service.set_write_guard(disable_legacy_writes_after_cutover)

        app = FastAPI()
        app.include_router(memory_router)
        app.state.runtime = SimpleNamespace(wiki_service=wiki)
        app.dependency_overrides[require_knowledge_runtime] = lambda: SimpleNamespace(
            page_edit_service=service,
            artifact_store=ArtifactMemoryStore(vault),
        )
        client = TestClient(app)

        listed = client.get("/admin/memory/artifacts", params={"kind": "topic", "q": "managed body"})
        assert listed.status_code == 200
        summaries = listed.json()["artifacts"]
        assert len(summaries) == 1
        assert summaries[0]["path"] == "topics/managed.md"
        assert summaries[0]["source"] == "wiki"
        assert summaries[0]["editable"] is False
        assert summaries[0]["readonly_reason"] == "Managed wiki page — use Rename; editing is not available here yet."
        assert summaries[0]["revision"] == managed.resource.version_id
        assert "topics/" in listed.json()["directories"]

        legacy_only = client.get("/admin/memory/artifacts", params={"q": "legacy-only text"})
        assert legacy_only.status_code == 200
        assert "topics/managed.md" not in {item["path"] for item in legacy_only.json()["artifacts"]}

        all_summaries = client.get("/admin/memory/artifacts").json()["artifacts"]
        all_paths = {item["path"] for item in all_summaries}
        assert "README.md" in all_paths
        wiki_readme = next(item for item in all_summaries if item["path"] == "README.md")
        assert wiki_readme["directory"] == ""
        rebuilt_paths = {item["path"] for item in client.post("/admin/memory/artifacts/rebuild").json()["artifacts"]}
        assert {"README.md", "topics/managed.md"}.issubset(rebuilt_paths)

        detail = client.get("/admin/memory/artifacts/topics/managed.md")
        assert detail.status_code == 200
        artifact = detail.json()["artifact"]
        assert artifact["content"] == "Managed body [[Wiki home]].\n"
        assert artifact["frontmatter"]["page_id"] == "managed-page"
        assert artifact["frontmatter"]["title"] == "Managed page"
        assert artifact["source"] == "wiki"
        assert artifact["revision"] == managed.resource.version_id
        assert artifact["editable_content"] is None

        links = client.get("/admin/memory/links", params={"path": "topics/managed.md"})
        assert links.status_code == 200
        link_body = links.json()
        assert link_body["stale"] is False
        assert link_body["revision"] == wiki.repository.head
        assert link_body["outgoing"][0]["target"] == "Wiki home"
        assert link_body["outgoing"][0]["resolved_path"] == "README.md"
        assert link_body["outgoing"][0]["candidates"] == ["README.md"]
        assert link_body["backlinks"][0]["source_path"] == "README.md"

        blocked_preview = client.post(
            "/admin/memory/page-edits/preview",
            json={
                "path": "topics/managed.md",
                "base_revision": page_revision(legacy.read_bytes()),
                "content": "# Attempted API edit\n",
            },
        )
        assert blocked_preview.status_code == 403
        assert "Managed wiki page" in blocked_preview.json()["detail"]

        blocked_apply = client.put("/admin/memory/page-edits/apply", json={"preview_id": preview.id, "decisions": {}})
        assert blocked_apply.status_code == 403
        assert legacy.read_text(encoding="utf-8") == "# Legacy collision\nLegacy-only text.\n"

        blocked_retry = client.put(
            "/admin/memory/page-edits/retry", json={"event_id": retry_pending.id, "decisions": {}}
        )
        assert blocked_retry.status_code == 403
        assert retry_legacy.read_text(encoding="utf-8") == "# Retry candidate\n"

        blocked_create = client.post(
            "/admin/memory/notebook/create",
            json={"path": "topics/managed.md", "kind": "note"},
        )
        assert blocked_create.status_code == 409
        assert legacy.read_text(encoding="utf-8") == "# Legacy collision\nLegacy-only text.\n"

        with pytest.raises(PermissionError, match="managed wiki cutover"):
            await service.create_page(path="legacy-write.md", actor="user:desktop")

        plan = wiki.prepare_rename(
            "managed-page",
            new_path="topics/moved.md",
            new_title="Moved page",
            expected_version=wiki.read_page("managed-page").resource.version_id,
            base_head=wiki.repository.head,
        )
        wiki.apply_rename(plan)

        after_rename = client.get("/admin/memory/artifacts").json()["artifacts"]
        assert "topics/moved.md" in {item["path"] for item in after_rename}
        assert "topics/managed.md" not in {item["path"] for item in after_rename}
        old_detail = client.get("/admin/memory/artifacts/topics/managed.md")
        assert old_detail.status_code == 200
        assert old_detail.json()["artifact"]["source"] == "wiki"
        assert old_detail.json()["artifact"]["frontmatter"]["lifecycle"] == "redirect"
        old_links = client.get("/admin/memory/links", params={"path": "topics/managed.md"})
        assert old_links.status_code == 200
        assert old_links.json()["path"] == "topics/moved.md"
        old_history = client.get("/admin/memory/page-edits/history", params={"path": "topics/managed.md"})
        assert old_history.status_code == 409
        still_blocked = client.post(
            "/admin/memory/notebook/create",
            json={"path": "topics/managed.md", "kind": "note"},
        )
        assert still_blocked.status_code == 409
        assert legacy.read_text(encoding="utf-8") == "# Legacy collision\nLegacy-only text.\n"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_runtime_managed_cutover_starts_legacy_memory_read_only(tmp_path: Path, monkeypatch):
    config = Config(
        arden_dir=tmp_path,
        chat_model=None,
        embedding_model=None,
        model_roles={},
        web_search="none",
    )
    config.chat_model = None
    config.model_roles = {}
    vault = config.memory_artifacts_dir
    wiki = WikiService(ManagedFileRepository(vault / "wiki" / "pages", history_root=vault / "wiki" / ".wiki-history"))
    wiki.create_page(page_id="managed", path="managed.md", title="Managed")
    wiki_before = {path.relative_to(vault).as_posix(): path.read_bytes() for path in vault.rglob("*") if path.is_file()}

    search_writes: list[str] = []

    class _SearchStore:
        async def clear_source(self, source: str):
            search_writes.append(f"clear:{source}")

        async def get_indexed_hashes(self, source: str):
            search_writes.append(f"hashes:{source}")
            return {}

    class _SearchIndex:
        store = _SearchStore()

        async def delete(self, source: str, source_id: str):
            search_writes.append(f"delete:{source}:{source_id}")

        async def upsert(self, **_kwargs):
            search_writes.append("upsert")
            return True

    runtime = Runtime(config)

    async def init_search_probe():
        runtime.knowledge.search_index = _SearchIndex()

    monkeypatch.setattr(runtime.knowledge, "_init_search", init_search_probe)
    await runtime.connect()
    try:
        knowledge = runtime.knowledge
        store = knowledge.record_store
        assert knowledge.memory_writes_enabled is False
        assert store.writes_enabled is False
        assert knowledge._daily_projection is not None
        assert knowledge._daily_projection._task is None
        assert knowledge._artifact_refresh_task is None

        service = knowledge.page_edit_service
        with pytest.raises(PermissionError, match="managed wiki cutover"):
            await service.preview(
                path="legacy.md",
                base_revision=page_revision(b""),
                content=b"# Changed\n",
                actor="user:desktop",
            )
        with pytest.raises(PermissionError, match="managed wiki cutover"):
            store.commit_generated_projection(
                Path("daily/2026-07-28.md"),
                b"# Daily\n",
                None,
                store.canonical_revision,
            )
        with pytest.raises(PermissionError, match="managed wiki cutover"):
            await store.add("must not enter the legacy ledger")

        assert not (vault / "daily" / "2026-07-28.md").exists()
        assert not (vault / "raw").exists()
        assert not (vault / ".arden").exists()
        assert not (vault / ".index").exists()
        assert not (vault / "AGENTS.md").exists()
        assert search_writes == []
        assert wiki_before == {
            path.relative_to(vault).as_posix(): path.read_bytes() for path in vault.rglob("*") if path.is_file()
        }
        assert runtime.automation is not None
        result = await runtime.automation._build_memory_synthesize_handler()(None)
        assert result == "legacy memory writes disabled after managed wiki cutover"
    finally:
        await runtime.close()
