"""Resolve project scope ids → human names from the sessions.db projects table.

Project records file under projects/<scope_key>.md, where scope_key is an opaque
project id (e.g. "proj_5ff1d4f89866"). This maps it to the display name so pages
are named/titled by the human project name, not the id.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def load_project_names(memory_root: Path) -> dict[str, str]:
    """{project_id | "project:<id>" | knowledge_scope -> display name}, from
    ~/.ntrp/sessions.db (sibling of the memory dir). Empty on any error."""
    db_path = Path(memory_root).parent / "sessions.db"
    if not db_path.exists() or db_path.is_symlink():
        return {}
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT area_id, name, knowledge_scope FROM areas WHERE archived_at IS NULL"
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass
    names: dict[str, str] = {}
    for project_id, name, knowledge_scope in rows:
        if not project_id or not name:
            continue
        display = str(name).strip()
        if not display:
            continue
        names[str(project_id)] = display
        names[f"project:{project_id}"] = display
        if knowledge_scope:
            names[str(knowledge_scope)] = display
    return names


def resolve_project_title(page, names: dict[str, str]) -> str:
    key = page.frontmatter.get("scope_key")
    if key and str(key) in names:
        return names[str(key)]
    return str(page.frontmatter.get("title") or key or "Untitled")


if __name__ == "__main__":
    names = load_project_names(Path("~/.ntrp/memory").expanduser())
    print(f"loaded {len(names)} project-name keys")
    for k, v in list(names.items())[:10]:
        print(f"  {k} -> {v}")
