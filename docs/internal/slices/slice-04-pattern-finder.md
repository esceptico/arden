# Slice 4 — Pattern finder pass 1 (`episode → observation`)

**Status:** Draft for PM A/B gate, then codex fire.
**Prereqs:** slice 3 shipped (`64f0ef24`). 817 tests passing + 3 xfailed. Ruff clean.
**Backlog absorbed from `slice-07-backlog.md`:** §4 (episode_buffers token anomaly), §5 (PM grep-gate lesson).
**Out of scope:** pass 2 (observation→claim — slice 5), contradiction watcher (slice 6), skill inducer (slice 7), UI.

---

## 0. TL;DR

Cluster recently-closed `kind=episode` rows by embedding similarity + tag overlap + temporal proximity. For each cluster ≥ 2 episodes, LLM-summarize into one `kind=observation` row with `role=evidence` parent edges to source episodes. Manual `/admin/pattern-finder/run` endpoint + a Trigger.dev-style daily scheduler stub. Recompute over a rolling 7-day window each run, idempotent.

---

## 1. Goal — concrete

After slice 4 ships, this works end-to-end:

1. Chat connector (slice 2) closes episodes into `memory_items` with `kind=episode`. Already shipping.
2. **NEW:** A `PatternFinder.run_pass1()` method clusters those episodes, summarizes each cluster, writes `kind=observation` rows linked via `memory_item_parents(role='evidence')`.
3. **NEW:** `POST /admin/memory/pattern-finder/run` triggers a run synchronously, returns clusters discovered + observations written.
4. **NEW:** A Trigger.dev scheduled task `pattern-finder-daily` fires `PatternFinder.run_pass1()` daily at 04:00 UTC. (Schedule registration only; the body just calls the same method.)
5. `MemoryRetrieval.search()` (slice 3) now surfaces observations alongside episodes for the same query — no retrieval-layer changes needed; observations are just `memory_items` rows with a different `kind`.

**Verification:** seed a fresh DB with 6 episodes (3 about topic A, 3 about topic B), run pass 1, assert 2 observations exist with correct evidence edges.

---

## 2. Hard scope boundaries

**MUST do:**
- New module `apps/server/arden/memory/pattern_finder.py` with `PatternFinder` class
- New repository methods on `MemoryItemsRepository`:
  - `list_recent_items(*, kind, window_days, limit, scope)` — pull candidates
  - `insert_parent_edge(child_id, parent_id, role, order=None)` — write evidence edges
  - `list_parent_edges(child_id)` — read helper for tests
- New admin endpoint `POST /admin/memory/pattern-finder/run` (returns JSON: clusters found, observations written, elapsed_ms)
- New Trigger.dev task `pattern-finder-daily` in `apps/server/arden/triggers/` (or equivalent — see §6)
- New tests `apps/server/tests/memory/test_pattern_finder.py` (≥ 12 tests)
- Wire `PatternFinder` into the DI graph (see §7)

**MUST NOT do:**
- Touch `apps/server/arden/memory/retrieval.py` (slice 3 surface, frozen)
- Touch `apps/server/arden/memory/activation.py` (slice 3)
- Touch `apps/server/arden/memory/connectors/` (slice 2 surface — episode close is upstream of us, we consume it)
- Implement pass 2 (`observation → claim`) — slice 5
- Implement contradiction detection — slice 6
- Restore the 11 untracked `arden/knowledge/*.py` modules — explicit per slice-07-backlog §3B
- Add new columns to `memory_items` schema — kind=observation reuses the existing row shape

**Allowed to read but not edit:**
- `memory/items_store.py` is editable (we're adding repo methods)
- `memory/connectors/episode_close.py` (read-only — pattern for LLM client shape)

---

## 3. Algorithm — clustering

**Pick: single-link agglomerative clustering with cosine ≥ 0.7 threshold + tag-overlap boost + temporal-proximity boost.**

Inputs: list of `memory_item` rows with `kind=episode`, each with an embedding vector and a `tags` list and a `valid_from` timestamp.

```python
def cluster_episodes(episodes: list[MemoryItem]) -> list[list[MemoryItem]]:
    """
    Single-link agglomerative clustering.
    Two episodes are 'linked' if combined_similarity(a, b) >= 0.7.
    Returns clusters of size >= 2 (singletons are dropped — no observation worth emitting).
    """
    n = len(episodes)
    parent = list(range(n))  # union-find
    for i in range(n):
        for j in range(i + 1, n):
            if combined_similarity(episodes[i], episodes[j]) >= 0.7:
                union(parent, i, j)
    clusters: dict[int, list[MemoryItem]] = {}
    for i in range(n):
        clusters.setdefault(find(parent, i), []).append(episodes[i])
    return [c for c in clusters.values() if len(c) >= 2]


def combined_similarity(a: MemoryItem, b: MemoryItem) -> float:
    """
    Weighted: 0.70 cosine + 0.20 tag_jaccard + 0.10 temporal_proximity.
    All terms clamped to [0, 1].
    """
    cos = cosine(a.embedding, b.embedding)                       # [0, 1] after clamp
    tag = jaccard(set(a.tags), set(b.tags))                      # [0, 1]
    days_apart = abs((a.valid_from - b.valid_from).total_seconds()) / 86400
    temporal = max(0.0, 1.0 - days_apart / 7.0)                  # 1.0 same day, 0.0 ≥ 7 days apart
    return 0.70 * cos + 0.20 * tag + 0.10 * temporal
```

**Why this:**
- **Single-link agglomerative** = each cluster is a connected component in the similarity graph; transitively links chained episodes. Cheap, deterministic, debuggable. O(n²) similarity calls, fine for n ≤ ~500 (our 7-day window).
- **Cosine 0.7 threshold** = empirically reasonable for OpenAI embedding-3-small (1536d). Tighter than 0.5 (which over-clusters) and looser than 0.9 (which under-clusters). Configurable via `PATTERN_FINDER_SIM_THRESHOLD` env var.
- **Weighted 70/20/10** = embedding does most of the work; tags break ties when embeddings drift; temporal breaks ties when content repeats months apart (different conversation about same topic).
- **Drop singletons** = no point summarizing 1 episode into 1 observation — that's just duplication. Re-running the finder may grow a singleton into a cluster as new episodes arrive.

**Alternatives rejected:**
- **HDBSCAN** — overkill for n ≤ 500; introduces a clustering dependency; harder to reason about parameter tuning.
- **LLM-driven clustering** ("give it 10 episodes, ask which cluster") — non-deterministic, expensive at scale, hard to test.
- **K-means** — needs preset k; episodes don't have a natural k.

---

## 4. Algorithm — LLM summarization

For each cluster (≥ 2 episodes), one LLM call:

```python
async def summarize_cluster(
    episodes: list[MemoryItem],
    client: SummaryClient,
) -> ObservationDraft:
    prompt = render_pass1_prompt(episodes)  # see prompts/pass1.txt below
    body = await client(prompt)
    return ObservationDraft(
        content=body.strip(),
        tags=sorted(set().union(*(set(e.tags) for e in episodes))),
        source_refs=_merge_source_refs(episodes),
        evidence_episode_ids=[e.id for e in episodes],
    )
```

**Prompt** (`apps/server/arden/memory/prompts/pass1.txt`):

```
You are summarizing a cluster of related conversation episodes into a single observation.

An observation is a witnessed pattern that recurs across these episodes — something the user did, said, preferred, or struggled with that shows up more than once. It is still context-bound (you may refer to "across these conversations" or "in recent sessions") but it is NOT yet a decontextualized claim about the user.

Episodes in this cluster:
{episode_bullets}

Write ONE observation (3-5 sentences, no preamble) that captures the recurring pattern. Do not list every episode. Do not invent facts not present in the input. If no real pattern emerges, write exactly: NO_PATTERN.
```

**Reject `NO_PATTERN`:** if the LLM returns `NO_PATTERN` (or any string < 20 chars, or starts with "I cannot"), skip the cluster — no observation written.

**LLM client shape:** reuse the `SummaryClient` Protocol from `memory/connectors/episode_close.py` (line 23). Tests pass a fake that returns canned strings keyed by episode ids.

**Embedding the observation:** after summarization, embed the observation body with the same `EmbeddingClient` used by the chat connector. Store on the row via `MemoryItemInsert.embedding`. This lets retrieval surface the observation later.

---

## 5. Persistence

For each cluster that produced a non-rejected observation:

```python
async def persist_observation(
    repo: MemoryItemsRepository,
    draft: ObservationDraft,
    embedding: np.ndarray,
) -> str:
    observation_id = await repo.insert_item(
        MemoryItemInsert(
            kind="observation",
            content=draft.content,
            provenance="inferred",
            source_refs=draft.source_refs,
            confidence=0.6,             # see below
            tags=draft.tags,
            embedding=embedding,
        )
    )
    for episode_id in draft.evidence_episode_ids:
        await repo.insert_parent_edge(
            child_id=observation_id,
            parent_id=episode_id,
            role="evidence",
        )
    return observation_id
```

**Confidence = 0.6.** Per spec §3.7, observations are middle-abstraction; not as solid as a deduped claim, not as ephemeral as a single episode. 0.6 is a deliberate fixed value for slice 4; slice 5 derives confidence properly. Document as a TODO in code.

**Idempotency.** Pattern finder runs daily over a 7-day window. Re-running on the same window must not create duplicate observations.

**Mechanism:** at the start of each `run_pass1()`, find all existing `kind=observation` rows whose `valid_from >= window_start`. For each, read its `role=evidence` parent edges. Build a set of "already-clustered episode-id sets" (as frozensets). When emitting a new cluster, if its episode-id frozenset is already in that set, **skip** (cluster is unchanged). If the new cluster is a strict **superset** (new episodes joined the same cluster), **supersede**: emit a new observation, link it via `role=supersedes` to the old one, mark the old one `status=superseded`, `invalid_at=now`.

Supersession on pattern-finder reruns is honest: clusters grow as new episodes arrive, and the older summary becomes stale. The bi-temporal trail is preserved.

---

## 6. Scheduler — Trigger.dev (or equivalent)

**Honest check first:** does the Arden server already use Trigger.dev? Or apscheduler? Or a custom asyncio scheduler?

```bash
grep -rn 'trigger\|Trigger\|scheduler\|crontab\|APScheduler' apps/server/arden/ --include='*.py' | head
```

If Trigger.dev is in use → register the daily task there.
If apscheduler is in use → use that.
If neither → punt to **manual endpoint only** for slice 4, file a stub in slice-07-backlog for "wire pattern finder into actual scheduler."

**Codex must determine which** as step 1 of slice 4 and document the answer in its working notes. Do not invent a scheduler.

If a real scheduler exists, the task is a 1-call wrapper:

```python
@trigger.scheduled(cron="0 4 * * *")  # 04:00 UTC daily
async def pattern_finder_daily():
    pattern_finder = container.resolve(PatternFinder)
    result = await pattern_finder.run_pass1(window_days=7)
    logger.info("pattern_finder.daily_run", **result.to_dict())
```

The manual endpoint is required regardless:

```python
@router.post("/admin/memory/pattern-finder/run")
async def run_pattern_finder(
    body: PatternFinderRunRequest,
    pattern_finder: PatternFinder = Depends(get_pattern_finder),
) -> PatternFinderRunResponse:
    return await pattern_finder.run_pass1(
        window_days=body.window_days or 7,
        scope=body.scope or "user",
    )
```

---

## 7. DI wiring

`PatternFinder` depends on:
- `MemoryItemsRepository` (read episodes, write observations)
- `SummaryClient` (LLM call for cluster summarization)
- `EmbeddingClient` (embed observation body before insert)

Wire into existing FastAPI DI graph (`apps/server/arden/server/deps.py` or wherever `MemoryRetrieval` was wired in slice 3 — codex finds the symmetric spot).

---

## 8. Address slice-07-backlog §4 (token anomaly)

The slice 2 anomaly: `episode_buffers.tokens=546940` for one episode. Slice 4 inherits this because pattern finder consumes episodes.

**Required investigation (~30min) before clustering work starts:**

1. Run smoke: open a fresh chat session, send 5 turns, close it (or wait for episode close).
2. `sqlite3 ~/.arden/memory.db 'SELECT id, tokens, turn_count FROM episode_buffers ORDER BY id DESC LIMIT 5'`
3. **Expected:** `tokens` in low thousands per episode; `turn_count` ≤ 20 for a short chat.
4. **If wrong:** trace the token-accumulation path in `memory/connectors/chat.py` and `memory/store/episode_buffers.py`. Find the bug. Either fix it in slice 4 (if ≤ 30min) or file a precise repro in slice-07-backlog §4 and proceed without fixing.

The clustering algorithm doesn't depend on `tokens`, so a wrong value won't break slice 4. But it might break slice 4's smoke test (if all episodes appear under one massive buffer, pattern finder has nothing to cluster).

Document the finding either way.

---

## 9. Tests — ≥ 12 cases

File: `apps/server/tests/memory/test_pattern_finder.py`.

Pattern follows slice 3's `test_retrieval.py`: a `_FakeSummaryClient` + canned embeddings via `tests.conftest.mock_embedding`. Fresh sqlite per test via existing fixtures.

**Required cases:**

1. `test_pattern_finder_emits_observation_for_two_similar_episodes` — 2 episodes with cosine 0.85 cluster, 1 observation written with 2 evidence edges.
2. `test_pattern_finder_drops_singleton_clusters` — 3 episodes, 1 isolated, 2 similar; only the pair produces an observation.
3. `test_pattern_finder_clusters_three_chained_episodes_via_single_link` — A↔B (0.8), B↔C (0.8), A↔C (0.5). Single-link unions all three; 1 observation with 3 evidence edges.
4. `test_pattern_finder_respects_window_days` — 4 episodes, 2 inside window, 2 outside (`valid_from` 10 days ago). Only the inside 2 are considered.
5. `test_pattern_finder_skips_observation_when_summary_returns_no_pattern` — fake client returns `NO_PATTERN`; cluster discarded, no observation written.
6. `test_pattern_finder_is_idempotent_on_unchanged_clusters` — run twice on the same data; second run writes nothing.
7. `test_pattern_finder_supersedes_observation_when_cluster_grows` — run 1: 2 episodes → obs_A. Run 2: same 2 + 1 new similar episode. Result: obs_A superseded, obs_B active with 3 evidence edges, `role=supersedes` edge from obs_B → obs_A.
8. `test_pattern_finder_uses_tag_jaccard_to_break_low_cosine_ties` — 2 episodes with cosine 0.62 + identical tags; combined score ≥ 0.70 → cluster.
9. `test_pattern_finder_uses_temporal_proximity_for_low_similarity_pairs` — 2 episodes with cosine 0.62 + same day; combined score ≥ 0.70 → cluster.
10. `test_pattern_finder_merges_source_refs_across_cluster` — observation row's `source_refs` is the deduped union of all evidence episodes' `source_refs`.
11. `test_pattern_finder_aggregates_tags_across_cluster` — observation `tags` = sorted set-union of episode tags.
12. `test_pattern_finder_writes_role_evidence_parent_edges` — assert `memory_item_parents` rows for each evidence edge, `role='evidence'`.

**Optional (nice-to-have):**

13. `test_pattern_finder_admin_endpoint_returns_summary` — `POST /admin/memory/pattern-finder/run`, assert JSON shape.
14. `test_pattern_finder_run_handles_empty_window` — no episodes, returns `{clusters: 0, observations: 0}` cleanly.

---

## 10. Run-result shape

```python
@dataclass
class PatternFinderRunResult:
    window_days: int
    scope: str
    episodes_considered: int
    clusters_found: int
    observations_written: int
    observations_superseded: int
    elapsed_ms: int

    def to_dict(self) -> dict: ...
```

Return from both the method and the admin endpoint.

---

## 11. Hard gates — codex MUST run ALL of these and paste output

(This list bakes in the slice-07-backlog §5 PM lesson — grep + collect + ruff are non-negotiable.)

```
cd apps/server

# 1. collection — every test file must import cleanly
.venv/bin/pytest tests/ --co -q 2>&1 | tail -5

# 2. memory tests — including slice 4 new file
.venv/bin/pytest tests/memory/ -q 2>&1 | tail -5

# 3. full suite — no new failures or xfails vs slice 3 baseline
.venv/bin/pytest tests/ -q 2>&1 | tail -10

# 4. no new dead imports
grep -rn 'from arden.memory.pattern_finder\b' apps/server/ --include='*.py'
grep -rn 'pattern_finder\|PatternFinder' apps/server/arden/ --include='*.py' | head -20

# 5. ruff
.venv/bin/ruff check arden/ tests/ 2>&1 | tail -5

# 6. admin endpoint smoke (if FastAPI app boots in tests)
.venv/bin/pytest tests/test_pattern_finder_routes.py -q 2>&1 | tail -5  # optional file
```

**Expected:**
- Gate 1: ≥ 832 collected (820 baseline + ≥ 12 new), 0 errors
- Gate 2: tests/memory/ all green (42 baseline + ≥ 12 new = ≥ 54)
- Gate 3: 0 failed, ≥ 829 passed (817 baseline + ≥ 12 new), 3 xfailed (unchanged)
- Gate 4: pattern_finder grep returns hits only in expected files (module, deps wiring, endpoint, tests)
- Gate 5: "All checks passed!"
- Gate 6: passes if endpoint test file created, otherwise N/A

---

## 12. PM checklist for codex's report

Codex must answer all of these in its final report:

1. **Scheduler decision:** Trigger.dev / apscheduler / custom asyncio / none-found-punt? Cite the file(s) that proved the answer.
2. **Token anomaly investigation (§8):** what was the value on a fresh smoke? Was the bug fixed, deferred, or not reproducible? Cite SQL output.
3. **DI wiring location:** where was `PatternFinder` wired? Cite file:line.
4. **Confidence value:** confirm 0.6 fixed; TODO comment in code links to slice 5.
5. **Supersession trail:** when a cluster grew on rerun, what SQL state does the test assert? Paste the test snippet.
6. **`MemoryItemsRepository` API additions:** list new methods + signatures.
7. **Files modified, added, deleted:** flat list.

---

## 13. Codex prompt (verbatim — extracted by invoke.sh §11)

```
You are implementing slice 4 of the Arden memory redesign: pattern finder pass 1
(episode → observation). The full brief is at
`docs/internal/slices/slice-04-pattern-finder.md`. Read it end-to-end before
writing any code. Authoritative spec: `docs/internal/arden-memory-redesign-spec.md`.

HARD CONSTRAINTS — violating any fails the pass:
- DO NOT touch apps/server/arden/memory/retrieval.py
- DO NOT touch apps/server/arden/memory/activation.py
- DO NOT touch apps/server/arden/memory/connectors/* (read-only reference)
- DO NOT implement pass 2 (observation→claim) — slice 5
- DO NOT restore any of the 11 untracked arden/knowledge/*.py modules

REQUIRED OUTPUTS:
1. apps/server/arden/memory/pattern_finder.py — PatternFinder + helpers
2. apps/server/arden/memory/prompts/pass1.txt — LLM prompt template
3. Repo method additions in apps/server/arden/memory/items_store.py:
   list_recent_items, insert_parent_edge, list_parent_edges
4. Admin endpoint POST /admin/memory/pattern-finder/run
5. Daily scheduler hookup IF a real scheduler exists in the repo (else punt
   to slice-07-backlog and document the punt)
6. apps/server/tests/memory/test_pattern_finder.py — ≥ 12 tests per brief §9
7. DI wiring symmetric to slice 3's MemoryRetrieval wiring

ALGORITHM (brief §3–4):
- Single-link agglomerative over recently-closed kind=episode rows
- Combined similarity: 0.70 cosine + 0.20 tag_jaccard + 0.10 temporal_proximity
- Cluster threshold: 0.70 (env: PATTERN_FINDER_SIM_THRESHOLD)
- Drop singleton clusters
- LLM-summarize each cluster via the SummaryClient protocol from
  memory/connectors/episode_close.py
- Reject summaries that return NO_PATTERN, < 20 chars, or start with "I cannot"
- Embed the observation body before insert
- Persist as kind=observation, confidence=0.6, role=evidence edges to source episodes
- Idempotent: if same episode-id frozenset already has an active observation,
  skip; if cluster has grown, write new observation and supersede old via
  role=supersedes edge + status=superseded + invalid_at=now

PRE-WORK (brief §6, §8):
A. Determine which scheduler the repo uses (Trigger.dev/apscheduler/none).
   Document the finding and act accordingly.
B. Smoke-test the episode_buffers token anomaly from slice-07-backlog §4.
   Either fix it (if ≤ 30min) or document the precise repro and proceed.

GATE LIST (brief §11) — paste full output of all 5 commands in your final
report. Match expected values. Stop and report any red gate; do not push past.

PM REPORT (brief §12) — answer all 7 questions in your final report.

When all 5 gates are green and all 7 PM questions answered, report DONE
with file list and gate output.
```

---

## 14. Sequence of work — codex's plan

1. Pre-work: scheduler discovery + token-anomaly smoke (§6, §8) — 15min
2. Repo method additions to `items_store.py` — 30min
3. `pattern_finder.py` module: clustering + summarization + persistence — 90min
4. Prompt template + LLM client wiring — 15min
5. Admin endpoint + scheduler hookup (or punt) — 30min
6. DI wiring — 15min
7. Tests `test_pattern_finder.py` (12+) — 90min
8. Run gates, fix red — 30min
9. Final report — 10min

Estimated total: ~5h codex wall time at xhigh reasoning.

---

## 15. Out of scope explicitly

- Pass 2: observation → claim (slice 5)
- Contradiction detection (slice 6)
- Skill induction (slice 7)
- Restoring deleted `test_knowledge_next_level.py` / `test_knowledge_write_gate.py` tests (slice-07-backlog §2A, §2B)
- Fixing dead `_repo.search_*` wrappers in `memory/service.py` (slice-07-backlog §3A)
- The 11 pre-existing untracked `arden/knowledge/*.py` modules — leave dirty (slice-07-backlog §3B)
- Restoring `semantic_alias_match` reason labels (slice 5)
- New schema columns on `memory_items`
- UI surfacing of observations (slice 8/9)

---

## 16. Risk register

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Embedding-3-small cosine 0.7 threshold over/under-clusters | Med | Configurable env var; tests use canned embeddings so they don't depend on real OAI |
| Single-link "chaining" produces giant clusters when threshold is too low | Med | Drop with the 0.7 floor; if observed in smoke, tighten to 0.75 in a follow-up |
| LLM returns junk summaries despite NO_PATTERN reject | Low | Confidence is 0.6, so junk observations are downweighted in retrieval; slice 5 dedupes |
| Token anomaly (§8) blocks smoke testing | Med | Document and proceed — clustering doesn't depend on `tokens` column |
| No scheduler in repo → daily task can't auto-run | Med | Manual endpoint suffices for slice 4; scheduler hookup deferred |
| Idempotency-via-frozenset breaks if episode ids change scope mid-window | Low | Window query filters by scope; cross-scope reruns are separate logical runs |
| `memory_item_parents` writes lack a transaction wrapper, leaving orphan observations | Med | Codex must wrap observation insert + parent-edge inserts in a single transaction |
