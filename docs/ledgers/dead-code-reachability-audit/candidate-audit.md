# Candidate audit

Current through `b94f576a1143ab5cc53f1bda6e7a3f196856d8ef` on 2026-08-03.

## Implementation disposition

- Removed every original **Z** candidate: 21 backend and 27 desktop declarations.
- Removed four desktop **T** setup-route wrappers because their only callers were obsolete contract tests and their paths are absent from the production bundle.
- Removed eight declarations orphaned by those deletions, ten unreferenced CSS selector groups, and one unused dependency.
- Retained the remaining 41 **T** candidates, all **K** candidates, server HTTP routes, and database schema because deletion safety was not proven.

Legend:

- **Z** — declaration has no production or test reference; no dynamic contract was found.
- **T** — no production reference; retained only by tests.
- **K** — keep/not dead; a dynamic, external, manual, or serialized contract explains the missing static edge.

`Z` and `T` prove production-unreferenced code, not deletion safety. Removal must still update tests/contracts and run the relevant suite.

## File-level graph

| Candidate | Status | Verification |
| --- | --- | --- |
| `apps/desktop/electron/preload.cjs` | K | Loaded by `BrowserWindow.webPreferences.preload` at `electron/main.cjs:449,536`. |
| `apps/desktop/scripts/remote-sim.mjs` | K | Standalone manual verification entry point documented at line 8; introduced as the remote-deployment simulation in commit `87822e87`. |

No file-level orphan remains after these two non-import edges are added. Python: 345/345 modules reachable. Desktop: 369/369 code files reachable.

## Backend top-level declarations

| Status | Symbol | Evidence |
| --- | --- | --- |
| T | `areas.paths.resolve_area_page` (`areas/paths.py:31`) | 0 production references; 4 test references. |
| Z | `automation.scheduler._CATCH_UP_CADENCE` (`automation/scheduler.py:68`) | Declaration only. |
| Z | `constants.BASH_TIMEOUT` (`constants.py:46`) | Declaration only. |
| Z | `constants.BASH_MAX_OUTPUT_CHARS` (`constants.py:49`) | Declaration only. |
| Z | `constants.BACKGROUND_AGENT_TIMEOUT` (`constants.py:59`) | Declaration only. |
| Z | `core.content.MessageContent` (`core/content.py:67`) | Declaration only. |
| Z | `events.triggers.TRIGGER_EVENT_TYPES` (`events/triggers.py:100`) | Declaration only. |
| Z | `execution.results.tool_result_from_record` (`execution/results.py:90`) | Declaration only; callers use payload-level conversion. |
| Z | `search.retrieval.rrf_merge` (`search/retrieval.py:13`) | Declaration only; `HybridRetriever._rrf_merge` is the live implementation. |
| Z | `server.deps.require_knowledge_runtime` (`server/deps.py:49`) | Declaration only; not used as a FastAPI dependency. |
| T | `server.routers.chat._keepalive` (`server/routers/chat.py:45`) | 0 production references; 2 test references; production calls `keepalive_chunk` directly. |
| Z | `server.routers.chat._resolve_run_id` (`server/routers/chat.py:656`) | Declaration only; `cancel_run` contains the live inline resolution. |
| T | `tools.core.collections.paginate` (`tools/core/collections.py:44`) | 0 production references; 4 test references. |
| T | `tools.scopes.SETTABLE_SCOPES` (`tools/scopes.py:41`) | 0 production references; 3 test references. |
| T | `wiki.pages.update_page_title` (`wiki/pages.py:403`) | 0 production references; 4 test references. |
| T | `wiki.pages.update_page_metadata` (`wiki/pages.py:409`) | 0 production references; 3 test references. |

## Backend methods

| Status | Symbol | Test references |
| --- | --- | --- |
| T | `SharedLedger.add_note` (`agent/ledger.py:166`) | 5 |
| T | `AreaWorkStore.create_work_item` (`areas/work_store.py:240`) | 4 |
| Z | `Scheduler._run_handler` (`automation/scheduler.py:819`) | 0 |
| T | `Scheduler._run_session_bound` (`automation/scheduler.py:836`) | 3 |
| Z | `Scheduler._matching_event_automations` (`automation/scheduler.py:858`) | 0 |
| T | `Scheduler._matching_message_automations` (`automation/scheduler.py:861`) | 1 |
| Z | `AutomationService.resolve_message_trigger` (`automation/service.py:159`) | 0 |
| T | `AutomationService.list_children` (`automation/service.py:685`) | 6 |
| T | `AutomationStore.update_last_run` (`automation/store.py:1426`) | 3 |
| T | `AutomationStore.update_last_run_if_next_run` (`automation/store.py:1449`) | 2 |
| Z | `AutomationStore.set_enabled_if_claim` (`automation/store.py:1883`) | 0 |
| Z | `AutomationStore.set_last_result` (`automation/store.py:2004`) | 0 |
| T | `AutomationStore.try_claim_idempotency` (`automation/store.py:2033`) | 30 |
| T | `AutomationStore.list_claims_for_parent` (`automation/store.py:2160`) | 2 |
| T | `AutomationStore.enqueue_event` (`automation/store.py:2180`) | 9 |
| Z | `AutomationStore.complete_event` (`automation/store.py:2211`) | 0 |
| T | `AutomationStore.fail_event` (`automation/store.py:2221`) | 1 |
| T | `AutomationStore.dead_letter_event` (`automation/store.py:2235`) | 3 |
| T | `SessionStore.get_tool_result` (`context/store.py:2474`) | 3 |
| T | `SessionStore.list_background_agent_events` (`context/store.py:3098`) | 4 |
| T | `SessionStore.list_chat_queued_messages` (`context/store.py:3284`) | 6 |
| T | `SessionStore.record_session_event` (`context/store.py:3413`) | 20 |
| T | `SessionStore.list_chat_compactions` (`context/store.py:3653`) | 1 |
| T | `SSEEvent.to_sse_string` (`events/sse.py:85`) | 1 |
| Z | `ExecutorCommandLog.prune_acked` (`execution/commands.py:100`) | 0 |
| Z | `FactLedger.validate_initialized` (`memory/facts/ledger.py:323`) | 0 |
| T | `FactLedger.active_subject_count` (`memory/facts/ledger.py:361`) | 1 |
| T | `OutboxStore.enqueue_run_failed` (`outbox/store.py:319`) | 2 |
| T | `ManagedFileRepository.inspect_commit` (`revisions/repository.py:231`) | 4 |
| T | `ManagedFileRepository.restore_from_commit` (`revisions/repository.py:335`) | 1 |
| Z | `DeviceSkillStore.drop_executor` (`skills/device_store.py:127`) | 0 |
| Z | `ToolRegistry.all_facts` (`tools/core/registry.py:148`) | 0 |
| T | `WikiRenameApprovalStore.supersede` (`wiki/approval_store.py:435`) | 15 |
| Z | `WikiEditCuratorQueueStore.list_jobs` (`wiki/curation/queue.py:181`) | 0 |
| T | `WikiService.link_report_for_path` (`wiki/service.py:605`) | 1 |

## Desktop exports

| Status | Symbol | Location |
| --- | --- | --- |
| Z | `saveArea` | `src/actions/sessions.ts:69` |
| Z | `queryString` | `src/api/memoryItems.ts:42` |
| Z | `GoogleServiceChoice` | `src/api/settings.ts:228` |
| Z | `removeGoogleAccountApi` | `src/api/settings.ts:346` |
| T | `getSetupStatusApi` | `src/api/settings.ts:350` |
| T | `saveGoogleCredentialsApi` | `src/api/settings.ts:354` |
| T | `preflightGoogleSetupApi` | `src/api/settings.ts:364` |
| T | `verifySlackTokenApi` | `src/api/settings.ts:374` |
| Z | `readFact` | `src/api/wiki.ts:323` |
| Z | `EmptyNote` | `src/components/ui/EmptyState.tsx:72` |
| T | `sliderPipStopCenter` | `src/components/ui/Slider.tsx:24` |
| T | `SLIDE_PAGE_VARIANTS` | `src/components/ui/TabPanels.tsx:15` |
| Z | `EditorSeed` | `src/features/automations/lib/schedule.ts:3` |
| Z | `relativeTime` | `src/features/memory/components/shared.tsx:7` |
| Z | `PrimaryBtn` | `src/features/memory/components/shared.tsx:25` |
| Z | `DangerBtn` | `src/features/memory/components/shared.tsx:58` |
| T | `clearDrafts` | `src/features/memory/lib/draftStore.ts:37` |
| Z | `isRecordListPage` | `src/features/memory/lib/format.ts:4` |
| Z | `scopeLabel` | `src/features/memory/lib/format.ts:16` |
| T | `findDir` | `src/features/memory/lib/workspaceTree.ts:72` |
| T | `googleChoiceLabel` | `src/features/settings/lib/setupAssistant.ts:8` |
| T | `parseMCPServerImport` | `src/features/settings/lib/setupAssistant.ts:53` |
| Z | `parseKeyValueLines` | `src/features/settings/lib/setupAssistant.ts:74` |
| T | `humanizeSlug` | `src/lib/format.ts:39` |
| Z | `isTopOverlay` | `src/lib/overlayStack.ts:50` |
| T | `SPRING_PEEK` | `src/lib/tokens/motion.ts:14` |
| T | `PEEK_ENTRY_LINEAR_CSS` | `src/lib/tokens/motion.ts:34` |
| T | `SPRING_SHEET` | `src/lib/tokens/motion.ts:38` |
| Z | `SHEET_ENTRY_LINEAR_CSS` | `src/lib/tokens/motion.ts:46` |
| T | `SHEET_CLEANUP_LINEAR_CSS` | `src/lib/tokens/motion.ts:56` |
| Z | `EASE_BLUR` | `src/lib/tokens/motion.ts:90` |
| Z | `TABS_LAYOUT_LINEAR_CSS` | `src/lib/tokens/motion.ts:225` |
| Z | `TRACE_ROW_LINEAR_CSS` | `src/lib/tokens/motion.ts:237` |
| Z | `DURATION_PANEL` | `src/lib/tokens/motion.ts:308` |
| Z | `POSE_INLINE_POPOVER_IN` | `src/lib/tokens/motion.ts:391` |
| Z | `POSE_INLINE_POPOVER_VISIBLE` | `src/lib/tokens/motion.ts:397` |
| Z | `POSE_INLINE_POPOVER_OUT` | `src/lib/tokens/motion.ts:404` |
| Z | `PAGE_VARIANTS` | `src/lib/tokens/motion.ts:460` |
| Z | `PAGE_ENTER_TRANSITION` | `src/lib/tokens/motion.ts:557` |
| Z | `PAGE_EXIT_TRANSITION` | `src/lib/tokens/motion.ts:559` |
| Z | `TABS_LAYOUT_TRANSITION` | `src/lib/tokens/motion.ts:566` |
| Z | `TABS_SLIDE_TRANSITION` | `src/lib/tokens/motion.ts:567` |

The production bundle independently confirms tree-shaking of the unused setup/remove-account API paths: `/setup/status`, `/setup/google/credentials`, `/setup/google/preflight`, `/setup/slack/verify`, and `/google/accounts/${id}` are absent from `dist/renderer`.

## Dynamic/static false positives retained

| Status | Symbol group | Dynamic contract |
| --- | --- | --- |
| K | Click commands and FastAPI route handlers | Registered by decorators. |
| K | `_OAuthCallbackHandler.do_GET/log_message`; nested MCP `CallbackHandler.do_GET/log_message` | Called by `http.server`. |
| K | `_DropAsgiCancelledError.filter` | Called through Python logging's `Filter` protocol. |
| K | `MCPTokenStorage.get_tokens/set_tokens/get_client_info/set_client_info` | MCP SDK storage protocol. |
| K | `APIKeyTokenVerifier.verify_token` | MCP SDK verifier protocol. |
| K | `TokenBudget.spent` | Exposed to dynamically executed workflow scripts as `budget.spent()`. |
| K | `FactPlanStatus.COMMITTING`, `PermissionDecision.REQUEST_APPROVAL`, `WikiHealthIssueOwner.MEMORY_MAINTENANCE`, `WorkflowState.WAITING_FOR_AUTH` | Serialized enum/public contract values; static member counts are not sufficient deletion evidence. |

## Coverage limits

- Name counting is conservative and can miss dead declarations whose name collides with an unrelated symbol.
- Static imports over-approximate liveness when an imported path is never executed.
- HTTP routes are external entry points. A route without a live desktop caller is not dead unless the external API contract is explicitly retired.
- CSS selectors, image assets, database columns/migrations, and declared third-party dependencies were not audited in this pass.
