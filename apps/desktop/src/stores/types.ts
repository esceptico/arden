import type { AppConfig } from "@/api/core";
import type { ConnectionAction, ConnectionState, ToolOutcome } from "@/api/events";
import type { ArchivedSession } from "@/api/sessions";
import type { Automation, ModelsResponse, Area, ServerConfig, SessionGoal, SessionListItem, SkillDescriptor, TodoListItem } from "@/api/types";
import type { TransportDiagnosticsSnapshot } from "@/lib/transportDiagnostics";
import type { MessageSourceFocus } from "@/lib/messageSourceFocus";
import type { Toast } from "@/lib/taskToast";
import type { AutomationStreamDomainState } from "@/stores/automation-domain";
import type { BackgroundAgentsDomainState } from "@/stores/background-agent-domain";
import type {
  WorkflowsDomainState,
  WorkflowStartedInput,
  WorkflowTokenUsageInput,
} from "@/stores/workflow-domain";
import type { SessionViewState } from "@/stores/session-view";
import type { AreasDomainState } from "@/stores/areas-domain";
import type { ShellLayout } from "@/lib/shellOwnership";

export type { SessionViewState } from "@/stores/session-view";

export interface MemoryVaultChange {
  paths: string[];
  revision: string | null;
  seq: number | null;
}

export interface SequencedMemoryVaultChange extends MemoryVaultChange {
  seq: number;
}

export type SettingsTabId =
  | "connection"
  | "providers"
  | "integrations"
  | "notifiers"
  | "models"
  | "agent"
  | "context"
  | "tools"
  | "mcp"
  | "appearance"
  | "storage"
  | "archive";

export type Role =
  | "user"
  | "assistant"
  | "reasoning"
  | "tool"
  | "status"
  | "error"
  | "activity"
  | "approval"
  | "todo";

export interface PendingGoalProposal {
  sessionId: string;
  objective: string;
}

export type ThemeChoice = "light" | "dark" | "system";
/** Global border-radius language: capsule controls, or softened rectangles. */
export type CornerProfile = "round" | "square";
/** Persisted visual-strength IDs for the composer's working strip. Keep this
 * runtime registry as the authority for both saved preferences and the
 * Appearance control; a type alone cannot reject stale localStorage. */
export const THINKING_INTENSITY_IDS = ["subtle", "normal", "strong"] as const;
export type ThinkingIntensity = (typeof THINKING_INTENSITY_IDS)[number];

export function isThinkingIntensity(value: unknown): value is ThinkingIntensity {
  return typeof value === "string" && (THINKING_INTENSITY_IDS as readonly string[]).includes(value);
}

export type SidebarGroupBy = "area" | "time" | "type" | "status";

export interface Prefs {
  theme: ThemeChoice;
  /** Selected accent palette id (see `@/lib/palettes`). */
  accent: string;
  /** Round (capsule) or square (softened rectangle) control/shell radius. */
  cornerProfile: CornerProfile;
  /** Visual strength of the composer's working strip. */
  thinkingIntensity: ThinkingIntensity;
  /** How the sidebar session list is grouped. */
  sidebarGroupBy: SidebarGroupBy;
  /** Sidebar filter: show only unread (finished, unseen) sessions. */
  sidebarUnreadOnly: boolean;
  /** Sidebar filter: show only channel sessions. */
  sidebarChannelsOnly: boolean;
  /** Session IDs pinned to the top of the sidebar, most-recent-pin first. */
  pinnedSessionIds: string[];
  /** Newest-seen item timestamp per Home activity lane — the unread cursor
   *  behind the strips' accent "N new" counts. */
  ambientSeen: Record<string, string>;
  /** `${sessionId}:${workflowId}` keys hidden from the sidebar hub (capped FIFO). */
  dismissedWorkflows: string[];
  sidebarHidden: boolean;
  /** Right panel (agents/todos/automations) collapsed. Shared so the chat
   *  area can reflow its right edge to dock the panel instead of floating
   *  over content. */
  rightPanelCollapsed: boolean;
  /** Chat inspector presentation. Area rooms always use the docked hub. */
  rightPanelDocked: boolean;
  /** Area-room Agent Hub visibility, independent from Chat's inspector. */
  areaHubCollapsed: boolean;
  /** Sidebar width in pixels. User-resizable via the right-edge drag
   *  handle. Clamped to [SIDEBAR_MIN_WIDTH, SIDEBAR_MAX_WIDTH] in the
   *  resize handler. Default matches the historic fixed width. */
  sidebarWidth: number;
  /** Right panel dock width in pixels. Includes the panel body plus its
   *  outer inset/gutter so the chat can dock flush against it. */
  rightPanelWidth: number;
  /** Electron accelerator string for the global quick-capture window
   *  shortcut, e.g. "CommandOrControl+Shift+Space". Pushed to the main
   *  process via IPC; main re-registers on change. Empty string disables
   *  the shortcut entirely. */
  quickCaptureShortcut: string;
  /** Custom interface font-family stack. Empty string = default Geist
   *  stack. Code keeps its mono stack; only the size follows. */
  uiFont: string;
  /** Base px size the whole type scale derives from — code blocks and
   *  diffs follow 1px smaller via --code-font-size in base.css. */
  uiFontSize: number;
  /** True = -webkit-font-smoothing: antialiased (default). False = auto,
   *  the platform's native subpixel rendering. */
  fontSmoothing: boolean;
  /** True = reasoning messages render as collapsible blocks in the
   *  transcript. Activity grouping is unaffected — reasoning stays an
   *  activity continuation either way. */
  showReasoning: boolean;
}

/** A desktop-owned fresh turn waiting behind the active run. */
export type QueuedMessageStatus = "pending" | "sending" | "failed";

export interface QueuedMessage {
  clientId: string;
  text: string;
  images?: ImageBlock[];
  meta?: boolean;
  status: QueuedMessageStatus;
  enqueuedAt: number;
}

export interface ServerLoop {
  task_id: string;
  session_id: string;
  prompt: string;
  every: string;
  enabled: boolean;
  iteration_count: number;
  max_iterations: number | null;
  stop_when: string | null;
  max_age_days: number | null;
  created_at: string;
  next_run_at: string | null;
  last_run_at: string | null;
  last_result: string | null;
  running_since: string | null;
}

export type BackgroundAgentStatus =
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted"
  | "cancel_requested";

export interface BackgroundAgent {
  taskId: string;
  /** Public model-facing identity; taskId remains the internal control key. */
  agentRef?: string;
  sessionId: string;
  childSessionId?: string;
  command: string;
  status: BackgroundAgentStatus;
  detail?: string;
  resultRef?: string;
  parentToolCallId?: string;
  agentType?: string;
  wait?: boolean;
  createdAt: number;
  updatedAt: number;
}

export interface ChildAgentRef {
  /** Public identity persisted in model history. */
  agentRef?: string;
  sessionRef?: string;
  /** Internal live-control identities supplied only by lifecycle events. */
  childRunId?: string;
  childSessionId?: string;
  parentToolCallId?: string;
  agentType: string;
  wait: boolean;
  status: string;
}

export interface SourceRef {
  provider: string;
  kind: string;
  ref: string;
  title: string;
  url?: string;
  toolCallId?: string;
}

export interface ActivityItem {
  id: string;
  /** Tool name (used for display + inspector lookup). Despite the name this
   *  is the tool's identifier — see `semanticKind` for "tool vs agent". */
  kind: string;
  /** Semantic kind from the server: "tool" (default) or "agent" for tools
   *  that internally spawn a sub-agent. The renderer picks a different row
   *  surface for agents. */
  semanticKind?: string;
  displayName?: string;
  /** Model-authored per-call label projected from the canonical
   * `_display_title` metadata argument. Never inferred from behavior args. */
  displayTitle?: string;
  // Additive UI rendering hints from the backend (tool_presentation): a
  // semantic icon key, a grouping noun, and the integration source/category.
  // Absent on history reload / uncategorized tools — the trace falls back to
  // its client-side heuristic.
  icon?: string;
  noun?: string;
  source?: string;
  target: string;
  args?: string;
  result?: string;
  status?: "ongoing" | "executed" | "backgrounded";
  /** Original TOOL_CALL_START timestamp, used by live elapsed labels. */
  startedAt?: number;
  cancelRequested?: boolean;
  runId?: string;
  /** Nesting depth: 0 = top-level (called by the user-facing agent),
   *  1 = inside a sub-agent (research → research, etc.). Used purely for
   *  visualization (indent + chip). */
  depth?: number;
  /** Tool-call id of the parent tool whose run produced this call.
   *  Available for nested calls; lets the inspector group children. */
  parentToolId?: string;
  /** Server-reported error flag (set on TOOL_CALL_RESULT). Lets the
   *  trace render error rows distinctly without parsing the result text. */
  error?: boolean;
  /** Wall-clock duration of the tool call in milliseconds. Set on
   *  TOOL_CALL_RESULT — undefined while running. */
  durationMs?: number;
  /** Structured terminal execution result from the server. */
  outcome?: ToolOutcome;
  taskStatus?: "running" | "completed" | "failed" | "cancelled" | "interrupted";
  progress?: string;
  /** Subagent token usage (only populated when `semanticKind === "agent"`).
   *  Reports the spawned agent's INTERNAL spend — these tokens never enter
   *  the parent's context. Used by the activity-trace agent row to surface
   *  per-agent context + cost without polluting the parent's budget gauge. */
  usage?: {
    prompt: number;
    completion: number;
    total: number;
    cache_read?: number;
    cache_write?: number;
  };
  /** Subagent USD cost (only populated when `semanticKind === "agent"`).
   *  Already rolled up into the parent run's `totalCost` server-side. */
  cost?: number;
  /** Durable child-agent identity/control metadata from tool result data. */
  childAgent?: ChildAgentRef;
  /** Normalized resources returned by this tool call. */
  sourceRefs?: SourceRef[];
  /** Real workflow id (from the workflow tool's result data) when
   *  `semanticKind === "workflow"`. Lets the lifted card open the panel even
   *  after reload, before any live workflow-domain event repopulates it. */
  workflowId?: string;
  /** HTML widget payload for `semanticKind === "html_widget"` (render_html
   *  tool). Set live from `input_needed` / TOOL_CALL_RESULT data, and from
   *  the tool-call args on history rebuild (result data is not persisted). */
  htmlWidget?: { html: string; title: string; mode: "display" | "input" };
}

export type ActivityLabel = "Calling" | "Called" | "Backgrounded" | "Stopped";

export interface ActivityState {
  items: ActivityItem[];
  label: ActivityLabel;
  done: boolean;
  backgrounded?: boolean;
}

export type ApprovalStatus = "pending" | "approved" | "rejected";

export interface ApprovalState {
  toolId: string;
  toolName: string;
  path?: string;
  diff?: string;
  preview?: string;
  status: ApprovalStatus;
  runId?: string;
  sessionId?: string;
  agentType?: string;
  agentName?: string;
  action?: string;
  expiresAt?: string;
}

export interface PendingConnection {
  runId: string;
  toolId: string;
  integrationId: string;
  connectionId: string;
  label: string;
  reason: ConnectionState;
  detail: string;
  capability: string;
  action: ConnectionAction;
  settingsTab: string;
  requiredScopes: string[];
  source: "recovery" | "suggestion";
  accountRef?: string;
}

export interface TodoListState {
  items: TodoListItem[];
  explanation?: string | null;
}

/** The session's effective todo list (override-aware), mirrored from the
 *  server's session-level slot — NOT derived from the transcript. Absent
 *  once the list is retired (all completed) or dismissed. */
export interface SessionTodo extends TodoListState {
  edited: boolean;
}

export interface TurnMeta {
  startedAt: number;
  endedAt: number | null;
  durationMs: number | null;
}

export interface ImageBlock {
  /** IANA media type, e.g. "image/png". */
  media_type: string;
  /** Base64-encoded image bytes (no data: URL prefix). */
  data: string;
}

export interface UiMessage {
  id: string;
  role: Role;
  sourceIndex?: number;
  sourceMessageId?: string;
  /** Hydrated/replayed rows should render directly, without entry CSS motion. */
  suppressEntryMotion?: boolean;
  title?: string;
  subtitle?: string;
  content: string;
  activity?: ActivityState;
  approval?: ApprovalState;
  todo?: TodoListState;
  turn?: TurnMeta;
  images?: ImageBlock[];
  /** True for system-generated user messages that should be hidden from
   *  the transcript UI but kept in conversation history for the model
   *  (e.g. loop tick prompts). Mirrors Claude Code's isMeta convention. */
  isMeta?: boolean;
}

export interface SessionContextBudget {
  model: string;
  hardLimit: number;
  compactionTrigger: number;
  messageLimit: number;
}

export interface SessionUsage {
  /** Provider-reported input tokens for the current model-visible context. */
  contextInputTokens: number;
  observedPromptTokens: number;
  observedCompletionTokens: number;
  observedCacheReadTokens: number;
  observedCacheWriteTokens: number;
  totalTokens: number;
  totalCost: number;
  /** Server-side active-context count after the latest run. Legacy sessions
   *  remain unknown rather than using their larger immutable transcript. */
  messageCount: number | null;
  /** Model-specific limits captured with this session's history. */
  contextBudget: SessionContextBudget | null;
}

export interface MarkdownViewState {
  title: string;
  subtitle?: string;
  content: string;
}

/** Which workflow is expanded/focused in the agent hub (right sidebar). Set by
 *  clicking a workflow card (in the hub or the chat trace); the session id is
 *  captured so it resolves even after switching sessions. */
export interface WorkflowViewerState {
  workflowId: string;
  sessionId: string;
}

/** Per-session view state cached across `setCurrentSession` switches.
 *  Snapshotted on switch-out, hydrated on switch-back, so flipping
 *  between sessions doesn't blank the UI while history reloads. The
 *  SSE replay (with the bus's checkpoint watermark) catches up any
 *  events that landed while the session was in the background. */
export interface CachedSessionState {
  sessionView: SessionViewState;
  messages: Map<string, UiMessage>;
  order: string[];
  running: boolean;
  currentRunId: string | null;
  thinkingRunId: string | null;
  thinkingStatus: string | null;
  usage: SessionUsage;
  editingId: string | null;
  activeActivityId: string | null;
  compacting: boolean;
  sourceFocus: MessageSourceFocus | null;
  pendingApprovals: ApprovalState[];
  pendingConnections: PendingConnection[];
  reviewingApprovalToolId: string | null;
  queuedMessages: QueuedMessage[];
  pendingResume: { runId: string | null; sessionId: string } | null;
  stoppingRunId: string | null;
}

export interface State {
  config: AppConfig;
  sessions: SessionListItem[];
  sessionView: SessionViewState;
  currentSessionId: string | null;
  /** Area intent for an unpersisted Home draft. */
  pendingNewChatAreaId: string | null;
  /** Monotonic identity prevents in-flight creation from crossing New Chat intents. */
  pendingNewChatDraftId: number;
  messages: Map<string, UiMessage>;
  order: string[];
  /** Session ids with a foreground run that should drive composer UI. */
  activeRunSessionIds: Set<string>;
  /** Session ids with a live backgrounded drain. These are still live,
   *  but must not drive composer/thinking/tool-counter state. */
  backgroundedRunSessionIds: Set<string>;
  /** Sessions whose runs finished while the user wasn't looking at
   *  them. Cleared when the user opens the session. Renders as an
   *  "unread" dot in the sidebar. */
  unreadDoneSessionIds: Set<string>;
  /** Per-session UI state preserved across `setCurrentSession` swaps. */
  sessionCache: Map<string, CachedSessionState>;
  connected: boolean;
  serverWarmup: import("@/api/core").WarmupHealth | null;
  running: boolean;
  error: string | null;
  draft: string;
  settingsOpen: boolean;
  settingsTab: SettingsTabId | null;
  connectionDraft: AppConfig;
  connectionError: string | null;
  connectionSaving: boolean;
  usage: SessionUsage;
  editingId: string | null;
  activeActivityId: string | null;
  currentRunId: string | null;
  thinkingRunId: string | null;
  thinkingStatus: string | null;
  skipApprovals: boolean;
  skills: SkillDescriptor[];
  commandPickerOpen: boolean;
  commandPickerIndex: number;
  viewingMarkdown: MarkdownViewState | null;
  viewingTool: ActivityItem | null;
  pendingImages: ImageBlock[];
  serverConfig: ServerConfig | null;
  serverModels: ModelsResponse | null;
  serverConfigError: string | null;
  automations: Automation[] | null;
  automationsOpen: boolean;
  automationTargetId: string | null;
  /** Deep-link intent: open the target automation's latest run result. */
  automationTargetRun: "latest" | null;
  automationStream: AutomationStreamDomainState;
  archivedSessions: ArchivedSession[] | null;
  compacting: boolean;
  memoryOpen: boolean;
  /** Page path Memory should land on when it opens, instead of its usual
   *  restored/README fallback. Cleared once Memory has honoured it. */
  memoryTargetPath: string | null;
  /** Optional heading within the target page — a chat citation's `#anchor`.
   *  Rides with memoryTargetPath and is cleared with it. */
  memoryTargetAnchor: string | null;
  /** The wiki page Memory is showing right now, published so the shell can
   *  put it in the route trail. Memory owns the value; nothing else sets it. */
  memoryCurrentPath: string | null;
  /** The automation the workspace is showing right now, published so the shell
   *  can put it in the route trail. Distinct from automationTargetId, which is
   *  a one-shot deep-link inbox that clears itself once honoured. */
  automationCurrentId: string | null;
  sourceFocus: MessageSourceFocus | null;
  rightInspectorTab: "activity" | "sources";
  sourceTurnId: string | null;
  /** Monotonic invalidation key for per-turn source derivation. */
  sourceRefsRevision: number;
  paletteOpen: boolean;
  /** Tool approvals waiting on the user. Lives outside `messages` so the
   *  approval UI can render as its own surface (sticky banner above the
   *  composer) without interleaving with the agent's narrative trace. */
  pendingApprovals: ApprovalState[];
  pendingConnections: PendingConnection[];
  /** When non-null, the approval UI is showing a diff/preview modal for
   *  this approval's `toolId`. */
  reviewingApprovalToolId: string | null;
  /** Messages submitted while a run was in flight. */
  queuedMessages: QueuedMessage[];
  /** Run resume requested by the UI but not yet reflected by stream state. */
  pendingResume: { runId: string | null; sessionId: string } | null;
  /** Active run currently being stopped by the user. */
  stoppingRunId: string | null;
  /** Terminal run ids seen locally. Prevents stale status polls from
   *  re-adding a run that the live stream already finished. */
  terminalRunIds: Set<string>;
  transportDiagnostics: Record<string, TransportDiagnosticsSnapshot>;
  streamReplaying: boolean;
  /** Center point of the element that triggered the currently-open modal.
   *  Null when the modal opens via keyboard / palette / non-positional path. */
  modalOrigin: { x: number; y: number } | null;
  loops: ServerLoop[];
  backgroundAgents: BackgroundAgentsDomainState;
  /** Fetched one-line result previews, keyed `${sessionId}:${taskId}`.
   *  Shared cache: the sidebar hub and the chat trace read the same entry so a
   *  detached agent's result reads identically on both surfaces. */
  childAgentResultSnippets: Record<string, string>;
  workflows: WorkflowsDomainState;
  /** The workflow expanded/focused in the agent hub. Null when none. */
  workflowViewer: WorkflowViewerState | null;
  goals: Record<string, SessionGoal>;
  sessionTodos: Record<string, SessionTodo>;
  pendingGoalProposal: PendingGoalProposal | null;
  toasts: Toast[];
  prefs: Prefs;
  /** Runtime viewport/workspace facts for shell-only arbitration. */
  shellLayout: ShellLayout;
  areas: AreasDomainState;
  triage: import("@/stores/triage-domain").TriageDomainState;
}

export interface Actions {
  setConfig: (config: AppConfig) => void;
  setAreaRecords: (areas: Area[]) => void;
  upsertAreaRecord: (area: Area) => void;
  archiveAreaRecord: (areaId: string) => void;
  setSessions: (sessions: SessionListItem[]) => void;
  prependSession: (session: SessionListItem) => void;
  patchSession: (session: SessionListItem) => void;
  syncActiveRuns: (
    runs: { runId?: string | null; sessionId: string; status?: string | null; backgrounded?: boolean }[],
  ) => void;
  markRunStarted: (runId: string | null, sessionId: string) => void;
  markRunCompleted: (runId: string | null, sessionId?: string | null) => void;
  clearForegroundRun: (
    runId: string | null,
    sessionId?: string | null,
    options?: { clearApprovals?: boolean; markBackgrounded?: boolean },
  ) => void;
  setCurrentSession: (sessionId: string | null) => void;
  beginNewChatDraft: (areaId: string | null) => void;
  setHistory: (messages: UiMessage[], page?: import("@/api/chat").HistoryPage) => void;
  prependHistory: (messages: UiMessage[], page?: import("@/api/chat").HistoryPage) => void;
  appendHistoryPage: (
    messages: UiMessage[],
    page?: import("@/api/chat").HistoryPage,
    activeActivityId?: string | null,
  ) => void;
  setHistoryLoading: (direction: "before" | "after", loading: boolean) => void;
  appendMessage: (message: UiMessage) => void;
  insertMessageBefore: (message: UiMessage, beforeId: string | null) => void;
  mutateMessage: (id: string, patch: Partial<UiMessage>) => void;
  upsertTodoList: (message: UiMessage, beforeId?: string | null) => void;
  truncateFrom: (id: string) => void;
  setConnected: (connected: boolean) => void;
  setServerWarmup: (warmup: import("@/api/core").WarmupHealth | null) => void;
  setError: (error: string | null) => void;
  setDraft: (draft: string) => void;
  setEditingId: (id: string | null) => void;
  resetUsage: () => void;
  accumulateUsage: (usage: {
    prompt: number;
    completion: number;
    total?: number;
    cache_read?: number;
    cache_write?: number;
    cost: number;
    exclusive_cost?: number;
    contextInputTokens?: number | null;
    messageCount?: number;
  }) => void;
  updateLiveUsage: (usage: {
    prompt: number;
    completion: number;
    total?: number;
    cache_read?: number;
    cache_write?: number;
    cost?: number;
    contextInputTokens?: number | null;
    messageCount?: number;
    scope?: "run" | "tool";
  }) => void;
  /** Replace the budget-relevant fields without touching cumulative spend.
   *  Used when loading a session's persisted state — last prompt size and
   *  message count come from disk; cumulative cost/tokens start fresh
   *  for the session view (server doesn't persist running totals). */
  hydrateUsageSnapshot: (snapshot: {
    contextInputTokens: number;
    messageCount: number | null;
    contextBudget?: SessionContextBudget | null;
  }) => void;
  openSettings: (tab?: SettingsTabId) => void;
  closeSettings: () => void;
  setConnectionDraft: (patch: Partial<AppConfig>) => void;
  setConnectionError: (error: string | null) => void;
  setConnectionSaving: (saving: boolean) => void;
  setActiveActivityId: (id: string | null) => void;
  appendActivityItem: (activityId: string, item: ActivityItem) => void;
  mergeActivityItem: (itemId: string, patch: Partial<ActivityItem>) => boolean;
  finalizeActivity: (activityId: string, label?: ActivityLabel) => void;
  setSkipApprovals: (skip: boolean) => void;
  setApprovalStatus: (id: string, status: ApprovalStatus) => void;
  addPendingApproval: (approval: ApprovalState) => void;
  resolvePendingApproval: (toolId: string) => void;
  addPendingConnection: (connection: PendingConnection) => void;
  resolvePendingConnection: (toolId: string) => void;
  setReviewingApproval: (
    toolId: string | null,
    origin?: { x: number; y: number } | null,
  ) => void;
  addQueuedMessage: (message: QueuedMessage) => void;
  setQueuedMessageStatus: (clientId: string, status: QueuedMessageStatus) => void;
  removeQueuedMessage: (clientId: string) => void;
  clearQueuedMessages: () => void;
  resetCancellingQueuedMessages: () => void;
  setLoops: (loops: ServerLoop[]) => void;
  setBackgroundAgentsForSession: (
    sessionId: string,
    agents: {
      taskId: string;
      agentRef: string;
      childSessionId?: string;
      command: string;
      status?: BackgroundAgentStatus | string | null;
      detail?: string;
      resultRef?: string;
      parentToolCallId?: string;
      agentType?: string;
      wait?: boolean;
      createdAt: number;
    }[],
  ) => void;
  upsertBackgroundAgent: (
    agent: Omit<BackgroundAgent, "createdAt"> & { createdAt?: number },
  ) => void;
  setChildAgentResultSnippet: (key: string, snippet: string) => void;
  setSkills: (skills: SkillDescriptor[]) => void;
  setCommandPickerOpen: (open: boolean) => void;
  setCommandPickerIndex: (index: number) => void;
  setViewingMarkdown: (view: MarkdownViewState | null) => void;
  setViewingTool: (item: ActivityItem | null) => void;
  setViewingWorkflow: (view: WorkflowViewerState | null) => void;
  addPendingImages: (images: ImageBlock[]) => void;
  removePendingImage: (index: number) => void;
  clearPendingImages: () => void;
  setServerConfig: (cfg: ServerConfig | null) => void;
  setServerModels: (models: ModelsResponse | null) => void;
  setServerConfigError: (error: string | null) => void;
  setAutomations: (automations: Automation[] | null) => void;
  openAutomations: (taskId?: string | null, options?: { run: "latest" }) => void;
  closeAutomations: () => void;
  clearAutomationTarget: () => void;
  automationStreamConnecting: () => void;
  automationStreamConnected: () => void;
  automationStreamStale: () => void;
  automationStreamFailed: (error: string) => void;
  automationStreamIdle: () => void;
  automationStreamReset: () => void;
  automationProgress: (taskId: string, status: string) => void;
  automationFinished: (taskId: string) => void;
  /** Monotonic counter bumped when the server's live memory vault absorbs
   *  on-disk changes (Obsidian edit, feed write, maintenance pass) — the
   *  memory view refetches silently when it moves. An omitted change is a
   *  coarse reset and clears retained sequences. */
  memoryVaultVersion: number;
  memoryVaultChanges: readonly SequencedMemoryVaultChange[];
  memoryVaultChanged: (change?: MemoryVaultChange) => void;
  pushToast: (toast: Toast) => void;
  dismissToast: (id: string) => void;
  backgroundAgentsRefreshStarted: () => void;
  backgroundAgentsRefreshFailed: (error: string) => void;
  workflowStarted: (input: WorkflowStartedInput, at?: number) => void;
  workflowFinished: (
    input: {
      workflowId: string;
      sessionId: string;
      status: "completed" | "failed" | "cancelled";
      summary?: string;
      agentCount?: number;
    },
    at?: number,
  ) => void;
  workflowTaskEvent: (
    input: {
      kind: "started" | "progress" | "finished";
      workflowId: string;
      sessionId: string;
      taskId: string;
      phase?: string | null;
      name?: string;
      agentType?: string;
      childSessionId?: string;
      toolCount?: number;
      detail?: string;
      status?: BackgroundAgentStatus;
    },
    at?: number,
  ) => void;
  workflowTokenUsage: (input: WorkflowTokenUsageInput, at?: number) => void;
  dismissWorkflow: (sessionId: string, workflowId: string) => void;
  setGoal: (sessionId: string, goal: SessionGoal | null) => void;
  setSessionTodo: (sessionId: string, todo: SessionTodo | null) => void;
  setPendingGoalProposal: (proposal: PendingGoalProposal | null) => void;
  setArchivedSessions: (sessions: ArchivedSession[] | null) => void;
  setCompacting: (compacting: boolean) => void;
  openMemory: (targetPath?: string, anchor?: string) => void;
  clearMemoryTarget: () => void;
  setMemoryCurrentPath: (path: string | null) => void;
  setAutomationCurrentId: (taskId: string | null) => void;
  closeMemory: () => void;
  setSourceFocus: (focus: MessageSourceFocus | null) => void;
  setRightInspectorTab: (tab: "activity" | "sources") => void;
  openSourcesForTurn: (turnId: string) => void;
  openPalette: () => void;
  closePalette: () => void;
  togglePalette: () => void;
  setPref: <K extends keyof Prefs>(key: K, value: Prefs[K]) => void;
  setShellLayout: (layout: ShellLayout) => void;
  toggleSidebar: () => void;
  areasOverviewLoaded: (overview: import("@/api/areas").AreasOverview) => void;
  setAreasOverviewPhase: (phase: import("@/stores/areas-domain").AreasLoadPhase) => void;
  areaDetailLoaded: (detail: import("@/api/areas").AreaDetail) => void;
  setAreaDetailPhase: (
    key: string,
    phase: import("@/stores/areas-domain").AreasLoadPhase,
  ) => void;
  areaAskResolved: (askId: string) => void;
  openArea: (key: string | null) => void;
  markTriageSeen: (sessionId: string) => void;
  setTriageProposal: (sessionId: string, decision: import("@/api/sessions").TriageDecision) => void;
  clearTriageProposal: (sessionId: string) => void;
}
