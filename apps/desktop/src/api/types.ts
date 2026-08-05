import type { RuntimeRunStatus } from "@/api/events";

export type SessionType = "chat" | "channel" | "agent";

export interface Area {
  area_id: string;
  name: string;
  default_cwd: string | null;
  instructions: string | null;
  page_path: string | null;
  autonomy: "observe" | "act" | null;
  attention: "dormant" | "ambient" | "active";
  interrupts: "asks" | "all" | "none";
  paused_at: string | null;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export interface SessionListItem {
  session_id: string;
  started_at: string;
  last_activity: string;
  name: string | null;
  message_count: number;
  area_id?: string | null;
  /** Set when the session was provisioned from an area room's scoped
   *  composer — attributes the session back to `/areas/{key}` detail. */
  /** Per-chat model override. null/undefined → falls back to the global
   *  default (config.chat_model), which is also what new chats inherit. */
  chat_model?: string | null;
  /** "chat" for normal user conversations; "channel" for agent-spawned
   *  feed sessions (post-mode loop output, push-style updates). */
  session_type?: SessionType;
  /** When set, the channel session was spawned by this automation. */
  origin_automation_id?: string | null;
  parent_session_id?: string | null;
  parent_tool_call_id?: string | null;
  agent_type?: string | null;
  agent_status?: string | null;
  active_run_id?: string | null;
  run_status?: RuntimeRunStatus | null;
  checkpoint_seq?: number;
  latest_event_seq?: number;
  is_active?: boolean;
  pending_approvals_count?: number;
  queued_messages_count?: number;
  run_error_code?: string | null;
  run_stop_reason?: string | null;
}

export interface SessionGoal {
  session_id: string;
  goal_id: string;
  objective: string;
  status: "active" | "paused" | "blocked" | "budget_limited" | "complete";
  evidence: { text: string; created_at: string }[];
  blocked_reason?: string | null;
  token_budget?: number | null;
  tokens_used: number;
  time_used_seconds: number;
  created_at: string;
  updated_at: string;
}

export interface ContextBudgetSnapshot {
  model: string;
  hard_limit: number;
  compaction_trigger: number;
  message_limit: number;
  input_tokens: number;
  message_count: number;
}

export type TodoStatus = "pending" | "in_progress" | "completed";

export interface TodoListItem {
  content: string;
  status: TodoStatus;
}

export type ToolOverrideDecision = "approve" | "ask" | "deny";

export interface ToolPolicyMetadata {
  action: "read" | "draft" | "write" | "execute";
  scope: "internal" | "external";
  requires_approval: boolean;
  requires_user_interaction: boolean;
  destructive: boolean | null;
  open_world: boolean | null;
  idempotent: boolean | null;
  approval_mode: "never" | "always";
  permissions: string[];
  timeout_seconds: number | null;
  audit: boolean;
  max_result_chars: number | null;
  offload: boolean;
}

export interface ToolMetadata {
  name: string;
  display_name: string;
  description: string;
  kind: string;
  source?: string | null;
  policy: ToolPolicyMetadata;
  override?: ToolOverrideDecision;
}

export interface SkillDescriptor {
  name: string;
  description: string;
  /** Where the skill came from: "builtin", "user", "global", "area". */
  location?: string;
  /** Absolute filesystem path to the SKILL.md file (when available). */
  path?: string;
}

export interface ServerConfig {
  chat_model: string;
  /** Hard token ceiling of the active chat model. */
  chat_model_max_context: number;
  /** Configured compaction ceiling and actual trigger after server headroom. */
  compaction_token_limit: number;
  compaction_token_trigger: number;
  research_model: string;
  /** Default model for workflow agents. */
  workflow_model: string;
  memory_model: string;
  embedding_model: string | null;
  web_search: "auto" | "exa" | "ddgs" | "none";
  web_search_provider: string;
  max_depth: number;
  reasoning_effort: string | null;
  reasoning_efforts: string[];
  model_reasoning_efforts: Record<string, string>;
  /** Per-role setup, keyed by role (research | workflow | auxiliary | memory). A role's
   *  effort overrides the per-model map, which every role pointed at the same
   *  model would otherwise share. */
  /** Absent on a server older than the role split — the Models tab says so
   *  rather than the whole config failing to parse and taking every other
   *  settings surface down with it. */
  model_roles?: Record<string, RoleModelSetup>;
  compression_threshold: number;
  max_messages: number;
  compression_keep_ratio: number;
  summary_max_tokens: number;
  max_space_gb: number | null;
  storage_backup_retention_days: number;
  storage_allow_archived_cleanup: boolean;
  storage_allow_delete_cold_chats: boolean;
  storage_allow_current_cleanup: boolean;
  storage_current_inactive_days: number;
  storage_current_minimum: number;
  memory_enabled: boolean;
  integrations: Record<string, Record<string, unknown>>;
  tool_overrides: Record<string, ToolOverrideDecision>;
}

export interface StorageStatus {
  status: "pending" | "disabled" | "ok" | "reclaimed" | "quota_blocked" | "error";
  total_bytes: number;
  reclaimable_bytes: number;
  protected_bytes: number;
  reclaimed_bytes: number;
  max_bytes: number | null;
  target_bytes: number | null;
  checked_at: string | null;
  error?: string | null;
  categories: StorageCategory[];
  reclaimed_by_category: Record<string, number>;
  database_reclaim_mode: string | null;
  last_plan?: Record<string, unknown> | null;
  last_run?: Record<string, unknown> | null;
  next_maintenance_at?: string | null;
}

export interface StorageCategory {
  id: string;
  label: string;
  total_bytes: number;
  reclaimable_bytes: number;
  item_count: number;
  policy_tier: number | null;
  protection_reason: string | null;
  description: string;
  measurement_kind: "physical" | "logical_estimate";
}

export interface StoragePlanRequest {
  target_gb?: number | null;
  allow_archived_chats?: boolean;
  allow_delete_cold_chats?: boolean;
  allow_current_chats?: boolean;
  current_session_id?: string | null;
  pinned_session_ids?: string[];
}

export interface StoragePlanAction {
  tier: number;
  kind: string;
  category_id: string;
  resource_id: string;
  estimated_reclaimable_bytes: number;
  destructive: boolean;
  description: string;
}

export interface StoragePlan {
  plan_id: string;
  before_bytes: number;
  target_bytes: number;
  estimated_after_bytes: number;
  estimated_reclaimable_bytes: number;
  attainable: boolean;
  actions: StoragePlanAction[];
  blockers: string[];
  created_at: string;
}

export interface StorageCleanupRun {
  plan_id: string;
  reclaimed_bytes: number;
  actions_completed: number;
  status: StorageStatus;
}

export interface StorageBackup {
  relative_path: string;
  size_bytes: number;
  modified_at: string;
  kept: boolean;
  expired: boolean;
}

export interface RoleModelSetup {
  model: string | null;
  reasoning_effort: string | null;
}

export interface ModelGroup {
  provider: string;
  label: string;
  models: string[];
}

export interface ModelsResponse {
  models: string[];
  groups: ModelGroup[];
  reasoning_efforts: Record<string, string[]>;
  chat_model: string;
  research_model: string;
  workflow_model: string;
  memory_model: string;
}

// ─── Automations ───────────────────────────────────────────────────

export type AutomationTriggerType = "time" | "event" | "idle" | "count" | "message";

export interface AutomationTrigger {
  type: AutomationTriggerType;
  // Time
  at?: string;
  days?: string;
  every?: string;
  start?: string;
  end?: string;
  // Event
  event_type?: string;
  lead_minutes?: number;
  // Idle
  idle_minutes?: number;
  // Count
  every_n?: number;
  threshold?: number;
  scope?: string;
  // Message (slack watcher). The editor submits names (channel/from_user);
  // the server resolves them to ids at save time and echoes both the *_id and
  // *_name fields back on read.
  source?: string;
  // Channel names on the way in (editor → server); the server resolves them
  // and echoes back {id,name} objects on read. One or more channels.
  channels?: (string | { id: string; name: string })[];
  from_user?: string;
  from_user_id?: string | null;
  from_user_name?: string | null;
  contains?: string[];
}

export type AutomationKind = "automation" | "loop";

/** Mirrors ScopeKey in apps/server/arden/tools/scopes.py. Custodians and
 *  builtin workers carry the fine-grained keys; only the two in
 *  SettableToolScope may be chosen by a person or the agent. */
export type ToolScope =
  | SettableToolScope
  | "area_observe"
  | "area_act"
  | "area_reply"
  | "area_action"
  | "fact_maintenance"
  | "fact_retention"
  | "daily_notes"
  | "wiki_maintenance"
  | "wiki_producer";

export type SettableToolScope = "read_only" | "all";

export interface Automation {
  task_id: string;
  name: string;
  /** Concise user-facing sentence shown beneath the automation name. */
  description: string | null;
  description_source: "manual" | "generated" | null;
  /** Full execution instructions. Never render this as descriptive copy. */
  prompt: string;
  model: string | null;
  triggers: AutomationTrigger[];
  enabled: boolean;
  created_at: string;
  last_run_at: string | null;
  next_run_at: string | null;
  /** Most recent run outcome ("completed" | "failed" | "running"), and the
   *  last few statuses newest-first for the card sparkline. Null/empty until
   *  the automation has fired since run-history landed. */
  last_status?: string | null;
  recent_statuses?: string[];
  /** Textual output from the most recent run (markdown). Null until the
   *  automation has actually run once. */
  last_result: string | null;
  auto_approve: boolean;
  running_since: string | null;
  handler: string | null;
  builtin: boolean;
  cooldown_minutes: number | null;
  /** Server-owned execution boundary — a key into the server's scope table,
   * never a tool list. Preserved when duplicating a visible automation so a
   * copy does not silently gain broader tool access. */
  tool_scope?: ToolScope;
  /** What that key grants, rendered by the server (`read | submit_area_report`)
   *  so the client never re-derives it. */
  tool_scope_label?: string | null;
  /** "automation" for standard scheduled tasks; "loop" for self-paced /loop
   *  and post-mode tasks. The composer already surfaces loops in a chip, so
   *  the desktop hides kind=loop from the main automation list. */
  kind?: AutomationKind;
  /** Loops with read_history=false are "channels" (post-mode feeds) — their
   *  spawned sessions are channel-type rather than chat-type. */
  read_history?: boolean;
  idempotency_key?: string | null;
  idempotency_scope?: "global" | "run" | "attempt" | null;
}

export interface CreateAutomationPayload {
  name: string;
  prompt: string;
  /** Stable per-create operation identity. Retries reuse this key. */
  idempotency_key?: string;
  idempotency_scope?: "global";
  /** Omit to have Arden generate concise display copy. */
  description?: string;
  model?: string | null;
  trigger_type?: AutomationTriggerType;
  at?: string;
  days?: string;
  every?: string;
  event_type?: string;
  lead_minutes?: number | string;
  idle_minutes?: number;
  every_n?: number;
  auto_approve?: boolean;
  start?: string;
  end?: string;
  triggers?: AutomationTrigger[];
  cooldown_minutes?: number | null;
  tool_scope?: SettableToolScope;
}

export type UpdateAutomationPayload = Partial<
  CreateAutomationPayload & { enabled?: boolean }
>;

export interface AutomationRun {
  id: number;
  task_id: string;
  chat_session_id: string | null;
  started_at: string;
  ended_at: string | null;
  status: string;
  result: string | null;
  error: string | null;
}

export interface BackgroundTaskSummary {
  task_id: string;
  child_run_id?: string | null;
  child_session_id?: string | null;
  session_id?: string;
  parent_run_id?: string | null;
  parent_tool_call_id?: string | null;
  agent_type?: string | null;
  wait?: boolean | null;
  status?: "running" | "completed" | "failed" | "cancelled" | "interrupted" | "cancel_requested" | string;
  command: string;
  detail?: string | null;
  result_ref?: string | null;
}
