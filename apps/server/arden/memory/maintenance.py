"""Per-automation learnings sidecars — continual learning for the nightly maintenance
passes. A learnings file is a bounded, deduped, flat list of operational gotchas an
automation discovered: read at the START of a run (injected as orienting context),
appended after — so the passes stop running stateless every night. NO LLM here; read,
dedup, and cap are deterministic. Lives under <root>/.maintenance/ (excluded from the
record store, never injected into chat). Scoped to the dream pass for now — the only
maintenance pass that authors free text and can surface a genuine gotcha.
"""

from pathlib import Path

from arden.logging import get_logger

_logger = get_logger(__name__)

_DIR = ".maintenance"
_CAP = 20
_ITEM_MAX = 200  # a learnings bullet is "one short gotcha"; truncate a runaway line


def _path(root: Path, name: str) -> Path:
    return root / _DIR / f"{name}-learnings.md"


def _fingerprint(line: str) -> str:
    """Date-stripped, normalized key so a recurring gotcha is deduped regardless of day."""
    s = " ".join(line.split()).lower()
    head, _, rest = s.partition(" ")
    if len(head) == 10 and head.count("-") == 2:  # leading YYYY-MM-DD
        s = rest
    return s


def _bullets(text: str) -> list[str]:
    return [ln.strip()[2:].strip() for ln in text.splitlines() if ln.strip().startswith("- ")]


def read_learnings(root: Path, name: str) -> str:
    """The prior gotcha bullets (joined, one `- ` line each), or '' if none. Best-effort —
    a read failure must never blank the pass it feeds."""
    try:
        path = _path(root, name)
        if not path.exists():
            return ""
        return "\n".join(f"- {b}" for b in _bullets(path.read_text(encoding="utf-8")))
    except OSError:
        _logger.warning("read_learnings failed", exc_info=True)
        return ""


def append_learnings(root: Path, name: str, items: list[str], *, date: str, cap: int = _CAP) -> None:
    """Append new gotchas (deduped by date-stripped fingerprint), keep only the newest
    `cap`. Deterministic, no LLM; lazily creates .maintenance/. A failure is swallowed —
    learnings are an optimization, never load-bearing."""
    cleaned = [" ".join(i.split())[:_ITEM_MAX] for i in items if i and i.strip()]
    if not cleaned:
        return
    try:
        path = _path(root, name)
        existing = _bullets(path.read_text(encoding="utf-8")) if path.exists() else []
        seen = {_fingerprint(b) for b in existing}
        fresh: list[str] = []
        for item in cleaned:
            fp = _fingerprint(item)
            if fp and fp not in seen:
                seen.add(fp)
                fresh.append(f"{date[:10]} {item}")
        if not fresh:
            return
        kept = (existing + fresh)[-cap:]
        body = (
            f"# {name} — learnings (operational gotchas; read first, append after)\n\n"
            + "\n".join(f"- {b}" for b in kept)
            + "\n"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    except OSError:
        _logger.warning("append_learnings failed", exc_info=True)


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        assert read_learnings(root, "memory_dream") == "", "absent -> empty"
        # 25 items: 22 unique + an exact dup + a cross-day dup of an earlier one.
        items = [f"gotcha number {i}" for i in range(22)]
        append_learnings(root, "memory_dream", items, date="2026-06-24")
        append_learnings(root, "memory_dream", ["gotcha number 0", "gotcha number 5"], date="2026-07-01")
        text = _path(root, "memory_dream").read_text(encoding="utf-8")
        bullets = _bullets(text)
        assert len(bullets) <= _CAP, f"capped at {_CAP}, got {len(bullets)}"
        fps = [_fingerprint(b) for b in bullets]
        assert len(fps) == len(set(fps)), "date-stripped dedup: no duplicate gotcha"
        assert _path(root, "memory_dream").parent.name == _DIR, ".maintenance dir created"
        # newest survive the cap; oldest evicted
        assert "gotcha number 21" in text and "gotcha number 0" not in fps[:1]
        out = read_learnings(root, "memory_dream")
        assert out.count("\n") + 1 == len(bullets), "read returns the kept bullets"
        print("maintenance.py self-check OK")
