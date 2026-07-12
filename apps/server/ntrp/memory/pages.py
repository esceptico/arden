"""Karpathy-layered memory page: the visible page file is COMPILED PROSE only
(the wiki artifact a human reads); its append-only dated TIMELINE of record lines
lives in a raw/<same-path>.md sidecar (the machine layer). A timeline line is the
canonical record — it round-trips a Record. Prose is human-facing and optional
(filled later by synthesis); the store reads the timeline.

Line shape (readable, parseable):
    - 2026-06-21 ^a1b2c3d4 [fact] (src:curator) Tim rides a gravel bike.
    - 2026-06-21 ^a1b2c3d4 [fact] [pin] (src:user) Tim's wife is Lena.
    - ~~2026-04-02 ^7a1b2c3d [fact] (src:chat) old claim~~   (superseded)

Frontmatter splits by owner: human-facing keys (title, updated, aliases, type)
render into the page file; engine bookkeeping (RAW_FM_KEYS) renders into the raw
sidecar. Scope is positional (derived from the page + kind), not encoded per line:
directives -> global; project pages carry scope_key; everything else -> user.
ponytail: minimal frontmatter parser (we control what we write); no yaml dep.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ntrp.memory.ledger import LedgerEntry, parse_ledger_entry, render_ledger_entry

SENTINEL = (
    "<!-- timeline (append-only; edit prose above, not below) -->"  # legacy two-zone marker, parsed for migration only
)

# Engine bookkeeping — rendered into the raw sidecar, never the visible page.
# prose_cites is the page's grounding: the record ids its prose was verified
# against at synthesis time (the visible prose carries readable source tags,
# not ids — this list is what keeps the grounding machine-checkable).
RAW_FM_KEYS = ("scope_key", "entity_labels", "meta_labels", "prose_synced", "prose_cites")
# Legacy bookkeeping now computed from the prose itself — dropped on parse.
_DROPPED_FM_KEYS = ("prose_tokens",)
_V2_HEADER_RE = re.compile(r"^<!-- ntrp:records schema=2(?: [^>]*)? -->$")

# Greedy text + bracket tags only (incl. [superseded]) so the text body can
# contain ANYTHING — trailing ~~, "(superseded)", brackets — without ambiguity.
_LINE_RE = re.compile(
    r"^- "
    r"(?P<date>\d{4}-\d{2}-\d{2}) "
    r"\^(?P<id>\w+) "
    r"\[(?P<kind>[\w-]+)\]"
    r"(?P<pin> \[pin\])?(?P<imp> \[imp:\d+\])?(?P<sup> \[superseded\])?(?P<ent> \[ent:[a-z0-9-]+\])? "
    r"\(src:(?P<src>[^)]*)\) "
    r"(?P<text>.*)$"
)


@dataclass
class Line:
    id: str
    text: str
    kind: str = "fact"
    date: str = ""  # YYYY-MM-DD (last_confirmed)
    src: str = "unknown"
    pinned: bool = False
    superseded: bool = False
    imp: int | None = None  # 1-10 poignancy; None = unscored (ranks as neutral 5)
    entity: str | None = None  # slug of the primary entity; drives page promotion (None = no entity)


def format_line(line: Line) -> str:
    pin = " [pin]" if line.pinned else ""
    imp = f" [imp:{line.imp}]" if line.imp is not None else ""
    sup = " [superseded]" if line.superseded else ""
    ent = f" [ent:{line.entity}]" if line.entity else ""
    return f"- {line.date} ^{line.id} [{line.kind}]{pin}{imp}{sup}{ent} (src:{line.src}) {line.text}"


def parse_line(raw: str) -> Line | None:
    # rstrip only the newline, not spaces — an empty text body keeps its trailing
    # separator space so the line still round-trips.
    m = _LINE_RE.match(raw.rstrip("\r\n"))
    if not m:
        return None
    return Line(
        id=m["id"],
        text=m["text"],
        kind=m["kind"],
        date=m["date"],
        src=m["src"],
        pinned=bool(m["pin"]),
        superseded=bool(m["sup"]),
        imp=(int(m["imp"].strip().removeprefix("[imp:").removesuffix("]")) if m["imp"] else None),
        entity=(m["ent"].strip().removeprefix("[ent:").removesuffix("]") if m["ent"] else None),
    )


# -- frontmatter (tiny; scalars + JSON-list values) --------------------------


def _parse_frontmatter(block: str) -> dict:
    fm: dict = {}
    for ln in block.splitlines():
        if not ln.strip() or ":" not in ln:
            continue
        key, _, val = ln.partition(":")
        key, val = key.strip(), val.strip()
        if val.startswith("[") and val.endswith("]"):
            try:
                fm[key] = json.loads(val)
            except ValueError:
                fm[key] = []
        else:
            fm[key] = val
    return fm


def _dump_frontmatter(fm: dict) -> str:
    out = []
    for key, val in fm.items():
        if isinstance(val, list):
            out.append(f"{key}: {json.dumps(val, ensure_ascii=False)}")
        else:
            out.append(f"{key}: {val}")
    return "\n".join(out)


@dataclass
class Page:
    frontmatter: dict = field(default_factory=dict)
    prose: str = ""
    lines: list[Line | LedgerEntry] = field(default_factory=list)
    records_header: str | None = None

    def active_lines(self) -> list[Line | LedgerEntry]:
        return [
            ln
            for ln in self.lines
            if not (ln.meta.operation == "retract" if isinstance(ln, LedgerEntry) else ln.superseded)
        ]


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return _parse_frontmatter(text[4:end]), text[end + 5 :]
    return {}, text


def parse_page(text: str) -> Page:
    """Parse a page file. Tolerates the legacy two-zone format (prose + SENTINEL +
    timeline in one file) so a pre-split vault migrates on first load."""
    fm, body = _split_frontmatter(text)
    for key in _DROPPED_FM_KEYS:
        fm.pop(key, None)
    if not isinstance(fm.get("prose_cites"), list):  # legacy int count — superseded by the id list
        fm.pop("prose_cites", None)
    prose, _, timeline = body.partition(SENTINEL)
    lines = [ln for ln in (parse_line(r) for r in timeline.splitlines()) if ln]
    return Page(frontmatter=fm, prose=prose.strip(), lines=lines)


def parse_raw(text: str) -> tuple[dict, list[Line | LedgerEntry]]:
    """Parse a raw sidecar: engine frontmatter + every parseable timeline line."""
    fm, body = _split_frontmatter(text)
    rows = body.splitlines()
    first = next((index for index, row in enumerate(rows) if row.strip()), None)
    header_rows = [index for index, row in enumerate(rows) if _V2_HEADER_RE.fullmatch(row)]
    if header_rows and first != header_rows[0]:
        raise ValueError("schema-v2 header must be the first nonempty raw line")
    if first is not None and header_rows:
        entries: list[LedgerEntry] = []
        index = first + 1
        while index < len(rows):
            if not rows[index].strip():
                index += 1
                continue
            if index + 1 >= len(rows) or not rows[index + 1].lstrip().startswith("<!-- ntrp:meta "):
                raise ValueError("schema-v2 record is missing its metadata comment")
            entries.append(parse_ledger_entry(f"{rows[index]}\n{rows[index + 1]}"))
            index += 2
        return fm, entries
    return fm, [ln for ln in (parse_line(r) for r in body.splitlines()) if ln]


def merge_split(page: Page, raw_text: str | None) -> Page:
    """Attach a raw sidecar's frontmatter + timeline onto a parsed page."""
    if raw_text:
        _, raw_body = _split_frontmatter(raw_text)
        fm, lines = parse_raw(raw_text)
        page.records_header = next(
            (row for row in raw_body.splitlines() if _V2_HEADER_RE.fullmatch(row)),
            None,
        )
        page.frontmatter.update(fm)
        page.lines.extend(lines)
    return page


def render_page(page: Page) -> str:
    """The visible wiki page: human frontmatter + compiled prose. No timeline."""
    fm = {k: v for k, v in page.frontmatter.items() if k not in RAW_FM_KEYS}
    parts = []
    if fm:
        parts.append("---\n" + _dump_frontmatter(fm) + "\n---")
    if page.prose:
        parts.append(page.prose)
    return "\n\n".join(parts).rstrip() + "\n"


def render_raw(page: Page) -> str:
    """The machine sidecar: engine frontmatter + timeline. Empty string when the
    page carries neither (caller removes the sidecar file)."""
    fm = {k: page.frontmatter[k] for k in RAW_FM_KEYS if k in page.frontmatter}
    if not fm and not page.lines and page.records_header is None:
        return ""
    parts = []
    if fm:
        parts.append("---\n" + _dump_frontmatter(fm) + "\n---")
    if page.records_header is not None:
        if any(not isinstance(ln, LedgerEntry) for ln in page.lines):
            raise ValueError("schema-v2 pages can contain only ledger entries")
        ledger = [render_ledger_entry(ln) for ln in page.lines]
        parts.append("\n".join((page.records_header, *ledger)))
    elif page.lines:
        parts.append("\n".join(format_line(ln) for ln in page.lines))
    return "\n\n".join(parts).rstrip() + "\n"


if __name__ == "__main__":
    # ponytail: round-trip self-check
    src = Line(id="a1b2c3d4", text="Tim rides a gravel bike.", kind="fact", date="2026-06-21", src="curator")
    assert parse_line(format_line(src)) == src, parse_line(format_line(src))
    pinned = Line(id="ff00", text="Wife is Lena.", kind="fact", date="2026-06-21", src="user", pinned=True)
    assert parse_line(format_line(pinned)).pinned
    dead = Line(id="7a1b", text="old claim", kind="fact", date="2026-04-02", src="chat", superseded=True)
    rt = parse_line(format_line(dead))
    assert rt.superseded and rt.text == "old claim", rt
    scored = Line(id="bb11", text="scored fact", kind="fact", date="2026-06-21", src="user", pinned=True, imp=7)
    assert parse_line(format_line(scored)) == scored, parse_line(format_line(scored))
    legacy = parse_line("- 2026-06-21 ^cc22 [fact] (src:curator) no imp tag")  # back-compat
    assert legacy is not None and legacy.imp is None and legacy.entity is None, legacy
    # per-line entity slug round-trips and coexists with other tags
    ent = Line(id="9911", text="works at Dex.", kind="fact", date="2026-06-21", src="curator", entity="dex-nexus")
    assert parse_line(format_line(ent)) == ent, parse_line(format_line(ent))
    ent_pin = Line(
        id="9922",
        text="pinned + entity.",
        kind="fact",
        date="2026-06-21",
        src="user",
        pinned=True,
        imp=6,
        entity="regina-lin",
    )
    assert parse_line(format_line(ent_pin)) == ent_pin, parse_line(format_line(ent_pin))
    # the entity tag lives in the positionally-locked bracket region, so a text body
    # that itself starts with a "(ent:..)"/"[ent:..]"-shaped token round-trips verbatim
    bait1 = Line(id="9933", text="(ent:roadmap) blockers cleared", kind="fact", date="2026-06-21", src="chat")
    assert parse_line(format_line(bait1)) == bait1, parse_line(format_line(bait1))
    bait2 = Line(id="9944", text="[ent:foo] is just prose", kind="fact", date="2026-06-21", src="chat")
    assert parse_line(format_line(bait2)) == bait2, parse_line(format_line(bait2))
    # regression: text ending in ~~ or containing "(superseded)" must survive verbatim
    tricky = Line(id="dd33", text="deprecated ~~old API~~", kind="fact", date="2026-06-21", src="curator")
    assert parse_line(format_line(tricky)) == tricky, parse_line(format_line(tricky))
    paren = Line(id="ee44", text="issue resolved (superseded) per Lena", kind="fact", date="2026-06-21", src="chat")
    assert parse_line(format_line(paren)) == paren, parse_line(format_line(paren))
    page = Page(
        frontmatter={"type": "topic", "title": "Bicycles", "entity_labels": ["Bicycles"]},
        prose="Tim's bikes.",
        lines=[src, pinned, dead],
    )
    # split round-trip: page file carries prose + human fm; raw carries engine fm + timeline
    page_text, raw_text = render_page(page), render_raw(page)
    assert "entity_labels" not in page_text and "^a1b2c3d4" not in page_text, page_text
    assert "entity_labels" in raw_text and "^a1b2c3d4" in raw_text, raw_text
    rp = merge_split(parse_page(page_text), raw_text)
    assert rp.frontmatter["title"] == "Bicycles"
    assert rp.frontmatter["entity_labels"] == ["Bicycles"]
    assert len(rp.lines) == 3 and len(rp.active_lines()) == 2
    assert rp.prose == "Tim's bikes."
    # legacy two-zone file still parses (migration path), incl. dropped bookkeeping keys
    legacy_text = (
        "---\ntitle: Bicycles\nprose_tokens: 12\nprose_cites: 3\n---\n\nTim's bikes.\n\n"
        + SENTINEL
        + "\n\n"
        + format_line(src)
        + "\n"
    )
    lp = parse_page(legacy_text)
    assert lp.prose == "Tim's bikes." and len(lp.lines) == 1 and "prose_tokens" not in lp.frontmatter, lp
    # prose-only page (no fm-worthy machine state, no lines) needs no raw sidecar
    assert render_raw(Page(frontmatter={"title": "2026-07-02"}, prose="Did things.")) == ""
    print("pages.py self-check OK")
