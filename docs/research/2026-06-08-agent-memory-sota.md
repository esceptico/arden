# Agent Memory — SOTA Research & Synthesis (2026-06-08)

Two independent research streams: (1) public SOTA web survey (frameworks, graphs, sleep-time, benchmarks); (2) mining of the Dex/ThirdLayer Slack for the team's own memory journey. They converge.

---

## TL;DR — the convergence

The public state-of-the-art **and** Dex's own painful 8-month path land in the same place:

- **Substrate = files/docs + embeddings + metadata.** Not knowledge graphs, not elaborate tier taxonomies. (Dex built the 6-tier `core/episodic/resource/procedural/semantic/integration` model, called it "soup," and is deprecating it toward "basic file system + embeddings + metadata.")
- **Retrieval = hybrid (BM25 + vector → RRF).** Consensus default everywhere.
- **Consolidation = a BACKGROUND / sleep-time job**, idle/cron/step-triggered with a `last_processed` cursor — *not* per-turn, *not* a heartbeat. (Dex: "user behavior IS the heartbeat.")
- **Add exactly ONE structured thing: timestamped, supersedable facts with recency-aware ranking.** This is the only place structure *repeatably* beats simpler memory (temporal / knowledge-update reasoning). Take the *concept* (validity + supersession + reconfirmation), skip the graph DB.
- **Entities/people: lightweight + aliases-first**, as files/sections — a real need ("Tim" vs "Timur" *will* happen), but not a graph.
- **Episodes as a base-unit framing** (source-native boundaries: 1 email/meeting/PR = 1 episode; chat is the hard case). Keep raw transcript retrievable — don't distill-and-discard.
- **An explicit `remember` tool** for ground-truth.

Reference design both camps point at: **Claude Code's 4-part memory** — `MEMORY.md` index + non-blocking per-turn prefetch + end-of-turn extract (background) + agent-initiated `memory_search`.

---

## 1. The landscape — one spectrum

How much structure you impose, and how much LLM work the write path pays:

```
SIMPLEST ───────────────────────────────────────────────► HEAVIEST
raw buffer/        editable text      extract atomic     extract entities+    temporal
rolling summary →  blocks/files    →  facts → vector  →  vector (hybrid)   →  knowledge graph
LangChain          Letta blocks       Mem0 (base)        Mem0 v3 entity-link  Zep/Graphiti
SummaryBuffer      Claude memory      LangMem            A-MEM                Cognee
ChatGPT/Gemini     CLAUDE.md          ChatGPT saved mem  MemoryOS             Mem0g (paper)
```

- **Simplest (file-blocks)** is a *first-class* SOTA approach, not a fallback: Letta `human`/`persona` blocks the agent self-edits; Claude's `/memories` files; ChatGPT's reference-chat-history dossier; Gemini's single `user_context` doc. Validated for the small-scale regime (see §5).
- **Mid (extract facts + vector)** is the modal 2026 design: Mem0 distills atomic facts and reconciles with ADD/UPDATE/DELETE/NOOP.
- **Heaviest (temporal KG)**: Zep/Graphiti — facts as edges with four timestamps, invalidated (not deleted) on contradiction. **The field is retreating from here** — Mem0 OSS v2→v3 *removed* its external graph store for in-vector entity linking.

arden today (curated docs per scope + per-turn curator + hybrid transcript search) = **file-blocks + the mid-tier's best part (hybrid retrieval), minus a fact store.** A deliberately good place to sit.

## 2. How SOTA memory actually works — the shared 5-stage pipeline

Three data models, but nearly everyone runs the same pipeline; the differences are *which stage is heavy* and *foreground vs background*.

1. **Capture** — store ground truth (Zep keeps raw messages as episodic nodes; Generative Agents append a timestamped memory stream).
2. **Extract / decide** — what to store & how it changes existing memory. Mem0's two-phase **extract → ADD/UPDATE/DELETE/NOOP** is the reference conflict-resolution op set; Letta/Claude/Gemini let the *model itself* decide (self-editing).
3. **Store** — doc/block | vector | graph.
4. **Retrieve** — **hybrid is consensus** (cosine + BM25 [+ n-hop]); rerank by RRF/MMR/cross-encoder; Generative Agents score `importance·recency·relevance`. LongMemEval: time-aware query expansion (+6.8–11.3% temporal) and fact-augmented keys (+9.4% recall) matter.
5. **Consolidate / reflect (background)** — merge, dedup, summarize, resolve contradictions, decay — off the hot path.

One line: **cheap inline write → hybrid retrieve → heavy reorganize later.** The 2025–26 shift pushes stages 5 (and increasingly 2) into a background worker.

## 3. Knowledge graphs — buy vs cost

**Buy:** temporal/contradiction/knowledge-update reasoning (Zep +18.5% on LongMemEval at ~1.6k vs 115k tokens, ~90% lower latency); multi-hop relational QA over a corpus.

**Cost:** multiple LLM calls per episode; **eventual consistency** (Zep retrieval "often failed" immediately, improved "after several hours"); 10–40× indexing; graph-DB ops + Cypher/SPARQL skills tax; fragmentation/synonym failure modes.

**Killer evidence (from inside the graph camp):** Mem0's own paper — graph variant adds only **~2% overall and *regresses* multi-hop** (47.19 vs 51.15): *"the addition of graph memory does not provide performance gains here… inefficiencies or redundancies in structured graph representations… compared to dense natural language memory alone."* A neutral Oct-2025 study found structured A-Mem **inferior to simpler RAG for most models.**

**Verdict:** graphs earn their keep on entity-dense, multi-hop, audit/provenance workloads with batch ingestion. For a single user: **skip the graph implementation; keep the temporal *concept* (validity + supersession).**

## 4. Sleep-time / background compute (hard requirement)

License: **Sleep-time Compute** paper (Lin et al., arxiv 2504.13171) — precompute `c' = f(c)` during idle: ~5× less test-time compute at iso-accuracy, +13–18% accuracy, ~2.5× lower cost/query *when the query is predictable from context* (memory consolidation is).

| Pattern | Mechanism | Cadence / trigger |
|---|---|---|
| **Reflection** | LLM poses ~3 salient questions over recent memories → writes ~5 higher-level insights back with citations | importance-accumulator > 150 |
| **Sleeptime agent** (Letta) | primary agent has **no memory-edit tools**; background agent writes clean "learned context" to shared blocks | step-counted; `sleeptime_agent_frequency` default 5; `last_processed_message_id` cursor |
| **Async "dreaming"** (ChatGPT/Mem0) | cheap inline write + background curates a durable profile w/ confidence tags | **idle / between sessions** (user never waits) |
| **Forgetting/decay** | time-decay / access-count / importance-thresholded prune | same background cadence; the *missing piece* in most modules |

**For one user:** a **cron/idle-triggered reflective pass**, not a second *live* agent — same benefit, far less machinery. (Arden's `feat/memory-rebuild` sweep is exactly this shape.) Dex runs a "dreamer" cron over new data; throttled cadence; explicit anti-heartbeat stance.

## 5. What the evidence actually says

- **LoCoMo is partly discredited** — independent audit: 6.4% of the answer key is wrong; the LLM judge accepted 62.81% of *intentionally-wrong* answers. Treat gaps < ~5–10 pts as noise. The Mem0-vs-Zep vendor war is unresolved; 90%+ scores signal LoCoMo *saturating*, not memory solved. **LongMemEval is the credible benchmark** (even strong systems drop 30–60% vs oracle retrieval).
- **Below ~150 conversations, full-context / simple beats RAG** (Convomem: full-context 70–82% vs Mem0 30–45% on hard multi-message cases).
- **Don't over-compress** — aggressive summary/fact extraction causes information loss that hurts detailed recall. Keep raw transcript retrievable alongside any distillate.
- **Complexity should scale to model capability**, not assume universal superiority.

## 6. Dex's internal journey (independent corroboration)

- Built the 6-tier model (~Nov 2025) → "too complex / soup" → **v1 reset (Kevin, May 20): "basic file system + embeddings + metadata… grep >> semantic for dups."** PRD-263 "file-based memory" active; taxonomy (legacy 6-tier vs chat-native `episode/fact/lesson/preference`) still unresolved early June.
- **Dropped graphs:** "i like that mem0 decided to drop any graph memory… neo4j is fun until you need to use it in production."
- **Freshness = reconfirmation, not TTL** (Viktor): *"a fact doesn't expire because a timer ran out — it goes stale because it stopped showing up… weight by `last_confirmed_at`… a `pinned: true` flag handles the birthday edge case. no taxonomy needed."*
- **Episodes = base unit, source-native boundaries**, "sloppy episodes + good dedup > perfect boundaries."
- **Aliases-first entity resolution** is the load-bearing hard problem; context-pollution-at-retrieval is the other ("the win is not injecting more context — it is injecting the right tiny slice").
- **Background "dreamer" cron**; daily append-only logs; `me.md` grounding (write the user's profile from `auth.users`, not the most-mentioned Slack person); explicit `remember` tool; reference = Claude Code 4-part memory.

## 7. Implications for arden (minimal design)

**Keep (already SOTA-right):**
- Curated markdown docs per scope (file-blocks) — proven for the sub-150-conversation single-user regime. Borrow Claude's anti-clutter + size-limit disciplines.
- Hybrid transcript search (vector + BM25 → RRF). Add time-aware query expansion for temporal questions; keep raw transcript retrievable.

**Move:**
- The **per-turn curator → background-only.** Per-turn rewrite pays latency every turn and risks over-compression. Make the inline write cheap (append/lightweight), let the **sleep-time sweep** (already built) do the consolidating rewrite — Mem0-style ADD/UPDATE/DELETE/NOOP conflict resolution.

**Add exactly one structured thing:**
- A small **timestamped, supersedable facts layer** (the "core/saved-memories" tier) with recency-aware ranking + reconfirmation (`last_confirmed_at`) freshness + a `pinned` flag. Fixes the named open problem ("employer fact is right until they change jobs"); gives identity/prefs a stable home that never depends on a retrieval hit.
- **Lightweight entities** (people/orgs as their own small docs with aliases) — the "names" fix — files, not a graph.

**Skip:** knowledge-graph backend; entity-relationship/community-detection; a separate *live* sleep-time agent; chasing LoCoMo; aggressive extraction that discards transcripts.

**One sentence:** *Keep the curated-docs + hybrid-search core; move the curator to an idle/step-triggered sleep-time pass doing conflict-resolved consolidation; add one structured layer — timestamped, supersedable facts with recency ranking + lightweight aliased entities — to win temporal/knowledge-update without ever touching a graph DB.*

---

### Key sources
Mem0 (arxiv 2504.19413) · Zep/Graphiti (2501.13956) · Sleep-time Compute (2504.13171) · Generative Agents (2304.03442) · LongMemEval (2410.10813) · Convomem (2511.10523) · A-Mem-vs-RAG neutral study (2510.23730) · LoCoMo audit (Penfield Labs) · Letta sleep-time + memory-blocks (letta.com) · Claude memory tool (platform.claude.com) · Mem0 OSS v2→v3 migration (docs.mem0.ai). Full per-thread output: session task `wea42e1js` (web) + Dex Slack mining agent.
