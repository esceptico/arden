"""The always-resident memory profile rendered into the system prompt.

Directives + durable user facts + anything explicitly pinned, in the read
scopes — a small, char-bounded projection of the flat record pool. Pure DB I/O,
zero LLM. The same block feeds interactive chat and operator/automation runs so
both see the same standing memory. Deeper recall stays pull-only via Recall.
"""

from pathlib import Path

from ntrp.logging import get_logger
from ntrp.memory.scopes import scopes_for_read
from ntrp.memory.scorer import salience

_logger = get_logger(__name__)

PROFILE_RECORD_LIMIT = 60
# Separate slices so verbose directives can't starve durable facts (or vice
# versa) — each kind is guaranteed its own room every turn.
DIRECTIVE_CHAR_BUDGET = 3000
FACT_CHAR_BUDGET = 2000
LESSON_CHAR_BUDGET = 2000
SYNTHESIZED_PROSE_CHAR_LIMIT = 4000
ROOT_MAP_CHAR_BUDGET = 1500


def _root_map(memory_records: object) -> str | None:
    root = getattr(memory_records, "_root", None)
    if root is None:
        return None
    try:
        from ntrp.memory.vault_index import VaultIndexer

        entries = VaultIndexer(Path(root)).root_entries()
    except Exception:
        _logger.warning("memory root map load failed", exc_info=True)
        return None
    lines: list[str] = []
    used = 0
    for entry in entries:
        line = f"- {entry.path} — {entry.description}"
        remaining = ROOT_MAP_CHAR_BUDGET - used
        if len(line) > remaining:
            if lines or remaining <= 1:
                break
            line = line[: remaining - 1].rstrip() + "…"
        lines.append(line)
        used += len(line)
    return "## Memory map\n\n" + "\n".join(lines) if lines else None


async def _playbook(memory_records: object, scopes: list) -> str | None:
    """The continual-learning playbook: lesson records the agent has DISTILLED
    from past interactions, surfaced every turn so they're actually applied (the
    active half of continual learning — capture is useless if never read).
    Salience-ranked, not recency-ranked: the char budget should carry the BEST
    lessons, not whichever happened to be confirmed last."""
    try:
        lessons = await memory_records.list(kinds=["lesson"], scopes=scopes, limit=None)
    except Exception:
        _logger.warning("playbook load failed", exc_info=True)
        return None
    if not lessons:
        return None
    lessons.sort(key=lambda r: (salience(r.imp, r.last_confirmed_at), r.last_confirmed_at), reverse=True)
    lines: list[str] = []
    used = 0
    for r in lessons:
        line = f"- {r.text}"
        if lines and used + len(line) > LESSON_CHAR_BUDGET:
            break
        lines.append(line)
        used += len(line)
    return "## Playbook (learned)\n\n" + "\n".join(lines)


def _synthesized_prose(memory_records: object) -> str | None:
    """The synthesized me.md prose (the wiki view of the user), if the file store
    has produced one. Duck-typed: a non-FilePageStore (tests) returns None and the
    caller falls back to the bullet dump. Cheap — reads already-synthesized prose,
    no LLM."""
    pages = getattr(memory_records, "_pages", None)
    root = getattr(memory_records, "_root", None)
    if pages is None or root is None:
        return None
    page = pages.get(Path(root) / "me.md")
    if page is None or not getattr(page, "prose", ""):
        return None
    prose = page.prose.strip()
    if prose.startswith("# "):  # drop the synthesizer's own `# Name` h1 (the block is already under ## MEMORY CONTEXT)
        prose = prose.split("\n", 1)[1].lstrip() if "\n" in prose else ""
    if not prose:
        return None
    if len(prose) > SYNTHESIZED_PROSE_CHAR_LIMIT:
        head = prose[:SYNTHESIZED_PROSE_CHAR_LIMIT].rsplit("\n", 1)[0]
        prose = head or prose[:SYNTHESIZED_PROSE_CHAR_LIMIT]  # one-giant-line guard
    return prose


async def _directives_block(memory_records: object, scopes: list) -> str | None:
    """Standing behaviour rules, verbatim. Always resident — even on the synthesized-
    prose path — so a rule can't silently stop being enforced just because the prose
    synthesizer paraphrased or dropped it. Rules ride exactly as the user stated them."""
    try:
        directives = await memory_records.list(kinds=["directive"], scopes=scopes, limit=PROFILE_RECORD_LIMIT)
    except Exception:
        _logger.warning("directives load failed", exc_info=True)
        return None
    if not directives:
        return None
    lines: list[str] = []
    used = 0
    for r in directives:
        line = f"- {r.text}"
        if lines and used + len(line) > DIRECTIVE_CHAR_BUDGET:
            break
        lines.append(line)
        used += len(line)
    return "## Directives\n\n" + "\n".join(lines) if lines else None


def _take(records: list, budget: int) -> list[str]:
    lines: list[str] = []
    used = 0
    for record in records:
        line = f"- [{record.kind}] {record.text}"
        if lines and used + len(line) > budget:
            break
        lines.append(line)
        used += len(line)
    return lines


async def resident_profile(
    memory_records: object | None,
    *,
    project_context=None,
    session_id: str | None = None,
) -> str | None:
    """Project the standing memory the agent should carry every turn. Directives
    sort first so behaviour rules survive the char budget; the pool is small
    after the LINT pass, so durable facts ride along without a manual pin."""
    if memory_records is None:
        return None
    scopes = [(s.kind, s.key) for s in scopes_for_read(project=project_context, session_id=session_id)]
    # The distilled playbook is appended to whichever profile form we return, so
    # the agent's learned lessons ride along every turn.
    playbook = await _playbook(memory_records, scopes)
    root_map = _root_map(memory_records)

    def _join(base: str | None) -> str | None:
        parts = [p for p in (base, root_map, playbook) if p]
        return "\n\n".join(parts) if parts else None

    # Prefer the synthesized me.md prose (clean wiki block); fall back to the
    # recency/char-budget bullet dump only before the first synthesis has run.
    prose = _synthesized_prose(memory_records)
    if prose is not None:
        # Directives ride verbatim alongside the prose — never only via paraphrase.
        directives = await _directives_block(memory_records, scopes)
        parts = [p for p in (prose, directives, root_map, playbook) if p]
        return "\n\n".join(parts) if parts else None
    try:
        # Directives queried on their own so a flood of recent facts can't evict
        # behaviour rules by recency before the char budget even applies.
        directives = await memory_records.list(kinds=["directive"], scopes=scopes, limit=PROFILE_RECORD_LIMIT)
        facts = await memory_records.list(kinds=["fact"], scopes=scopes, limit=PROFILE_RECORD_LIMIT)
        pinned = await memory_records.list(pinned_only=True, scopes=scopes, limit=PROFILE_RECORD_LIMIT)
    except Exception:
        _logger.warning("profile records load failed", exc_info=True)
        return None

    seen: set[str] = set()

    def _dedup(records: list) -> list:
        out = []
        for record in records:
            if record.id in seen:
                continue
            seen.add(record.id)
            out.append(record)
        return out

    # Pinned records join their own kind's slice (a pinned summary/source rides
    # with the facts), so an explicit pin is always honoured.
    dir_recs = _dedup([*directives, *(p for p in pinned if p.kind == "directive")])
    fact_recs = _dedup([*(p for p in pinned if p.kind != "directive"), *facts])

    lines = _take(dir_recs, DIRECTIVE_CHAR_BUDGET) + _take(fact_recs, FACT_CHAR_BUDGET)
    return _join("## Profile\n\n" + "\n".join(lines) if lines else None)
