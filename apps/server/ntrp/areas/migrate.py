"""One-shot fold of the legacy slices.json era into the areas table.

Runs once at boot: creates/updates areas with area capabilities, re-keys
ask records and area automations from page slugs to area_ids, links
stranded area-tagged sessions, then renames slices.json → .migrated so the
scan never runs again. Idempotent by construction — no file, no work."""

import json
from pathlib import Path

from ntrp.constants import AREAS_STATE_FILE, AREAS_SUGGESTIONS_FILE
from ntrp.logging import get_logger

_logger = get_logger(__name__)


def migrate_state_files(ntrp_dir: Path) -> None:
    """slices-*.json -> areas-*.json (2026-07-10 areas rename), rewriting the
    asks' persisted slice_key field. Idempotent: no-ops once the new files
    exist; the old state file is kept as .migrated."""
    old_state = ntrp_dir / "slices-state.json"
    new_state = ntrp_dir / AREAS_STATE_FILE
    if old_state.exists() and not new_state.exists():
        data = json.loads(old_state.read_text())
        for ask in data.get("asks", []):
            if "slice_key" in ask:
                ask["area_key"] = ask.pop("slice_key")
        new_state.write_text(json.dumps(data, indent=2))
        old_state.rename(old_state.with_suffix(".json.migrated"))
    old_sugg = ntrp_dir / "slices-suggestions.json"
    new_sugg = ntrp_dir / AREAS_SUGGESTIONS_FILE
    if old_sugg.exists() and not new_sugg.exists():
        old_sugg.rename(new_sugg)


def _slug(name: str) -> str:
    return name.strip().lower().replace(" ", "-")


async def migrate_legacy_areas(
    *,
    areas_file: Path,
    session_service,
    ask_store,
    automation_store,
    session_store,
) -> dict | None:
    if not areas_file.exists():
        return None
    # Legacy file format: {"slices": [...]} — the pre-rename registry.
    entries = json.loads(areas_file.read_text()).get("slices", [])

    # 1. Fold registry entries into areas (reuse slug-matched rows — the
    #    slug rule's last stand — else create).
    areas = await session_service.list_areas()
    by_slug = {_slug(p["name"]): p for p in areas}
    key_to_area: dict[str, str] = {}
    for entry in entries:
        existing = by_slug.get(entry["key"])
        if existing:
            await session_service.update_area(
                existing["area_id"], page_path=entry["page_path"], autonomy=entry["autonomy"]
            )
            key_to_area[entry["key"]] = existing["area_id"]
        else:
            created = await session_service.create_area(
                name=entry["title"], page_path=entry["page_path"], autonomy=entry["autonomy"]
            )
            key_to_area[entry["key"]] = created["area_id"]

    # 2. Sessions: link area-tagged rows to their area. The area_key
    #    column is dead after this — only this raw-SQL reader touches it.
    rows = await session_store.list_area_tagged_sessions()
    for row in rows:
        area_id = key_to_area.get(row["area_key"])
        if area_id and not row["area_id"]:
            await session_service.move_session_to_area(row["session_id"], area_id)

    # 3. Asks: slug → area_id in place.
    for ask in ask_store.list(include_resolved=True):
        if ask.area_key in key_to_area:
            ask.area_key = key_to_area[ask.area_key]
    ask_store._flush()

    # 4. Automations: area:{slug} → area:{area_id} across task tables,
    #    plus the channel sessions that reference the automation as origin.
    for key, area_id in key_to_area.items():
        for legacy_id in (f"slice:{key}", f"area:{key}"):
            await automation_store.rewrite_task_id(legacy_id, f"area:{area_id}")
            await session_store.rewrite_origin_automation_id(legacy_id, f"area:{area_id}")

    areas_file.rename(areas_file.with_suffix(".json.migrated"))
    summary = {"areas": len(entries), "sessions": len(rows)}
    _logger.info("Areas→areas migration complete: %s", summary)
    return summary
