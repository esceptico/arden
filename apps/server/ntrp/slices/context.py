from pathlib import Path

from ntrp.memory.pages import parse_page


def load_slice_context(vault_dir: Path, project: dict | None) -> dict | None:
    """Prompt context for a chat filed into a slice: the container's title +
    topic page prose. None for plain containers or a missing page — the chat
    degrades to an ordinary project chat rather than failing the run."""
    if not project or not project.get("page_path"):
        return None
    page_file = vault_dir / project["page_path"]
    if not page_file.exists():
        return None
    page = parse_page(page_file.read_text())
    return {"title": project["name"], "page": page.prose.strip()}
