from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from arden.core.content import ContextContent, ContextManifestEntry
from arden.tools.core.types import ToolOverrideDecision

# --- Chat / run ---


class ImageBlock(BaseModel):
    media_type: str
    data: str


class ChatRequest(BaseModel):
    message: str = Field("", max_length=100_000)
    images: list[ImageBlock] = Field(default_factory=list)
    context: list[ContextContent] = Field(default_factory=list)
    skip_approvals: bool = False
    session_id: str | None = None
    client_id: str | None = None


class ToolResultRequest(BaseModel):
    run_id: str
    tool_id: str
    result: str
    approved: bool = True


class ConnectionResultRequest(BaseModel):
    run_id: str
    tool_id: str
    result: str = ""
    approved: bool = True


class CancelRequest(BaseModel):
    # run_id is preferred; session_id lets the client say "stop whatever is
    # running in this session" when it can't reliably name the run (e.g. a
    # backgrounded / automation run the UI shows as running but never tracked
    # a foreground run_id). The server resolves the session's active run.
    run_id: str | None = None
    session_id: str | None = None


class BackgroundRequest(BaseModel):
    run_id: str


class InjectChildAgentRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10000)


class ChatRunStatusResponse(BaseModel):
    run_id: str
    session_id: str
    status: Literal["pending", "running", "backgrounded", "completed", "cancelled", "error"]
    created_at: str
    updated_at: str
    age_seconds: int
    idle_seconds: int
    message_count: int
    pending_injections: int
    approvals_pending: int
    task_running: bool
    drain_task_running: bool
    cancelled: bool
    backgrounded: bool


class BackgroundTaskSessionStatusResponse(BaseModel):
    session_id: str
    pending_tasks: int


class BackgroundAgentRunResponse(BaseModel):
    task_id: str
    child_run_id: str | None = None
    child_session_id: str | None = None
    session_id: str
    parent_run_id: str | None = None
    parent_tool_call_id: str | None = None
    agent_type: str = "background_research"
    wait: bool = False
    status: Literal[
        "running",
        "activity",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
        "cancel_requested",
    ]
    command: str
    detail: str | None = None
    result_ref: str | None = None
    created_at: str
    started_at: str | None = None
    updated_at: str
    ended_at: str | None = None
    cancel_requested_at: str | None = None
    notified_at: str | None = None


class BackgroundAgentRunsResponse(BaseModel):
    tasks: list[BackgroundAgentRunResponse] = Field(default_factory=list)


class ChildAgentResultResponse(BaseModel):
    task_id: str
    child_run_id: str
    session_id: str
    status: Literal[
        "running",
        "activity",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
        "cancel_requested",
    ]
    terminal: bool
    result: str | None = None
    result_ref: str | None = None


class ChatRunsStatusResponse(BaseModel):
    observed_at: str
    total_retained: int
    active_count: int
    active_runs: list[ChatRunStatusResponse] = Field(default_factory=list)
    background_task_sessions: list[BackgroundTaskSessionStatusResponse] = Field(default_factory=list)


class GoalEvidenceResponse(BaseModel):
    text: str
    created_at: str


class SessionGoalResponse(BaseModel):
    session_id: str
    goal_id: str
    objective: str
    status: Literal["active", "paused", "blocked", "budget_limited", "complete"]
    evidence: list[GoalEvidenceResponse] = Field(default_factory=list)
    blocked_reason: str | None = None
    token_budget: int | None = None
    tokens_used: int = 0
    time_used_seconds: int = 0
    created_at: str
    updated_at: str


class SetSessionGoalRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=100_000)
    token_budget: int | None = Field(default=None, gt=0)


class GoalProposalResponse(BaseModel):
    objective: str


class TodoOverrideItem(BaseModel):
    content: str = Field(min_length=1, max_length=500)
    status: Literal["pending", "in_progress", "completed"]


class SetTodoOverrideRequest(BaseModel):
    items: list[TodoOverrideItem] = Field(default_factory=list, max_length=50)
    explanation: str | None = Field(default=None, max_length=2_000)


class UpdateSessionGoalRequest(BaseModel):
    status: Literal["active", "paused", "blocked", "budget_limited", "complete"] | None = None
    evidence: str | None = Field(default=None, max_length=20_000)
    blocked_reason: str | None = Field(default=None, max_length=20_000)


OutboxEventId = Annotated[int, Field(gt=0)]


class ReplayOutboxRequest(BaseModel):
    event_ids: list[OutboxEventId] = Field(..., min_length=1, max_length=100)


class OutboxHealthResponse(BaseModel):
    worker_running: bool
    pending: int
    ready: int
    running: int
    dead: int


class HealthResponse(BaseModel):
    status: str
    version: str
    has_providers: bool
    outbox: OutboxHealthResponse
    config_version: int
    config_loaded_at: str
    auth: bool | None = None


class OutboxWorkerResponse(BaseModel):
    running: bool
    worker_id: str | None = None


class OutboxStatusCountsResponse(BaseModel):
    pending: int = 0
    running: int = 0
    completed: int = 0
    dead: int = 0


class OutboxDeadEventResponse(BaseModel):
    id: int
    event_type: str
    aggregate_type: str | None = None
    aggregate_id: str | None = None
    attempts: int
    last_error: str | None = None
    created_at: str
    updated_at: str


class OutboxEventsStatusResponse(BaseModel):
    observed_at: str
    total: int
    ready: int
    scheduled: int
    by_status: OutboxStatusCountsResponse
    by_event_type: dict[str, OutboxStatusCountsResponse]
    oldest_pending_created_at: str | None = None
    next_pending_available_at: str | None = None
    oldest_running_locked_at: str | None = None
    newest_dead_updated_at: str | None = None
    recent_dead: list[OutboxDeadEventResponse]


class OutboxStatusResponse(BaseModel):
    status: Literal["running", "stopped", "disabled"]
    worker: OutboxWorkerResponse | None = None
    events: OutboxEventsStatusResponse | None = None


class OutboxReplaySkippedResponse(BaseModel):
    id: int
    status: str


class OutboxReplayResponse(BaseModel):
    status: Literal["queued", "unchanged", "disabled"]
    requested: list[int]
    replayed: list[int]
    missing: list[int]
    skipped: list[OutboxReplaySkippedResponse]


class OutboxPruneResponse(BaseModel):
    status: Literal["deleted", "disabled"]
    deleted: int
    before: str
    limit: int
    older_than_days: int


class SchedulerTaskStatusResponse(BaseModel):
    total: int
    enabled: int
    disabled: int
    running: int
    due: int
    next_run_at: str | None = None
    oldest_running_since: str | None = None


class SchedulerEventQueueStatusResponse(BaseModel):
    total: int
    ready: int
    scheduled: int
    claimed: int
    oldest_pending_created_at: str | None = None
    next_attempt_at: str | None = None
    oldest_claimed_at: str | None = None


class SchedulerCountStateStatusResponse(BaseModel):
    total: int
    oldest_updated_at: str | None = None


class SchedulerStoreStatusResponse(BaseModel):
    observed_at: str
    tasks: SchedulerTaskStatusResponse
    event_queue: SchedulerEventQueueStatusResponse
    count_state: SchedulerCountStateStatusResponse


class SchedulerStatusResponse(BaseModel):
    status: Literal["running", "stopped", "disabled"]
    started_at: str | None = None
    last_tick_at: str | None = None
    last_tick_error: str | None = None
    last_activity_at: str | None = None
    running_tasks: int = 0
    registered_handlers: list[str] = Field(default_factory=list)
    store: SchedulerStoreStatusResponse | None = None


# --- Session / config ---


class SessionResponse(BaseModel):
    session_id: str
    integrations: list[str]
    integration_errors: dict[str, str]
    name: str | None = None
    area_id: str | None = None
    chat_model: str | None = None


class InspectorSourceResponse(BaseModel):
    provider: str
    kind: str
    ref: str
    title: str
    url: str | None = None


class InspectorApprovalResponse(BaseModel):
    tool_call_id: str
    tool_name: str
    status: str
    feedback: str | None = None


class InspectorEffectResponse(BaseModel):
    tool_call_id: str
    operation: str
    target: str
    before_ref: str | None = None
    after_ref: str | None = None


class InspectorReceiptResponse(BaseModel):
    tool_call_id: str
    receipt: str


class InspectorCheckResponse(BaseModel):
    tool_call_id: str
    postcondition: str
    observed: str
    confidence: float | None = None


class InspectorLimitationResponse(BaseModel):
    tool_call_id: str
    status: str
    code: str
    recovery_action: str | None = None


class TurnEvidenceResponse(BaseModel):
    sources: list[InspectorSourceResponse] = Field(default_factory=list)
    approvals: list[InspectorApprovalResponse] = Field(default_factory=list)
    effects: list[InspectorEffectResponse] = Field(default_factory=list)
    receipts: list[InspectorReceiptResponse] = Field(default_factory=list)
    checks: list[InspectorCheckResponse] = Field(default_factory=list)
    limitations: list[InspectorLimitationResponse] = Field(default_factory=list)


class TurnInspectorResponse(BaseModel):
    run_id: str
    session_id: str
    context_manifest: list[ContextManifestEntry] = Field(default_factory=list)
    evidence: TurnEvidenceResponse = Field(default_factory=TurnEvidenceResponse)
    updated_at: str


class AreaResponse(BaseModel):
    area_id: str
    name: str
    default_cwd: str | None = None
    instructions: str | None = None
    knowledge_scope: str
    page_path: str | None = None
    page_id: str | None = None
    autonomy: str | None = None
    attention: str = "ambient"
    interrupts: str = "asks"
    paused_at: str | None = None
    created_at: str
    updated_at: str
    archived_at: str | None = None


class CreateAreaRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    default_cwd: str | None = Field(default=None, max_length=1_000)
    instructions: str | None = Field(default=None, max_length=20_000)
    knowledge_scope: str | None = Field(default=None, max_length=500)
    # Attaching a page grows the area's capabilities (observe agent implied).
    page_path: str | None = Field(default=None, max_length=1_000)


class UpdateAreaRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    default_cwd: str | None = Field(default=None, max_length=1_000)
    instructions: str | None = Field(default=None, max_length=20_000)
    knowledge_scope: str | None = Field(default=None, max_length=500)
    page_path: str | None = Field(default=None, max_length=1_000)
    autonomy: str | None = Field(default=None, max_length=20)
    attention: Literal["dormant", "ambient", "active"] | None = None
    interrupts: Literal["asks", "all", "none"] | None = None
    paused: bool | None = None


AreaWorkKey = Annotated[
    str,
    Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$"),
]


class CreateAreaOutcomeRequest(BaseModel):
    key: AreaWorkKey
    title: str = Field(min_length=1, max_length=300)
    success_criteria: str = Field(min_length=1, max_length=2_000)
    priority: int = Field(ge=1, le=5)


class UpdateAreaOutcomeRequest(BaseModel):
    expected_updated_at: str
    title: str | None = Field(default=None, min_length=1, max_length=300)
    success_criteria: str | None = Field(default=None, min_length=1, max_length=2_000)
    priority: int | None = Field(default=None, ge=1, le=5)
    status: Literal["active", "paused", "completed", "cancelled"] | None = None


class UpdateAreaWorkItemRequest(BaseModel):
    expected_updated_at: str
    text: str | None = Field(default=None, min_length=1, max_length=2_000)
    status: Literal["active", "in_progress", "completed", "cancelled"] | None = None
    owner: Literal["custodian", "user", "external"] | None = None
    due_at: str | None = None
    next_attempt_at: str | None = None


class CreateSessionRequest(BaseModel):
    name: str | None = None
    area_id: str | None = None


class UpdateSessionModelRequest(BaseModel):
    chat_model: str | None = None


class BranchRequest(BaseModel):
    name: str | None = None
    up_to_message_id: str | None = None


class SetSessionAutoRequest(BaseModel):
    value: bool


class RenameSessionRequest(BaseModel):
    name: str


class MoveSessionAreaRequest(BaseModel):
    area_id: str | None = None


class CompactRequest(BaseModel):
    session_id: str | None = None


class ClearSessionRequest(BaseModel):
    session_id: str | None = None


class RevertRequest(BaseModel):
    session_id: str | None = None
    turns: int = Field(1, ge=1)
    # Preferred over `turns`: revert up to (and including) the message with
    # this client_id. Used by edit flows so the server-side context is in
    # sync with the UI before the next /chat/message arrives.
    message_id: str | None = None


class IntegrationToggles(BaseModel):
    gmail: bool | None = None
    calendar: bool | None = None
    google_drive: bool | None = None
    memory: bool | None = None


class RoleSetupPatch(BaseModel):
    """Partial update for one role. Omitted keys keep their stored value; an
    explicit null clears one. A new per-role knob is a field here and nothing
    else — that is the point of the shape."""

    model: str | None = None
    reasoning_effort: str | None = None

    model_config = {"extra": "forbid"}


class UpdateConfigRequest(BaseModel):
    chat_model: str | None = None
    model_roles: dict[str, RoleSetupPatch] | None = None
    max_depth: int | None = Field(default=None, ge=1, le=16)
    reasoning_model: str | None = None
    reasoning_effort: str | None = None
    compression_threshold: float | None = Field(default=None, ge=0.1, le=1)
    max_messages: int | None = Field(default=None, ge=10, le=1000)
    compression_keep_ratio: float | None = Field(default=None, ge=0, le=1)
    summary_max_tokens: int | None = Field(default=None, ge=256, le=8000)
    consolidation_interval: int | None = Field(default=None, ge=1, le=500)
    web_search: Literal["auto", "exa", "ddgs", "none"] | None = None
    tool_overrides: dict[str, ToolOverrideDecision] | None = None
    integrations: IntegrationToggles | None = None


class UpdateEmbeddingRequest(BaseModel):
    embedding_model: str | None
    confirmed: bool = False


class UpdateDirectivesRequest(BaseModel):
    content: str


# --- Automations / notifiers ---


class CreateAutomationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    description: str | None = Field(default=None, min_length=1, max_length=220)
    model: str | None = None
    trigger_type: str | None = None
    at: str | None = None
    days: str | None = None
    every: str | None = None
    event_type: str | None = None
    lead_minutes: int | str | None = None
    idle_minutes: int | None = None
    every_n: int | None = None
    auto_approve: bool = False
    start: str | None = None
    end: str | None = None
    triggers: list[dict] | None = None
    cooldown_minutes: int | None = None
    tool_scope: list[str] | None = None
    idempotency_key: str = Field(min_length=1, max_length=200)
    idempotency_scope: Literal["global"] = "global"


class UpdateAutomationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    prompt: str | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, min_length=1, max_length=220)
    model: str | None = None
    trigger_type: str | None = None
    at: str | None = None
    days: str | None = None
    every: str | None = None
    event_type: str | None = None
    lead_minutes: int | str | None = None
    idle_minutes: int | None = None
    every_n: int | None = None
    start: str | None = None
    end: str | None = None
    auto_approve: bool | None = None
    enabled: bool | None = None
    triggers: list[dict] | None = None
    cooldown_minutes: int | None = None
    tool_scope: list[str] | None = None


class CreateLoopRequest(BaseModel):
    session_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    every: str = Field(min_length=1)
    max_iterations: int | None = None
    stop_when: str | None = None
    max_age_days: int | None = None


class UpdateLoopRequest(BaseModel):
    prompt: str | None = None
    every: str | None = None
    enabled: bool | None = None
    max_iterations: int | None = None
    stop_when: str | None = None
    max_age_days: int | None = None


class CreateNotifierRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    type: str
    config: dict


class UpdateNotifierRequest(BaseModel):
    config: dict
    name: str | None = None


# --- Skills ---


class ConnectProviderRequest(BaseModel):
    api_key: str = Field(..., min_length=1)
    chat_model: str | None = None


class ConnectServiceRequest(BaseModel):
    api_key: str = Field(..., min_length=1)


class AddCustomModelRequest(BaseModel):
    model_id: str = Field(..., min_length=1)
    base_url: str = Field(..., min_length=1)
    context_window: int = Field(..., gt=0, le=2_000_000)
    max_output_tokens: int = Field(default=8192, gt=0)
    api_key: str | None = None

    @model_validator(mode="after")
    def validate_token_limits(self):
        if self.max_output_tokens > self.context_window:
            raise ValueError("max_output_tokens must not exceed context_window")
        return self


# --- Skills ---


class InstallRequest(BaseModel):
    source: str = Field(..., min_length=5, description="GitHub path: owner/repo/path/to/skill")
