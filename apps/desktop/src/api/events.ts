import type { HistoryImage } from "@/api/chat";
import type { SessionGoal, TodoListItem } from "@/api/types";

export type ToolOutcomeStatus = "succeeded" | "failed" | "denied" | "uncertain";

export interface ToolOutcome {
  status: ToolOutcomeStatus;
  error?: {
    code: string;
    retryable: boolean;
    recovery_action?: string;
    diagnostic_ref?: string;
  };
  effect?: {
    operation: string;
    target: string;
    before_ref?: string;
    after_ref?: string;
  };
  verification?: {
    postcondition: string;
    observed: string;
    confidence?: number;
  };
  receipt?: string;
}

export type RuntimeRunStatus =
  | "pending"
  | "running"
  | "backgrounded"
  | "interrupted"
  | "error"
  | "failed"
  | "cancelled"
  | "completed";

export interface RuntimeApprovalSnapshot {
  tool_id: string;
  tool_name: string;
  preview?: string | null;
  diff?: string | null;
  status: "pending";
  requested_at?: string | null;
  run_id?: string | null;
  session_id?: string | null;
  action?: string | null;
  scope?: string | null;
  expires_at?: string | null;
  description?: string | null;
  agent_type?: string | null;
  agent_name?: string | null;
  parent_session_id?: string | null;
}

export interface RuntimeConnectionSnapshot {
  tool_id: string;
  run_id: string;
  integration_id: string;
  connection_id: string;
  label: string;
  reason: ConnectionState;
  detail: string;
  capability: string;
  action: ConnectionAction;
  settings_tab: string;
  required_scopes: string[];
  source: "recovery" | "suggestion";
}

export type ConnectionState = "not_configured" | "disabled" | "auth_required" | "scope_required" | "degraded";
export type ConnectionAction = "oauth" | "credentials" | "enable" | "settings";

export interface RuntimeQueuedMessageSnapshot {
  client_id: string;
  text: string;
  images?: HistoryImage[];
  status: "pending" | "failed";
  server_status?: string | null;
  enqueued_at?: string | null;
  run_id?: string | null;
}

export interface ActiveRunSnapshot {
  run_id: string;
  status: RuntimeRunStatus;
  started_at?: string | null;
  updated_at?: string | null;
  ended_at?: string | null;
  stop_reason?: string | null;
  checkpoint_seq: number;
  latest_event_seq: number;
  error_code?: string | null;
  error_message?: string | null;
  pending_approvals: RuntimeApprovalSnapshot[];
  pending_connections: RuntimeConnectionSnapshot[];
  queued_messages: RuntimeQueuedMessageSnapshot[];
}

export interface SessionRuntimeSnapshot {
  session_id: string;
  latest_event_seq: number;
  checkpoint_seq: number;
  active_run: ActiveRunSnapshot | null;
  pending_approvals: RuntimeApprovalSnapshot[];
  pending_connections: RuntimeConnectionSnapshot[];
  queued_messages: RuntimeQueuedMessageSnapshot[];
}

/** AG-UI-shaped event protocol. Every event carries a `timestamp` (Unix ms). */
type CommonServerEventFields = { timestamp?: number; seq?: number; session_id?: string; replay?: boolean };
type CompactionOwner =
  | { scope?: "run"; parent_tool_call_id?: null }
  | { scope: "agent"; parent_tool_call_id: string };

export type ServerEvent = CommonServerEventFields & (
  // ─── Run lifecycle ──────────────────────────────────────────────────
  | { type: "RUN_STARTED"; run_id: string; session_id: string; session_name?: string | null; skip_approvals?: boolean; is_meta_run?: boolean; meta_client_id?: string | null }
  | { type: "session_updated"; session_id: string; name?: string | null }
  | { type: "RUN_FINISHED"; run_id: string; usage?: { prompt: number; completion: number; total?: number; cache_read?: number; cache_write?: number; cost: number }; context_input_tokens?: number | null; message_count?: number }
  | { type: "run_cancelled"; run_id: string }
  | { type: "run_backgrounded"; run_id: string; session_id?: string }
  | { type: "RUN_ERROR"; run_id: string; message: string; code?: string; debug_id?: string | null; recoverable?: boolean }
  | { type: "token_usage"; run_id: string; usage: { prompt: number; completion: number; total?: number; cache_read?: number; cache_write?: number }; cost?: number; message_count?: number | null; scope?: "run" | "tool"; task_id?: string | null; child_run_id?: string | null; workflow_id?: string | null; phase?: string | null }
  | { type: "thinking"; status: string; run_id?: string | null }

  // ─── Text messages (Start / Content / End) ─────────────────────────
  | { type: "TEXT_MESSAGE_START"; message_id: string; role?: string; depth?: number }
  | { type: "TEXT_MESSAGE_CONTENT"; message_id: string; delta: string; depth?: number }
  | { type: "TEXT_MESSAGE_END"; message_id: string; content?: string; depth?: number }

  // ─── Tool calls (Start / Args / End / Result) ──────────────────────
  | { type: "TOOL_CALL_START"; tool_call_id: string; tool_call_name: string; description?: string; display_name?: string; parent_message_id?: string | null; depth?: number; parent_id?: string | null; kind?: string; icon?: string | null; noun?: string | null; source?: string | null }
  | { type: "TOOL_CALL_ARGS"; tool_call_id: string; delta: string; depth?: number; parent_id?: string | null }
  | { type: "TOOL_CALL_END"; tool_call_id: string; depth?: number; parent_id?: string | null }
  | { type: "TOOL_CALL_RESULT"; tool_call_id: string; name: string; content?: string; preview?: string; display_name?: string; depth?: number; parent_id?: string | null; kind?: string; is_error?: boolean; duration_ms?: number; data?: Record<string, unknown> | null; source_refs?: unknown; outcome?: ToolOutcome | null }

  // ─── Reasoning ─────────────────────────────────────────────────────
  | { type: "REASONING_START"; message_id: string; depth?: number }
  | { type: "REASONING_MESSAGE_START"; message_id: string; role?: string; depth?: number }
  | { type: "REASONING_MESSAGE_CONTENT"; message_id: string; delta: string; depth?: number }
  | { type: "REASONING_MESSAGE_END"; message_id: string; depth?: number }
  | { type: "REASONING_END"; message_id: string; depth?: number }

  // ─── arden-specific (non-AG-UI canonical) ───────────────────────────
  | { type: "approval_needed"; tool_id: string; name: string; path?: string | null; diff?: string | null; content_preview?: string | null; run_id?: string | null; session_id?: string | null; agent_type?: string | null; agent_name?: string | null; action?: string | null; scope?: string | null; expires_at?: string | null }
  | { type: "input_needed"; tool_id: string; name: string; title: string; html: string }
  | { type: "connection_needed"; run_id: string; tool_id: string; integration_id: string; connection_id: string; label: string; reason: ConnectionState; detail: string; capability: string; action: ConnectionAction; settings_tab: string; required_scopes: string[]; source: "recovery" | "suggestion" }
  | {
      type: "background_task";
      event_id?: string | null;
      task_id: string;
      session_id?: string;
      run_id?: string | null;
      child_run_id?: string | null;
      child_session_id?: string | null;
      parent_tool_call_id?: string | null;
      agent_type?: string | null;
      wait?: boolean | null;
      command: string;
      status: "started" | "activity" | "completed" | "failed" | "cancelled" | "interrupted" | "cancel_requested" | string;
      detail?: string | null;
      result_ref?: string | null;
      model_visible?: boolean;
      ui_visible?: boolean;
      terminal?: boolean;
    }
  | { type: "stream_reset"; reason: "replay_gap" | string }
  | { type: "stream_keepalive"; latest_seq: number }
  | { type: "task_started"; session_id?: string | null; run_id: string; task_id: string; parent_task_id?: string | null; parent_tool_call_id?: string | null; child_run_id?: string | null; child_session_id?: string | null; agent_type?: string | null; wait?: boolean | null; name?: string; summary?: string; depth?: number; workflow_id?: string | null; phase?: string | null }
  | { type: "task_progress"; session_id?: string | null; run_id: string; task_id: string; parent_task_id?: string | null; parent_tool_call_id?: string | null; child_run_id?: string | null; child_session_id?: string | null; agent_type?: string | null; wait?: boolean | null; name?: string; status?: string; summary?: string; depth?: number; workflow_id?: string | null; phase?: string | null }
  | { type: "task_finished"; session_id?: string | null; run_id: string; task_id: string; parent_task_id?: string | null; parent_tool_call_id?: string | null; child_run_id?: string | null; child_session_id?: string | null; agent_type?: string | null; wait?: boolean | null; name?: string; status: "completed" | "failed" | "cancelled"; summary?: string; depth?: number; workflow_id?: string | null; phase?: string | null; tool_count?: number | null }
  | { type: "workflow_started"; session_id?: string | null; run_id: string; workflow_id: string; parent_tool_call_id?: string | null; name?: string; description?: string; phases?: string[] }
  | { type: "workflow_finished"; session_id?: string | null; run_id: string; workflow_id: string; status: "completed" | "failed" | "cancelled"; summary?: string; agent_count?: number }
  | ({ type: "compaction_started"; run_id: string } & CompactionOwner)
  | ({ type: "compaction_finished"; run_id: string; messages_before: number; messages_after: number } & CompactionOwner)
  | { type: "message_ingested"; client_id: string; run_id: string }
  | { type: "goal_updated"; session_id: string; goal: SessionGoal }
  | { type: "goal_cleared"; session_id: string }
  | { type: "todo_updated"; run_id: string; tool_call_id?: string | null; explanation?: string | null; items: TodoListItem[] }
);
