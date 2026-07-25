import type { ServerEvent } from "@/api/events";
import type { ActivityItem } from "@/stores/index";

export interface PendingToolCall {
  name: string;
  displayName?: string;
  argsBuffer: string;
  depth: number;
  parentId: string | null;
  semanticKind: string;
  startSeq?: number;
  icon?: string;
  noun?: string;
  source?: string;
}

export interface TranscriptProjectionState {
  pendingResultPatches: Map<string, Partial<ActivityItem>>;
  pendingToolCalls: Map<string, PendingToolCall>;
  pendingActivityReplaySeqs: Map<string, number>;
  delayedActivityTimers: Set<ReturnType<typeof setTimeout>>;
  activeAssistantMessageId: string | null;
  nextItemRenderAt: number;
}

export interface TranscriptProjectionResult {
  state: TranscriptProjectionState;
}

export interface TranscriptProjectionRuntime {
  getProjectionState: () => TranscriptProjectionState;
  setProjectionState: (state: TranscriptProjectionState) => void;
}

export const TODO_TOOL_NAME = "update_todos";

export type TaskLifecycleEvent = Extract<
  ServerEvent,
  { type: "task_started" | "task_progress" | "task_finished" }
>;

export interface ProjectionContext {
  state: TranscriptProjectionState;
  runtime?: TranscriptProjectionRuntime;
  update: (next: TranscriptProjectionState) => TranscriptProjectionState;
  latest: () => TranscriptProjectionState;
  commit: (next: TranscriptProjectionState) => TranscriptProjectionState;
}
