"""A) AGENTS.md operating manual loaded by the maintenance passes + B) the dream
per-automation learnings loop. Hermetic: scripted fake LLM, real tmp FilePageStore."""

from pathlib import Path

import pytest

from ntrp.memory.dreamer import run_dream
from ntrp.memory.file_store import FilePageStore, load_conventions
from ntrp.memory.maintenance import _path, append_learnings, read_learnings
from ntrp.memory.models import TRUST_LEVEL, SourceRef

pytestmark = pytest.mark.asyncio


# -- A: operating manual ------------------------------------------------------


def test_operating_manual_renders_trust_from_code():
    man = load_conventions()
    assert "## Source trust" in man and "## Grounding" in man and "## Authoring" in man
    for src in TRUST_LEVEL:  # every enforced trust source is documented
        assert src in man, src
    assert man.index("user") < man.index("dreamer"), "descending trust order"
    # the two learnings channels are delineated so they don't read as parallel systems
    assert "lessons.md" in man and ".maintenance/" in man


def test_manual_documents_both_cite_dialects():
    man = load_conventions()
    assert "(record:<8hex>)" in man and "(because of ^id1, ^id2)" in man


# -- B: .maintenance exclusion + learnings loop -------------------------------


async def test_maintenance_dir_excluded_from_store(tmp_path: Path):
    root = Path(tmp_path)
    (root / "me.md").write_text("# Me\n\nhi\n", encoding="utf-8")
    md = root / ".maintenance"
    md.mkdir()
    (md / "memory_dream-learnings.md").write_text("# memory_dream\n\n- 2026-06-24 a gotcha\n", encoding="utf-8")
    store = FilePageStore(root)
    await store.open()
    assert not any(".maintenance" in p.parts for p in store._pages), list(store._pages)
    assert all(".maintenance" not in p.parts for p in store._loc.values())


def test_learnings_bounded_and_deduped(tmp_path: Path):
    root = Path(tmp_path)
    append_learnings(root, "memory_dream", [f"gotcha {i}" for i in range(25)], date="2026-06-24")
    append_learnings(root, "memory_dream", ["gotcha 0", "gotcha 24"], date="2026-07-02")  # cross-day dups
    text = _path(root, "memory_dream").read_text(encoding="utf-8")
    bullets = [ln for ln in text.splitlines() if ln.startswith("- ")]
    assert len(bullets) <= 20, len(bullets)
    assert "gotcha 24" in text and text.count("gotcha 24") == 1, "cross-day dedup"
    assert read_learnings(root, "memory_dream").count("\n") + 1 == len(bullets)


# -- B: dream emits a LEARNINGS trailer that is NOT ingested as a record -------


class _DreamLLM:
    """Turn 1: a question. Turn 2: a valid cross-domain insight + a LEARNINGS trailer."""

    def __init__(self, a: str, b: str):
        self._a, self._b, self.n, self.systems = a, b, 0, []

    async def completion(self, *, messages, model, reasoning_effort=None):
        self.n += 1
        self.systems.append(messages[0]["content"])
        if self.n == 1:
            content = "What connects the user's work and their tools?"
        else:
            # INLINE trailer (the hard case): the gotcha is collapsed onto the insight
            # line, not on its own line — it must still be stripped before ingest.
            content = (
                f"Work focus shapes tool choice. (because of ^{self._a}, ^{self._b}) "
                "LEARNINGS: evidence too thin to bridge finance and health"
            )
        msg = type("M", (), {"content": content})()
        return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()


async def test_dream_learnings_partitioned_and_conventions_injected(tmp_path: Path):
    store = FilePageStore(Path(tmp_path))
    await store.open()
    a = await store.add("The user works on Dex.", kind="fact", source_ref=SourceRef("user", ""), entity_labels=["Dex"])
    for t in ("The user prefers async.", "The user is in Armenia.", "The user uses Obsidian."):
        await store.add(t, kind="fact", source_ref=SourceRef("user", ""))
    b = await store.add("The Nexus review is scheduled weekly.", kind="fact", source_ref=SourceRef("user", ""), entity_labels=["Nexus"])
    await store.add("Regina runs a weekly 1:1.", kind="fact", source_ref=SourceRef("user", ""))

    llm = _DreamLLM(b.id, a.id)
    _summary, learnings = await run_dream(
        store, llm, "memory-model",
        conventions=load_conventions(), learnings="- prior gotcha to avoid",
    )

    # the LEARNINGS gotcha is returned for the sidecar, NOT minted as a dreamer record
    assert learnings == ["evidence too thin to bridge finance and health"], learnings
    dreamed = [r for r in await store.list(scopes=None, limit=None) if r.source_ref and r.source_ref.kind == "dreamer"]
    assert dreamed, "a real insight still written"
    assert not any("LEARNINGS" in r.text for r in dreamed), "the LEARNINGS line must never become a record"
    # the operating manual + prior learnings reached the dream prompts
    assert any("<operating_manual>" in s for s in llm.systems)
    assert any("prior gotcha to avoid" in s for s in llm.systems)


def test_curator_and_consolidate_prepend_conventions():
    # the manual is a leading system block; the tuned rubric/system prompt is untouched
    from ntrp.memory.curator import _SYSTEM_PROMPT
    from ntrp.memory.prompts_consolidate import LINT_RUBRIC

    man = load_conventions()
    composed_curator = f"<operating_manual>\n{man}\n</operating_manual>\n\n" + _SYSTEM_PROMPT
    assert composed_curator.startswith("<operating_manual>") and _SYSTEM_PROMPT in composed_curator
    assert "## Source trust" in man and LINT_RUBRIC  # rubric stays a separate, intact block
