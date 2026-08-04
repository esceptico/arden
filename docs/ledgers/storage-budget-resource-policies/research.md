# Research

## Surface research

- **Scope**: Current storage inventory/reporting, Settings UI, archive/chat lifecycle, pin/active protections, complete session deletion, and physical SQLite reclamation.
- **Sources inspected**: `storage_budget.py`, runtime/health/config schemas, `ArchiveTab.tsx`, shared tooltip components, session store/service/router/actions, live `~/.arden` file sizes, read-only live SQLite schema/counts, and official Docker, Apple, GitHub, GitLab, OpenAI, LangGraph/LangSmith, Langfuse, and SQLite documentation.
- **Observations**: The existing implementation safely deletes only stale unreferenced tool-result blobs. All other files are one undifferentiated complement named “protected.”
- **Negative evidence**: No category inventory, cleanup-plan contract, server-owned pins, complete session purge, or online physical database reclamation exists.

## Consolidated findings

| ID | Type | Claim | Evidence | Implication | Confidence | Last checked |
| --- | --- | --- | --- | --- | --- | --- |
| F-01 | Fact | The API exposes only aggregate total/reclaimable/protected/reclaimed values. | `apps/server/arden/storage_budget.py:15-24`; `apps/server/arden/server/schemas.py:204-213` | Add a typed, additive category list rather than deriving UI labels from paths. | High | 2026-08-03 |
| F-02 | Fact | “Protected” is calculated as total minus stale unreferenced tool-result candidates; it is not a semantic protection registry. | `apps/server/arden/storage_budget.py:75-100,118-137` | Rename the aggregate and model protection/reclaimability per resource. | High | 2026-08-03 |
| F-03 | Fact | Current automatic cleanup recognizes only content-addressed `.txt.gz` tool-result blobs older than seven days and absent from live references. | `apps/server/arden/storage_budget.py:10-12,57-65,83-98,105-116` | Existing deletion remains tier 0; every new resource owner needs an explicit policy adapter. | High | 2026-08-03 |
| F-04 | Fact | Live Arden usage was 6.2 GiB: archive 3.4 GiB, sessions DB 2.3 GiB, blobs 406 MiB, memory 28 MiB, search DB 26 MiB, and logs 16 MiB. | `du -sh ~/.arden{,/archive,/sessions.db,/blobs,/memory,/search.db,/logs}`; V-01 | The UI should lead with backups, chat history, and blobs rather than one “protected” number. | High | 2026-08-03 |
| F-05 | Fact | `~/.arden/archive` contains explicit compressed migration/merge backups; archived chats remain rows in `sessions.db`. | Read-only `find ~/.arden/archive -maxdepth 2 -type f`; `apps/server/arden/context/store.py:79-98,474-477` | Present “Backup archives” and “Archived chats” as separate categories and cleanup tiers. | High | 2026-08-03 |
| F-06 | Fact | The UI/server already accept any limit at least 0.1 GB, including below current usage, but enforcement can only clean tier-0 blobs and otherwise returns `quota_blocked`. | `apps/desktop/src/features/settings/components/ArchiveTab.tsx:48-64,77-100`; `apps/server/arden/storage_budget.py:101-127`; `apps/server/arden/server/schemas.py:536-547` | Replace save-and-immediately-enforce with plan, confirm, then execute. | High | 2026-08-03 |
| F-07 | Fact | Permanent archived-session deletion removes the `sessions` row and a legacy offloaded-result directory, not normalized messages/events/tool rows. | `apps/server/arden/context/store.py:477,4024-4025`; `apps/server/arden/services/session.py:681-687` | Build one transactional, ownership-complete purge before quota-driven chat deletion. | High | 2026-08-03 |
| F-08 | Fact | The live schema has 22 tables with a `session_id` column; session child tables do not declare a foreign key to `sessions`. | Read-only query over `sqlite_master` + `pragma_table_info`; representative schema at `apps/server/arden/context/store.py:79-118` | Maintain a canonical purge registry/contract and test every owned table, blob, index, and projection. | High | 2026-08-03 |
| F-09 | Fact | The live session database has `auto_vacuum=NONE`; current code shrinks it only through offline `VACUUM INTO`. | `PRAGMA auto_vacuum` returned `0`; `apps/server/arden/maintenance/session_compaction.py:617` | Planner estimates must distinguish logical deletion from physically reclaimed bytes and coordinate compaction. | High | 2026-08-03 |
| F-10 | Fact | Session pins are desktop preferences and are removed when a chat is archived; the server has no durable pin authority. | `apps/desktop/src/stores/types.ts:89-90`; `apps/desktop/src/actions/sessions.ts:118-135` | Persist server-visible protections before server-side cleanup can honor pins. | High | 2026-08-03 |
| F-11 | Fact | The archived-session API returns only 20 rows. | `apps/server/arden/context/store.py:4002-4022`; `apps/server/arden/server/routers/session.py:923-926` | Cleanup planning needs paginated/all-candidate inventory independent of the current UI list. | High | 2026-08-03 |
| F-12 | Fact | A house tooltip component already supports focus/hover hints with supplementary accessible text. | `apps/desktop/src/components/ui/Tooltip.tsx:31-58` | Reuse it for category definitions; do not introduce native-title-only help. | High | 2026-08-03 |
| F-13 | Observation | The live DB contained 302 archived and 1,201 current session rows. Archived transcript/event bodies were about 463 MiB logically; current bodies about 1.1 GiB. Orphan message/event rows also existed. | Read-only joined aggregate queries; V-05 | Category estimates need counts and logical bytes, while purge verification must catch pre-existing orphans. | Medium | 2026-08-03 |
| F-14 | External precedent | Docker separates total/active/size/reclaimable by type, defaults to conservative explicit pruning, supports age/label filters, and requires separate opt-in for volumes; BuildKit adds maximum-used, minimum-free, reserved-space, and in-use concepts. | [Docker system df](https://docs.docker.com/reference/cli/docker/system/df/); [pruning](https://docs.docker.com/engine/manage-resources/pruning/); [buildx prune](https://docs.docker.com/reference/cli/docker/buildx/prune/) | Arden should show total + reclaimable per category, expose a reserve/protected floor, and require separate consent for history tiers. | High | 2026-08-03 |
| F-15 | External precedent | macOS Storage uses a category chart, recommendations, per-category detail/info, sortable large files, and view-only categories for system-managed data. | [Apple Storage settings](https://support.apple.com/en-ca/guide/mac-help/mchl3d437fbc/mac) | Use category rows plus recommendations/actions; some categories can be informative without a delete button. | High | 2026-08-03 |
| F-16 | External precedent | GitHub applies configurable day-based retention to logs/artifacts; GitLab combines expiry with a manual Keep action and a latest-successful protection that can be disabled. | [GitHub retention](https://docs.github.com/en/organizations/managing-organization-settings/configuring-the-retention-period-for-github-actions-artifacts-and-logs-in-your-organization); [GitLab artifacts](https://docs.gitlab.com/ci/jobs/job_artifacts/) | Backup/log-like artifacts should use visible TTLs plus manual Keep rather than indefinite blanket protection. | High | 2026-08-03 |
| F-17 | External precedent | ChatGPT distinguishes archive from deletion: archived chats remain retained; deletion is explicit and irreversible from the user's perspective. | [OpenAI chat retention](https://help.openai.com/en/articles/8809935-how-chat-retention-works-in-chatgpt) | Arden must not imply that archiving frees space; conversion/deletion states and consequences need distinct labels. | High | 2026-08-03 |
| F-18 | External precedent | LangGraph offers `delete` and `keep_latest` checkpoint TTL strategies plus access-refreshed store TTL; it also warns that default full-state checkpoints grow and offers delta storage. | [LangGraph TTL](https://docs.langchain.com/langsmith/configure-ttl); [persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | Base eligibility on last meaningful use, keep a recoverable latest state where appropriate, and compress/delta old history before deletion. | High | 2026-08-03 |
| F-19 | External precedent | LangSmith keeps ordinary traces for 14 days and promotes traces with feedback/rules/queues to extended retention; Langfuse supports project TTL, nightly deletion, and recurring blob export for data that must outlive the window. | [LangSmith retention](https://docs.langchain.com/langsmith/administration-overview); [Langfuse retention](https://langfuse.com/docs/administration/data-retention) | Use a short default for low-value harness artifacts, promote explicitly valuable chats, and require export/restore proof before cold deletion. | High | 2026-08-03 |
| F-20 | External precedent | OpenAI Agents SDK separates retrieved/model-facing history from persisted session history and recommends running potentially blocking session compaction manually or during idle time. | [OpenAI Agents sessions](https://openai.github.io/openai-agents-python/sessions/) | Retention/storage conversion must stay distinct from prompt-context pruning and must not extend active run latency. | High | 2026-08-03 |
| F-21 | Fact/external | SQLite `INCREMENTAL` mode requires a one-time `VACUUM` migration from `NONE`, after which bounded `incremental_vacuum(N)` calls truncate freelist pages; `FULL` can worsen fragmentation. | [SQLite PRAGMA](https://www.sqlite.org/pragma.html#pragma_auto_vacuum) | Use one verified offline migration, then bounded idle reclamation instead of a full compaction after every purge. | High | 2026-08-03 |

## Conflicts and gaps

- **Archive ambiguity resolved**: explicit backup files are removed before archived chat rows; both precede inactive current chats.
- **Physical versus logical size**: file/directory totals are physical-budget inputs. Per-chat/table values are estimates until a compaction plan maps deleted rows to actual file shrinkage.
- **Recoverability remains open**: summarized trajectories are not a full restore artifact. A cold-chat tier cannot claim recoverability until export/import parity is proven.
- **Current purge contract is unsafe for quota use**: it can make a chat disappear from the UI while retaining most database bytes.
- **Common defaults differ by data value**: artifact systems commonly expire low-value outputs; consumer chat products retain chats until deletion. Arden should therefore default backups to TTL but current chats to keep-forever.
- **“Keep latest” is not universal**: it is valuable for deploy artifacts but conflicts with this user's fast-changing harness backups. Arden uses a bounded rollback window plus manual Keep, not an immortal newest backup.

## Supporting material

### Proposed resource categories

| Category | Examples | Default policy |
| --- | --- | --- |
| Backup archives | `~/.arden/archive/**` | Retention-managed; oldest first; preserve configured recovery floor |
| Chat history | `sessions.db` rows, split archived/current | User history; archived before inactive current; explicit opt-in |
| Tool results and blobs | `blobs/tool-results`, legacy offloads | Expiry/reference managed |
| Search/index data | Search databases and derived indexes | Rebuildable; safe tier |
| Memory/wiki/artifacts | Canonical user knowledge and produced files | Never inferred disposable from age alone |
| Logs/transient files | Rotated logs, WAL/SHM, temporary candidates | Rotation/rebuild rules; never unlink live SQLite sidecars |
| Configuration/secrets | Settings, OAuth tokens, credentials | Always protected; category total only, never enumerate secrets in UI |

### Accepted cleanup ladder

1. Expired/rebuildable data and stale unreferenced blobs.
2. Old explicit backup archives within a declared retention floor.
3. Archived chats, oldest first, after a complete purge contract and optional cold-export policy.
4. Inactive current chats, oldest first, only with separate explicit opt-in.
5. Stop at the protected floor and explain blockers; never silently cross it.

### Proposed default policy

| Resource | Default | Pressure behavior |
| --- | --- | --- |
| Auto-created backups/log archives | Expire after 14 days; manual Keep exempts | Delete oldest expired first; no permanent newest-backup floor |
| Rebuildable/search/cache data | Owner-specific short TTL/reference rule | Remove automatically and regenerate lazily |
| Archived chats | Retain; cold-convert oldest under pressure | Full-fidelity compressed bundle with metadata stub and proven restore; permanent deletion requires explicit history-tier consent |
| Current chats | Keep indefinitely | Optional aggressive tier: inactive ≥90 days, keep ≥100 newest, interactive-only, revalidate protections |
| Pinned/current/active/goal/automation chats | Never eligible | Report as protected floor with exact reasons |

### UX borrowed from precedents

- A macOS/Docker-style size-sorted category view: total, reclaimable, item count, policy, and Info tooltip.
- Recommendations above raw controls: “Remove 3.4 GB of expired harness backups,” “Convert 12 old archived chats,” or “Target cannot be reached without current chats.”
- A Docker-style explicit action preview listing what will be affected and the measured/estimated reclaimed bytes.
- GitLab-style Keep exceptions for individual backup artifacts and future cold chats.
- Clear state vocabulary: **Active**, **Archived** (hidden, still hot), **Cold** (compressed/restorable), **Pending deletion**, **Deleted**.
