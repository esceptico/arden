from pathlib import Path

from arden.revisions import ManagedFileRepository
from arden.wiki.migrate import migrate_title_links
from arden.wiki.service import WikiService


def _service(tmp_path: Path) -> WikiService:
    return WikiService(ManagedFileRepository(tmp_path / "wiki"))


def test_title_links_become_path_links_with_display_text(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.create_page(path="topics/anthropic.md", title="Anthropic", aliases=("The Lab",))
    service.create_page(
        path="notes.md",
        title="Notes",
        body=b"See [[Anthropic]] and [[The Lab|the lab]] and [[Anthropic#Team|folks]].\n"
        b"Keep [[topics/anthropic]] and [[Missing]] as they are.\n",
    )

    commit_id = migrate_title_links(service)

    assert commit_id is not None
    record = next(item for item in service.list_pages() if item.resource.path == "notes.md")
    body = record.page.body.decode()
    assert "[[topics/anthropic|Anthropic]]" in body
    assert "[[topics/anthropic|the lab]]" in body
    assert "[[topics/anthropic#Team|folks]]" in body
    assert "[[topics/anthropic]] and [[Missing]]" in body

    links = service.links(record.page.page_id)
    resolved = [item for item in links if item.target_page_id is not None]
    assert len(resolved) == 4


def test_migration_is_idempotent_and_noop_without_title_links(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.create_page(path="topics/anthropic.md", title="Anthropic")
    service.create_page(path="notes.md", title="Notes", body=b"See [[Anthropic]].\n")

    assert migrate_title_links(service) is not None
    assert migrate_title_links(service) is None


def test_ambiguous_titles_stay_untouched(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.create_page(path="one.md", title="Same")
    service.create_page(path="two.md", title="same")
    service.create_page(path="notes.md", title="Notes", body=b"See [[Same]].\n")

    assert migrate_title_links(service) is None
