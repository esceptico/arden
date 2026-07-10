import type { RuntimeRunStatus } from "@/api/events";

export type SessionType = "chat" | "channel" | "agent";

export interface Area {
  area_id: string;
  name: string;
  default_cwd: string | null;
  instructions: string | null;
  knowledge_scope: string;
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
  /** Default model for workflow agents; optional until the server ships the key. */
  workflow_model?: string;
  memory_model: string;
  embedding_model: string;
  web_search: "auto" | "exa" | "ddgs" | "none";
  web_search_provider: string;
  google_enabled: boolean;
  max_depth: number;
  reasoning_effort: string | null;
  reasoning_efforts: string[];
  model_reasoning_efforts: Record<string, string>;
  compression_threshold: number;
  max_messages: number;
  compression_keep_ratio: number;
  summary_max_tokens: number;
  consolidation_interval: number;
  memory_enabled: boolean;
  integrations: Record<string, Record<string, unknown>>;
  tool_overrides: Record<string, ToolOverrideDecision>;
}

export interface ModelGroup {
  provider: string;
  models: string[];
}

export interface ModelsResponse {
  models: string[];
  groups: ModelGroup[];
  reasoning_efforts: Record<string, string[]>;
  chat_model: string;
  research_model: string;
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

export interface Automation {
  task_id: string;
  name: string;
  description: string;
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
  /** "automation" for standard scheduled tasks; "loop" for self-paced /loop
   *  and post-mode tasks. The composer already surfaces loops in a chip, so
   *  the desktop hides kind=loop from the main automation list. */
  kind?: AutomationKind;
  /** Loops with read_history=false are "channels" (post-mode feeds) — their
   *  spawned sessions are channel-type rather than chat-type. */
  read_history?: boolean;
}

export interface CreateAutomationPayload {
  name: string;
  description: string;
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
  /** When set, the server marks the originating suggestion `accepted` on
   *  successful create (see suggestionToPayload / AutomationSuggestion). */
  from_suggestion_id?: string;
}

export type UpdateAutomationPayload = Partial<
  CreateAutomationPayload & { enabled?: boolean }
>;

export interface AutomationRun {
  id: number;
  task_id: string;
  started_at: string;
  ended_at: string | null;
  status: string;
  result: string | null;
  error: string | null;
}

/** Contextual, server-synthesized automation the user can accept in one
 *  click. Mirrors the `GET /automations/suggestions` response shape. */
export interface AutomationSuggestion {
  id: string;
  name: string;
  description: string;
  triggers: AutomationTrigger[];
  rationale: string;
  evidence: string[];
  category: string;
  icon: string | null;
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
