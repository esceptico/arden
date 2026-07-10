import re

from ntrp.memory.pages import Page

_LOOP_HEADING = re.compile(r"^##\s+open loops\s*:?\s*$", re.IGNORECASE)
_RELATED_HEADING = re.compile(r"^##\s+related\s*:?\s*$", re.IGNORECASE)
_HEADING = re.compile(r"^#{1,6}\s")
_BULLET = re.compile(r"^[-*]\s+(.*)$")
_PROVENANCE = re.compile(r"\s*\((?:from chat|record:[^)]*)\)\.?\s*$")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")


def _section_bullets(prose: str, heading: re.Pattern[str]) -> list[str]:
    items: list[str] = []
    in_section = False
    for line in prose.splitlines():
        stripped = line.strip()
        if heading.match(stripped):
            in_section = True
            continue
        if in_section and _HEADING.match(stripped):
            break
        if in_section:
            m = _BULLET.match(stripped)
            if m:
                text = _MD_BOLD.sub(r"\1", m.group(1))
                items.append(_PROVENANCE.sub("", text).strip())
    return items


def parse_open_loops(prose: str) -> list[str]:
    return _section_bullets(prose, _LOOP_HEADING)


def parse_related(prose: str) -> list[str]:
    """Slugs of `[[Wiki Links]]` under the page's `## Related` heading —
    lowercased, spaces to dashes, matching topic-page/area keys."""
    slugs: list[str] = []
    for item in _section_bullets(prose, _RELATED_HEADING):
        for m in _WIKILINK.finditer(item):
            slugs.append(m.group(1).strip().lower().replace(" ", "-"))
    return slugs


def page_summary(page: Page) -> dict:
    return {
        "title": page.frontmatter.get("title", ""),
        "updated": str(page.frontmatter.get("updated", "")),
        "open_loops": parse_open_loops(page.prose),
        "related": parse_related(page.prose),
    }


def area_automation_match(task_id: str, key: str) -> bool:
    """Match an automation task_id against an area key.

    Seeded area automations carry the stable id `area:{key}` (see
    _seed_area_automations) while their display name is ordinary prose;
    colon-suffixed ids like `area:{key}:daily` are reserved for future
    sub-automations.
    """
    base = f"area:{key}"
    return task_id == base or task_id.startswith(f"{base}:")
