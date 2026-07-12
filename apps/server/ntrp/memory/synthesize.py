"""File-native synthesis: write clean cited prose into each page's COMPILED zone.

Walks the live FilePageStore pages, turns each page's active timeline records into
Records, routes to the EXISTING prompts (prompts_synthesis: PROFILE/DOSSIER/
ACTIVE_WORK — the same ones that produced the old prose), validates that every
(record:XXXXXXXX) cite resolves to a real record, and writes the prose into
page.prose then persists. Stale-gated: a pass only re-synthesizes pages whose
prose is empty or older than their newest record. Reuses prompts_synthesis verbatim;
no new prompts, no dependency on the legacy ArtifactMemoryStore.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ntrp.logging import get_logger
from ntrp.memory import prompts_synthesis as ps
from ntrp.memory.artifacts import ArtifactMemoryStore
from ntrp.memory.file_store import _slug, load_conventions
from ntrp.memory.journal import JournalConflictError
from ntrp.memory.merge import (
    MergeResult,
    synthesis_base_rel,
    synthesis_candidate_rel,
    three_way_merge,
)
from ntrp.memory.models import Record
from ntrp.memory.page_edit_service import PageEditService, StalePageRevisionError
from ntrp.memory.pages import parse_page, render_page
from ntrp.memory.project_names import resolve_project_title
from ntrp.observability import observed_trace

_logger = get_logger(__name__)

ACTIVE_WORK_RECENT_DAYS = 7
DAILY_RECENT_DAYS = 7  # regenerate daily logs for this trailing window; older days freeze
DAILY_MIN_RECORDS = 2  # a day with fewer meaningful records gets no page
PROFILE_RECORD_CAP = 80
REGRESSION_FLOOR = 0.60  # reject a re-synthesis that drops below 60% of prior size/cites (anti-collapse)
_SKIP_DIRS = {"changelog", "context", "facts", ".index", ".maintenance", "sources", "insights"}
_SKIP_NAMES = {"directives.md", "references.md", "lessons.md", "needs-triage.md", "inbox.md", "index.md", "README.md", "AGENTS.md", "health.md"}
_WIKILINK_RE = re.compile(r"\[\[([^\]#|]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")


def _flat(labels_by_id: dict[str, list], ids) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    wanted = {getattr(r, "id", r) for r in ids}
    for rid, entries in labels_by_id.items():
        if rid in wanted:
            out[rid] = [e["label"] if isinstance(e, dict) else e for e in entries]
    return out


def _strip_unknown_wikilinks(text: str, known_titles: list[str]) -> str:
    known = {t.strip().lower() for t in known_titles}

    def repl(m: re.Match) -> str:
        title = m.group(1).strip()
        display = (m.group(2) or title).strip()
        return m.group(0) if title.lower() in known else display

    return _WIKILINK_RE.sub(repl, text)


async def _complete(llm, model, system, user, effort) -> str | None:
    try:
        resp = await llm.completion(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=model,
            reasoning_effort=effort,
        )
    except Exception:
        _logger.warning("memory synthesis LLM call failed", exc_info=True)
        return None
    content = resp.choices[0].message.content if resp.choices else None
    return content.strip() if content and content.strip() else None


# The LLM contract stays id-based — `(record:XXXXXXXX)` cites are what the
# provenance check verifies. The STORED page translates each cite group into a
# dex-style readable source tag — `(from chat)`, `(from gmail + calendar)`,
# `(inferred)` — and the verified id list moves to the raw sidecar (prose_cites).
_SRC_CLASS = {"user": "chat", "chat_turn": "chat", "curator": "chat", "seed": "chat", "unknown": "chat"}


def _cite_tag(ids: list[str], src_by_id: dict[str, str]) -> str:
    classes: list[str] = []
    for rid in ids:
        src = src_by_id.get(rid.lower(), "chat")
        label = "inferred" if src == "dreamer" else f"from {_SRC_CLASS.get(src, src)}"
        if label not in classes:
            classes.append(label)
    # "(from chat + gmail)" — strip the repeated "from" on merged classes
    return "(" + " + ".join(c.removeprefix("from ") if i else c for i, c in enumerate(classes)) + ")"


def _humanize_cites(prose: str, src_by_id: dict[str, str]) -> str:
    def repl(m: re.Match) -> str:
        return _cite_tag(ps._CITE_ID_RE.findall(m.group(0)), src_by_id)

    return ps._CITATION_GROUP_RE.sub(repl, prose)


def _pre(system: str, conventions: str) -> str:
    """Prepend the shared operating manual as a static cacheable block ahead of the
    tuned task system prompt (additive — the task prompt is never altered)."""
    return f"<operating_manual>\n{conventions}\n</operating_manual>\n\n{system}" if conventions else system


def _provenance_ok(text: str, allowed: set[str]) -> bool:
    return ps.cited_ids(text).issubset({a[:8].lower() for a in allowed})


def _regression_ok(page, candidate: str) -> bool:
    """GEPA-style anti-collapse guard: reject a re-synthesis that drops token or
    citation count below REGRESSION_FLOOR of the prior (keep the prior prose).
    Prior tokens come from the stored prose; prior cites from the verified
    grounding list (the stored prose carries readable tags, not ids). The
    candidate is judged pre-translation, so its ids are still countable.
    First synthesis (no baseline) always passes."""
    prior_tokens = len(page.prose.split()) if page.prose else 0
    prior_cites = len(page.frontmatter.get("prose_cites") or [])
    if prior_tokens == 0 and prior_cites == 0:
        return True
    new_tokens = len(candidate.split())
    new_cites = len(ps.cited_ids(candidate))
    tokens_ok = prior_tokens == 0 or new_tokens >= prior_tokens * REGRESSION_FLOOR
    cites_ok = prior_cites == 0 or new_cites >= prior_cites * REGRESSION_FLOOR
    if not (tokens_ok and cites_ok):
        _logger.warning(
            "synthesis regression rejected — keeping prior prose",
            new_tokens=new_tokens, prior_tokens=prior_tokens, new_cites=new_cites, prior_cites=prior_cites,
        )
    return tokens_ok and cites_ok


def _page_kind(root: Path, path: Path) -> str | None:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    if rel.parts and rel.parts[0] in _SKIP_DIRS:
        return None
    if rel.name in _SKIP_NAMES:
        return None
    if rel.name == "me.md":
        return "profile"
    if rel.name == "active-work.md":
        return "active_work"
    if rel.parts and rel.parts[0] == "daily":
        return None  # granular DailyProjector owns daily pages
    if rel.parts and rel.parts[0] in ("topics", "entities", "projects"):
        return "dossier"
    return None


def _stale(page, canonical_revision: str) -> bool:
    if not page.prose:
        return True
    return page.frontmatter.get("generated_from_revision") != canonical_revision


def _stored_synthesis_base(store, path: Path) -> bytes | None:
    revision = store._pages[path].frontmatter.get("generated_from_revision")
    if not isinstance(revision, str):
        return None
    try:
        return ArtifactMemoryStore(store._root).read_synthesis_maintenance(
            synthesis_base_rel(path.relative_to(store._root).as_posix(), revision)
        )
    except FileNotFoundError:
        return None


async def _merge_generated_page(
    store,
    path: Path,
    generated: bytes,
    *,
    source_revision: str,
    prose_cites: tuple[str, ...],
) -> MergeResult:
    rel = path.relative_to(store._root).as_posix()
    current_on_disk = store._safe_read_bytes(path)
    current = render_page(store._pages[path]).encode() if current_on_disk is None else current_on_disk
    stored_base = _stored_synthesis_base(store, path)
    try:
        current_has_prose = bool(parse_page(current.decode("utf-8")).prose)
    except UnicodeDecodeError as exc:
        raise ValueError("memory pages must be UTF-8") from exc
    base = current if stored_base is None and not current_has_prose else stored_base
    result = three_way_merge(base, current, generated)
    resources = ArtifactMemoryStore(store._root)
    if result.review_required:
        resources.write_synthesis_maintenance(synthesis_candidate_rel(rel, source_revision), result.candidate)
        return result
    if store.canonical_revision != source_revision:
        resources.write_synthesis_maintenance(synthesis_candidate_rel(rel, source_revision), generated)
        return MergeResult(None, generated, True, "stale_source")
    assert result.merged is not None
    service = PageEditService(store._root, store, reconciler=None)
    if result.merged == current:
        if (
            store._pages[path].frontmatter.get("generated_from_revision") != source_revision
            or stored_base != generated
        ):
            try:
                await service.advance_synthesis_checkpoint(
                    path=rel,
                    current=current,
                    source_revision=source_revision,
                    generated_base=generated,
                    prose_cites=prose_cites,
                )
            except (JournalConflictError, StalePageRevisionError):
                resources.write_synthesis_maintenance(synthesis_candidate_rel(rel, source_revision), generated)
                return MergeResult(None, generated, True, "stale_source")
        return result
    try:
        await service.apply_synthesis_merge(
            path=rel,
            base=current_on_disk,
            result=result.merged,
            source_revision=source_revision,
            generated_base=generated,
            prose_cites=prose_cites,
        )
    except (JournalConflictError, StalePageRevisionError):
        resources.write_synthesis_maintenance(synthesis_candidate_rel(rel, source_revision), generated)
        return MergeResult(None, generated, True, "stale_source")
    return result


def _render_generated_page(store, path: Path, prose: str) -> bytes:
    current_on_disk = store._safe_read_bytes(path)
    current = render_page(store._pages[path]).encode() if current_on_disk is None else current_on_disk
    template = _stored_synthesis_base(store, path) or current
    try:
        template.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("memory pages must be UTF-8") from exc
    body = prose.rstrip().encode("utf-8") + b"\n"
    if not template.startswith(b"---\n"):
        return body
    frontmatter_end = template.find(b"\n---\n", 4)
    if frontmatter_end < 0:
        return body
    return template[: frontmatter_end + 5] + b"\n" + body


def _known_titles(store) -> list[str]:
    out: list[str] = []
    for path, page in store._pages.items():
        if _page_kind(store._root, path) == "dossier":
            title = page.frontmatter.get("title")
            if title:
                out.append(str(title))
    return out


def _rename_project_pages(store) -> None:
    """Retitle + rename a project page filed under its opaque scope-id to its human
    name, within the unified topics/ folder. A project page is any topics/ page with a
    scope_key. Idempotent (once renamed, slug == filename)."""
    names = getattr(store, "_project_names", {}) or {}
    if not names:
        return
    root = store._root
    for path in list(store._pages.keys()):
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if not rel.parts or rel.parts[0] != "topics":
            continue
        page = store._pages[path]
        key = page.frontmatter.get("scope_key")
        human = names.get(str(key)) if key else None
        if not human:
            continue
        page.frontmatter["title"] = human
        new_path = root / "topics" / f"{_slug(human)}.md"
        if new_path == path:
            store._persist(path)
            continue
        if new_path in store._pages or new_path.exists():
            store._persist(path)  # slug collision: keep filename, fix title only
            continue
        store._pages[new_path] = page
        del store._pages[path]
        for ln in page.lines:
            store._loc[ln.id] = new_path
        store._persist(new_path)
        store._remove_page_files(path)


# -- per-page synthesizers -----------------------------------------------------


async def _synth_profile(store, labels, llm, model, effort, conventions: str = "") -> str | None:
    rows = await store.list(limit=None, scopes=None)
    directives = [r for r in rows if r.kind == "directive"]
    facts = [
        r for r in rows
        if r.kind == "fact"
        and (r.scope_kind or "").lower() in ("user", "global", "")
        and not (r.source_ref and r.source_ref.kind == "dreamer")
    ]
    pinned = [r for r in rows if r.pinned and r.kind != "source"]
    seen: set[str] = set()
    sel: list[Record] = []
    for r in [*directives, *facts, *pinned]:
        if r.id not in seen:
            seen.add(r.id)
            sel.append(r)
    if not sel:
        return None
    sel.sort(key=lambda r: (r.pinned, r.last_confirmed_at), reverse=True)
    sel = sel[:PROFILE_RECORD_CAP]
    known = _known_titles(store)
    user = ps.profile_user_message(sel, _flat(labels, sel), known_subjects=known)
    out = await _complete(llm, model, _pre(ps.PROFILE_SYSTEM, conventions), user, effort)
    if not out or not _provenance_ok(out, {r.id for r in sel}):
        return None
    return _strip_unknown_wikilinks(out, known).rstrip()


async def _synth_dossier(store, path, labels, llm, model, effort, conventions: str = "") -> str | None:
    page = store._pages[path]
    rows = [store._to_record(ln, path) for ln in page.active_lines() if ln.src != "dreamer"]
    if page.frontmatter.get("scope_key"):  # a project page (now in topics/) — resolve human name
        title = resolve_project_title(page, getattr(store, "_project_names", {}) or {})
    else:
        title = str(page.frontmatter.get("title") or path.stem)
    ordered = sorted(rows, key=lambda r: r.last_confirmed_at, reverse=True)
    user = ps.dossier_user_message(title, ordered, _known_titles(store), _flat(labels, ordered))
    out = await _complete(llm, model, _pre(ps.DOSSIER_SYSTEM, conventions), user, effort)
    if not out or out.strip() == ps.INSUFFICIENT_DOSSIER:
        return None
    if not _provenance_ok(out, {r.id for r in ordered}):
        return None
    return out.rstrip()


async def _synth_active_work(store, labels, llm, model, effort, conventions: str = "") -> str | None:
    rows = await store.list(limit=None, scopes=None)
    cutoff = (datetime.now(UTC) - timedelta(days=ACTIVE_WORK_RECENT_DAYS)).isoformat()
    recent = [
        r for r in rows
        if r.kind != "source"
        and (r.last_confirmed_at or "") >= cutoff
        and not (r.source_ref and r.source_ref.kind == "dreamer")
    ]
    project = [r for r in rows if (r.scope_kind or "").lower() == "project"]
    if not recent and not project:
        return None
    user = ps.active_work_user_message(recent, project, _flat(labels, [*recent, *project]))
    out = await _complete(llm, model, _pre(ps.ACTIVE_WORK_SYSTEM, conventions), user, effort)
    if not out:
        return None
    if out.strip() == ps.NO_ACTIVE_WORK:
        return out  # sentinel written as-is (provenance bypassed)
    if not _provenance_ok(out, {r.id for r in [*recent, *project]}):
        return None
    return out.rstrip()


_DAILY_SKIP_KINDS = {"observation", "changelog"}  # observation = legacy lines pre-retirement


def _daily_records(store, day: str) -> list:
    """Meaningful records that entered memory on `day` (by append date), gathered
    across the whole store. Integration observations, housekeeping, and dream
    insights are excluded so the daily log reads as what the USER did — not inbox
    noise, and not a mirror of the insights page."""
    out: list = []
    for path, page in store._pages.items():
        if _page_kind(store._root, path) == "daily":
            continue
        for ln in page.active_lines():
            if ln.date == day and ln.kind not in _DAILY_SKIP_KINDS and ln.src != "dreamer":
                out.append(store._to_record(ln, path))
    return out


async def _synth_daily(store, path, labels, llm, model, effort, conventions: str = "") -> str | None:
    day = path.stem
    recs = _daily_records(store, day)
    if len(recs) < DAILY_MIN_RECORDS:
        return None
    recs.sort(key=lambda r: r.last_confirmed_at)
    user = ps.daily_user_message(day, recs, _flat(labels, recs))
    out = await _complete(llm, model, _pre(ps.DAILY_SYSTEM, conventions), user, effort)
    if not out or out.strip() == ps.NO_DAILY:
        return None
    if not _provenance_ok(out, {r.id for r in recs}):
        return None
    return out.rstrip()


# -- driver --------------------------------------------------------------------


@observed_trace("memory.synthesis", tags="memory")
async def run_synthesis(store, llm, model: str, *, reasoning_effort: str | None = None) -> str:
    if llm is None or not model:
        return "synthesis skipped: no memory model configured"
    conventions = load_conventions()  # shared operating manual, prepended to every pass (additive)
    _rename_project_pages(store)  # fix opaque names + titles BEFORE synth so cites/links use the human name
    # active-work.md is a cross-cutting thread with no timeline of its own — ensure
    # the page exists so the loop synthesizes it (the synthesizer pulls from across
    # the store, not from this page's lines).
    store._ensure_page(store._root / "active-work.md", title="Active work")
    source_revision = store.canonical_revision
    all_records = await store.list(limit=None, scopes=None)
    labels = await store.labels_for([r.id for r in all_records], include_kind=True)
    known_titles = _known_titles(store)  # links survive only to real topic pages (no dangling [[X]] in Obsidian)
    live_ids = {r.id.lower() for r in all_records}  # for dangling-grounding detection
    src_by_id = {r.id.lower(): (r.source_ref.kind if r.source_ref else "chat") for r in all_records}
    done: list[str] = []
    pending_pages = {path: deepcopy(page) for path, page in store._pages.items()}
    for path in pending_pages:
        if path not in store._pages:
            store._pages[path] = pending_pages[path]
        kind = _page_kind(store._root, path)
        if kind is None:
            continue
        page = store._pages[path]
        # active_work and in-window daily pages have no timeline of their own, so
        # _stale (which reads local lines) can't judge them — force re-synth so they
        # track the store's current state. Past-window daily pages fall through to
        # _stale and freeze once written.
        force = kind == "active_work"
        # Re-synthesize if a new record arrived, a grounding id went dangling (the
        # cited record was merged/superseded/pruned away), or the prose still carries
        # the legacy inline `(record:..)` dialect (one-time migration to source tags).
        grounding = {str(i).lower() for i in (page.frontmatter.get("prose_cites") or [])}
        legacy_cites = bool(ps.cited_ids(page.prose)) if page.prose else False
        dangling = bool(page.prose) and not grounding.issubset(live_ids)
        if not force and not dangling and not legacy_cites and not _stale(page, source_revision):
            continue
        if kind == "profile":
            prose = await _synth_profile(store, labels, llm, model, reasoning_effort, conventions)
        elif kind == "active_work":
            prose = await _synth_active_work(store, labels, llm, model, reasoning_effort, conventions)
        else:
            prose = await _synth_dossier(store, path, labels, llm, model, reasoning_effort, conventions)
        if prose is None:
            # Couldn't re-synthesize (e.g. a frozen daily log whose records consolidated
            # away). Keep the prose but keep its bookkeeping honest: translate any legacy
            # inline id-cites to readable source tags and drop dead ids from the grounding.
            if page.prose and (legacy_cites or dangling):
                generated = _render_generated_page(store, path, _humanize_cites(page.prose, src_by_id))
                merged = await _merge_generated_page(
                    store,
                    path,
                    generated,
                    source_revision=source_revision,
                    prose_cites=tuple(sorted(grounding & live_ids)),
                )
                if not merged.review_required:
                    done.append(path.stem)
            continue
        # Strip wikilinks to subjects that have no page (parked sub-threshold entities)
        # so the vault has no dangling [[X]] links in Obsidian — every pass, not just profile.
        prose = _strip_unknown_wikilinks(prose, known_titles)
        # Sentinels are deliberate short strings — exempt from the regression guard.
        # Legacy-cite pages are also exempt: their baseline is the old essay register,
        # and the briefing register is SUPPOSED to be tighter (one-time migration).
        if (
            prose not in (ps.NO_ACTIVE_WORK, ps.INSUFFICIENT_DOSSIER)
            and not legacy_cites
            and not _regression_ok(page, prose)
        ):
            continue
        prose_cites = tuple(sorted(ps.cited_ids(prose)))  # verified grounding, pre-translation
        generated = _render_generated_page(store, path, _humanize_cites(prose, src_by_id))
        merged = await _merge_generated_page(
            store,
            path,
            generated,
            source_revision=source_revision,
            prose_cites=prose_cites,
        )
        if not merged.review_required:
            done.append(path.stem)
    if done:  # prose changed -> index blurbs + health verdicts changed with it
        store._write_index()
        store._write_health()
    msg = f"synthesized {len(done)} pages ({', '.join(done) or 'none'})"
    _logger.info(msg)
    return msg


if __name__ == "__main__":
    import asyncio
    import tempfile

    from ntrp.memory.file_store import FilePageStore
    from ntrp.memory.models import SourceRef

    class _FakeLLM:
        def __init__(self) -> None:
            self.mode = "echo"
            self.calls = 0

        async def completion(self, *, messages, model, reasoning_effort=None):
            self.calls += 1
            user = messages[-1]["content"]
            if self.mode == "insufficient":
                content = ps.INSUFFICIENT_DOSSIER
            elif self.mode == "fabricate":
                content = "Bogus claim. (record:deadbeef)"
            else:
                m = re.search(r"\[([0-9a-f]{6,})\]", user)
                cid = m.group(1) if m else "00000000"
                content = f"Synthesized prose summary. (record:{cid})"
            msg = type("M", (), {"content": content})()
            return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()

    async def _demo():
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            store = FilePageStore(root)  # no name map yet -> simulates legacy opaque filing
            await store.open()
            # Two records promote "Bicycles" to its own page (one would just park on me.md).
            r = await store.add("Tim rides a gravel bike daily.", kind="fact", source_ref=SourceRef("user", ""))
            await store.set_labels(r.id, [], entity_labels=["Bicycles"])
            rb = await store.add("Tim's bike has 700c wheels.", kind="fact", source_ref=SourceRef("user", ""))
            await store.set_labels(rb.id, [], entity_labels=["Bicycles"])
            bike_path = root / "topics" / "bicycles.md"
            assert bike_path in store._pages, "two records should promote the entity to its own page"

            fake = _FakeLLM()
            await run_synthesis(store, fake, "m")
            page = store._pages[bike_path]
            ids = {ln.id for ln in page.active_lines()}
            assert page.prose and "(from chat)" in page.prose and "(record:" not in page.prose, page.prose
            assert set(page.frontmatter["prose_cites"]) & ids, page.frontmatter
            assert len(page.active_lines()) == 2, "timeline must survive synthesis"
            raw_bike = root / "raw" / "topics" / "bicycles.md"
            assert raw_bike.exists() and "^" in raw_bike.read_text(encoding="utf-8"), "timeline persists in raw/ sidecar"
            assert "<!-- timeline" not in bike_path.read_text(encoding="utf-8"), "page file is prose-only"
            # active-work.md is a cross-cutting thread (no own timeline) — created + synthesized from the store
            aw = root / "active-work.md"
            assert aw in store._pages and store._pages[aw].prose, "active-work.md synthesized from across the store"

            # stale gate: unchanged dossiers are skipped; active-work and today's
            # daily log always refresh (both are timeline-less, window-current pages).
            before = fake.calls
            await run_synthesis(store, fake, "m")
            assert fake.calls == before + 2, "only active-work + today's daily re-synthesize; stale dossiers are skipped"

            # provenance rejection: a new page whose synthesis cites a fake id is rejected
            r2 = await store.add("Cats are great.", kind="fact", source_ref=SourceRef("user", ""))
            await store.set_labels(r2.id, [], entity_labels=["Cats"])
            r2b = await store.add("Cats purr when content.", kind="fact", source_ref=SourceRef("user", ""))
            await store.set_labels(r2b.id, [], entity_labels=["Cats"])
            fake.mode = "fabricate"
            await run_synthesis(store, fake, "m")
            assert store._pages[root / "topics" / "cats.md"].prose == "", "fabricated cite must be rejected"

            fake.mode = "echo"

            # daily log: a dated, prose-only page aggregating the day's MEANINGFUL
            # records (facts), with integration observations filtered out.
            today = datetime.now(UTC).date().isoformat()
            day_recs = _daily_records(store, today)
            assert all(r.kind not in _DAILY_SKIP_KINDS for r in day_recs), "observations/changelog excluded from daily"
            assert any(r.kind == "fact" for r in day_recs), "facts feed the daily log"
            daily_path = root / "daily" / f"{today}.md"
            assert daily_path in store._pages, "daily page created for today"
            dp = store._pages[daily_path]
            assert dp.prose and "(from" in dp.prose and "(record:" not in dp.prose, "daily page synthesized with readable source tags"
            assert not dp.active_lines(), "daily page is a prose-only projection (no own records)"

            # project rename: opaque-id page -> human name (add files under slug of scope_key)
            await store.add("ntrp is the OS.", kind="fact", scope_kind="project", scope_key="proj_x", source_ref=SourceRef("user", ""))
            opaque = root / "topics" / "proj-x.md"  # _slug("proj_x") == "proj-x"
            assert opaque.exists(), f"project page filed under slug of scope_key; got {list((root/'topics').iterdir())}"
            store._project_names = {"proj_x": "ntrp"}  # name map now available (e.g. from sessions.db)
            _rename_project_pages(store)
            assert (root / "topics" / "ntrp.md").exists(), "project renamed to human name"
            assert not opaque.exists(), "opaque project page removed"
            _rename_project_pages(store)  # idempotent
            assert (root / "topics" / "ntrp.md").exists()
            print("synthesize.py self-check OK")

    asyncio.run(_demo())
