"""~20-probe memory eval (research doc §11/§13: "a tiny eval keeps the knobs honest").

A 20-minute spot-check, NOT a gate: run real questions the user would ask against
the memory store and check recall surfaces the supporting record. Pass = any
expected substring appears in the top-k recall results. Runs against a COPY of the
live memory (never mutates the running server's dir).

    uv run python -m scripts.memory_eval            # against ~/.arden/memory (copied)
    uv run python -m scripts.memory_eval <dir>      # against another memory dir

Probes are user-specific by design — edit PROBES for your own memory. Lexical-only
here (no embedder attached); the live server's vector leg does better on paraphrase,
so a lexical FAIL flags either a real gap OR a paraphrase the vector leg would catch.
"""

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

from arden.memory.file_store import FilePageStore

# (question, [any-of expected substrings, lowercased]) — grounded in the user's wiki.
PROBES: list[tuple[str, list[str]]] = [
    ("what bike does the user ride", ["trek", "marlin", "gravel", "bike"]),
    ("when is the MATS application deadline", ["june 23", "tuesday", "aoe"]),
    ("where does the user live", ["yerevan", "armenia"]),
    ("who does the user work for", ["dex", "thirdlayer"]),
    ("what visa is the user pursuing", ["o-1a", "o1a"]),
    ("who is Regina", ["regina", "founder", "product"]),
    ("who is Kevin Gu", ["kevin", "engineering", "technical"]),
    ("what is Ostium", ["ostium", "crm", "vip", "trader"]),
    ("who is the user's accountant for contractor paperwork", ["naira", "invoice", "act"]),
    ("what design aesthetic does the user want", ["vercel", "linear"]),
    ("what is Interaction Lab", ["interaction lab", "transitions", "playground", "demos"]),
    ("does the user have pets", ["cat", "cats"]),
    ("what is the user's native language", ["russian"]),
    ("what was the user's role at Replika", ["replika", "post-training", "dpo", "safety"]),
    ("what is arden", ["arden", "assistant", "agent os", "memory"]),
    ("what is the Nexus project", ["nexus", "context view", "computed", "column"]),
    ("what does the user prefer about committing code", ["review", "commit", "before"]),
    ("what is the user's primary github handle", ["esceptico"]),
    ("what is the user studying / applying to in AI safety", ["mats", "alignment", "anthropic", "safety"]),
    ("what tools does the user use for the vault", ["obsidian", "vault"]),
]


async def run(root: Path) -> int:
    store = FilePageStore(root)
    await store.open()
    passed = 0
    print(f"memory eval — {len(PROBES)} probes against {root}\n")
    for query, expected in PROBES:
        # Mirror the recall tool's default (topical: fact+source; directives/lessons
        # are resident, not recalled). Lexical-only here — the live server's vector
        # leg lifts this materially (a lexical FAIL may be a paraphrase it would catch).
        hits = await store.search(query, limit=6, scopes=None, kinds=["fact", "source"])
        blob = " ".join(h.text.lower() for h in hits)
        ok = any(e.lower() in blob for e in expected)
        passed += ok
        if ok:
            print(f"  PASS  {query}")
        else:
            print(f"  FAIL  {query}  (expected any of {expected})")
    print(f"\n{passed}/{len(PROBES)} probes passed ({100 * passed // len(PROBES)}%)")
    return passed


async def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / ".arden" / "memory"
    tmp = Path(tempfile.mkdtemp()) / "memory"
    shutil.copytree(src, tmp)  # never open the live dir directly (it would reconcile/rewrite)
    try:
        await run(tmp)
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
