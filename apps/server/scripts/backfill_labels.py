"""Label the EXISTING record corpus — the one-shot backfill for the labels
substrate. Reads every active record, batches them through the memory LLM with
the curator's label rules (reuse-before-mint against the vocabulary accumulated
across batches), and applies the labels via add_labels. Additive writes only,
so it is safe to run with the server up. Re-runnable: records that already
carry labels are skipped.

    uv run python -m scripts.backfill_labels
"""

import asyncio
import json

from arden.config import get_config
from arden.llm.models import get_models
from arden.llm.router import get_completion_client
from arden.memory.records import RecordStore

BATCH_SIZE = 25
VOCAB_LIMIT = 40

_SYSTEM_PROMPT = (
    "You attach LABELS to atomic memory records about the user and their world. "
    "Labels are open-vocabulary names — BOTH referents (people, pets, projects, "
    "tools, medications — anything nameable, non-physical included) AND "
    "categories (traits, bugs, open loops, health). Return a SINGLE JSON object "
    "mapping each record id to its labels:\n"
    '{"<record id>": ["<label>", ...], ...}\n'
    "Rules:\n"
    "(1) REUSE an existing label from the VOCABULARY whenever it fits, with its "
    "exact casing. Mint a new label only when nothing existing fits.\n"
    "(2) 1-4 labels per record. Labels are short names, not sentences.\n"
    "(3) Use ONLY the record ids given; give every record an entry. Output ONLY "
    "the JSON object, no preamble."
)


def _effort(config, model_id):
    if not model_id:
        return None
    if (configured := config.reasoning_effort_for(model_id)):
        return configured
    efforts = get_models()[model_id].reasoning_efforts
    return ("low" if "low" in efforts else efforts[0]) if efforts else None


def _parse(content: str) -> dict | None:
    body = content.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[-1]
        if body.endswith("```"):
            body = body[: body.rfind("```")]
        body = body.strip()
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


async def _label_batch(store: RecordStore, llm, model, effort, batch) -> int:
    vocab = (await store.list_labels())[:VOCAB_LIMIT]
    vocab_block = (
        "\n".join(f"- {v['label']} ({v['count']})" for v in vocab) if vocab else "(empty)"
    )
    records_block = "\n".join(f"- {r.id}: {r.text}" for r in batch)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"LABEL VOCABULARY (label (count) — reuse these names when they fit):\n"
                f"{vocab_block}\n\nRECORDS:\n{records_block}"
            ),
        },
    ]
    resp = await llm.completion(messages=messages, model=model, reasoning_effort=effort)
    content = resp.choices[0].message.content if resp.choices else None
    mapping = _parse(content) if content else None
    if mapping is None:
        return 0
    valid_ids = {r.id for r in batch}
    labeled = 0
    for rid, labels in mapping.items():
        if rid not in valid_ids or not isinstance(labels, list):
            continue
        names = [name for name in labels if isinstance(name, str) and name.strip()][:4]
        if names:
            await store.add_labels(rid, names)
            labeled += 1
    return labeled


async def main() -> None:
    config = get_config()
    if not config.memory_model:
        print("no memory_model configured")
        return
    llm = get_completion_client(config.memory_model)
    effort = _effort(config, config.memory_model)
    store = RecordStore(db_path=config.memory_db_path, search_index=None)
    try:
        total = await store.count_active()
        records = await store.list(limit=total)
        already = await store.labels_for([r.id for r in records])
        pending = [r for r in records if not already[r.id]]
        print(f"{total} active records, {len(pending)} unlabeled", flush=True)

        batches = [pending[i : i + BATCH_SIZE] for i in range(0, len(pending), BATCH_SIZE)]
        for n, batch in enumerate(batches, start=1):
            labeled = await _label_batch(store, llm, config.memory_model, effort, batch)
            vocab_size = len(await store.list_labels())
            print(
                f"batch {n}/{len(batches)}: labeled {labeled}/{len(batch)} records"
                f" | vocabulary: {vocab_size} labels",
                flush=True,
            )

        labels = await store.list_labels()
        print(f"DONE: {len(labels)} labels over the active pool", flush=True)
        for entry in labels[:VOCAB_LIMIT]:
            print(f"  {entry['count']:>4}  {entry['label']}", flush=True)
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
