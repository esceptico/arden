from pathlib import Path

import pytest

from ntrp.areas.paths import resolve_area_page


def test_resolve_area_page_accepts_contained_markdown(tmp_path: Path) -> None:
    vault = tmp_path / "memory"
    page = vault / "topics" / "health.md"
    page.parent.mkdir(parents=True)
    page.write_text("# Health")

    assert resolve_area_page(vault, "topics/health.md") == page.resolve()


@pytest.mark.parametrize("page_path", ["/etc/passwd", "../secrets.md", "topics/../../secrets.md", "topics/x.txt"])
def test_resolve_area_page_rejects_unsafe_paths(tmp_path: Path, page_path: str) -> None:
    with pytest.raises(ValueError, match="vault-relative Markdown"):
        resolve_area_page(tmp_path / "memory", page_path)


def test_resolve_area_page_rejects_symlink_escape(tmp_path: Path) -> None:
    vault = tmp_path / "memory"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret")
    vault.mkdir()
    (vault / "topics").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes the memory vault"):
        resolve_area_page(vault, "topics/secret.md")
