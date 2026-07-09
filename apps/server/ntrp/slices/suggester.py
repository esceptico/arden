import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from ntrp.logging import get_logger
from ntrp.memory.pages import parse_page

_logger = get_logger(__name__)

SLICE_SUGGESTER_SYSTEM = """You review the topic pages of a personal memory \
vault and identify which are LIFE DOMAINS — areas the user actively lives or \
works in, with personal stakes and an ongoing arc (a job, a visa process, a \
health concern, a long-running project of their own). A life domain benefits \
from a standing agent that watches it daily.

NOT life domains: reference notes about tools/companies/concepts, research \
digests, other people's products, one-off technical topics.

Suggest at most 3, best first. Be conservative — a wrong suggestion costs \
trust. For each, one plain-language sentence on why it deserves a standing \
agent, grounded in what the page shows."""


class SliceSuggestionDraft(BaseModel):
    key: str
    rationale: str


class SliceSuggestionSet(BaseModel):
    suggestions: list[SliceSuggestionDraft]


class SliceSuggestionStore:
    """Suggestions + dismissals in one small file. Dismissals persist so a
    rejected page is never re-suggested — silence costs nothing, nagging
    costs trust."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _read(self) -> dict:
        if not self._path.exists():
            return {"suggestions": [], "dismissed": []}
        return json.loads(self._path.read_text())

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2))

    def replace_suggestions(self, suggestions: list[dict]) -> None:
        data = self._read()
        dismissed = set(data["dismissed"])
        data["suggestions"] = [s for s in suggestions if s["key"] not in dismissed]
        self._write(data)

    def dismiss(self, key: str) -> None:
        data = self._read()
        if key not in data["dismissed"]:
            data["dismissed"].append(key)
        data["suggestions"] = [s for s in data["suggestions"] if s["key"] != key]
        self._write(data)

    def list(self, exclude_keys: set[str]) -> list[dict]:
        return [s for s in self._read()["suggestions"] if s["key"] not in exclude_keys]

    def exists(self) -> bool:
        return self._path.exists()


def candidate_pages(vault_dir: Path, attached_page_slugs: set[str]) -> list[dict]:
    """Unattached topic pages with enough content to judge: slug, title,
    updated date, and the prose head (the LLM sees substance, not just
    names). `attached_page_slugs` = page stems already attached to a slice."""
    topics = vault_dir / "topics"
    if not topics.exists():
        return []
    existing = attached_page_slugs
    out = []
    for path in sorted(topics.glob("*.md")):
        slug = path.stem
        if slug in existing:
            continue
        page = parse_page(path.read_text(encoding="utf-8"))
        out.append(
            {
                "key": slug,
                "title": str(page.frontmatter.get("title", slug)),
                "updated": str(page.frontmatter.get("updated", "")),
                "head": page.prose[:600],
            }
        )
    return out


class SliceSuggester:
    def __init__(self, *, attached_page_slugs: set[str], vault_dir: Path, store: SliceSuggestionStore, cheap_llm, model):
        self.attached_page_slugs = attached_page_slugs
        self.vault_dir = vault_dir
        self.store = store
        self.cheap_llm = cheap_llm
        self.model = model

    async def run(self) -> str:
        candidates = candidate_pages(self.vault_dir, self.attached_page_slugs)
        if not candidates:
            self.store.replace_suggestions([])
            return "No unpromoted topic pages to consider."
        context = json.dumps(candidates, ensure_ascii=False, indent=1)
        response = await self.cheap_llm.completion(
            messages=[
                {"role": "system", "content": SLICE_SUGGESTER_SYSTEM},
                {"role": "user", "content": context},
            ],
            model=self.model,
            response_format=SliceSuggestionSet,
        )
        content = response.choices[0].message.content
        parsed = content if isinstance(content, SliceSuggestionSet) else SliceSuggestionSet.model_validate_json(content)
        drafts = parsed.suggestions
        by_key = {c["key"]: c for c in candidates}
        kept = []
        now = datetime.now(UTC).isoformat()
        for draft in drafts:
            candidate = by_key.get(draft.key)
            if candidate is None:
                _logger.warning("Slice suggester proposed unknown page %r; dropped", draft.key)
                continue
            kept.append(
                {
                    "id": str(uuid.uuid4()),
                    "key": draft.key,
                    "title": candidate["title"],
                    "page_path": f"topics/{draft.key}.md",
                    "rationale": draft.rationale,
                    "created_at": now,
                }
            )
        self.store.replace_suggestions(kept)
        return f"Suggested {len(kept)} slice(s) from {len(candidates)} candidate pages."
