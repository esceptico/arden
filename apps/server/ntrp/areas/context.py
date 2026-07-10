from pathlib import Path

from ntrp.memory.pages import parse_page


def load_area_context(vault_dir: Path, area: dict | None) -> dict | None:
    """Prompt context for a chat filed into an area: the container's title +
    topic page prose. None for plain containers or a missing page — the chat
    degrades to an ordinary area chat rather than failing the run."""
    if not area or not area.get("page_path"):
        return None
    page_file = vault_dir / area["page_path"]
    if not page_file.exists():
        return None
    page = parse_page(page_file.read_text())
    return {"title": area["name"], "page": page.prose.strip()}
