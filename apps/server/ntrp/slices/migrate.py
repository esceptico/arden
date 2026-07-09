"""One-shot fold of the slices.json era into the projects table.

Runs once at boot: creates/updates projects with slice capabilities, re-keys
ask records and slice automations from page slugs to project_ids, links
stranded slice-tagged sessions, then renames slices.json → .migrated so the
scan never runs again. Idempotent by construction — no file, no work."""

import json
from pathlib import Path

from ntrp.logging import get_logger

_logger = get_logger(__name__)


def _slug(name: str) -> str:
    return name.strip().lower().replace(" ", "-")


async def migrate_slices_to_projects(
    *,
    slices_file: Path,
    session_service,
    ask_store,
    automation_store,
    session_store,
) -> dict | None:
    if not slices_file.exists():
        return None
    entries = json.loads(slices_file.read_text()).get("slices", [])

    # 1. Fold registry entries into projects (reuse slug-matched rows — the
    #    slug rule's last stand — else create).
    projects = await session_service.list_projects()
    by_slug = {_slug(p["name"]): p for p in projects}
    key_to_project: dict[str, str] = {}
    for entry in entries:
        existing = by_slug.get(entry["key"])
        if existing:
            await session_service.update_project(
                existing["project_id"], page_path=entry["page_path"], autonomy=entry["autonomy"]
            )
            key_to_project[entry["key"]] = existing["project_id"]
        else:
            created = await session_service.create_project(
                name=entry["title"], page_path=entry["page_path"], autonomy=entry["autonomy"]
            )
            key_to_project[entry["key"]] = created["project_id"]

    # 2. Sessions: link slice-tagged rows to their project. The slice_key
    #    column is dead after this — only this raw-SQL reader touches it.
    rows = await session_store.list_slice_tagged_sessions()
    for row in rows:
        project_id = key_to_project.get(row["slice_key"])
        if project_id and not row["project_id"]:
            await session_service.move_session_to_project(row["session_id"], project_id)

    # 3. Asks: slug → project_id in place.
    for ask in ask_store.list(include_resolved=True):
        if ask.slice_key in key_to_project:
            ask.slice_key = key_to_project[ask.slice_key]
    ask_store._flush()

    # 4. Automations: slice:{slug} → slice:{project_id} across task tables,
    #    plus the channel sessions that reference the automation as origin.
    for key, project_id in key_to_project.items():
        await automation_store.rewrite_task_id(f"slice:{key}", f"slice:{project_id}")
        await session_store.rewrite_origin_automation_id(f"slice:{key}", f"slice:{project_id}")

    slices_file.rename(slices_file.with_suffix(".json.migrated"))
    summary = {"slices": len(entries), "sessions": len(rows)}
    _logger.info("Slices→projects migration complete: %s", summary)
    return summary
