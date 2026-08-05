import os
from datetime import timedelta
from pathlib import Path

# Every runtime artefact lives here: settings, databases, wiki, logs. Defined in
# this leaf module (not settings.py) because arden.logging needs it for its file
# sink and settings.py imports arden.logging — importing the other way would cycle.
ARDEN_DIR = Path(os.environ.get("ARDEN_DIR", str(Path.home() / ".arden"))).expanduser()

# Integrations Arden implements itself are `_`-prefixed; the rest are outside
# services. Tool scopes select on this (`tools.system`, `tools.integrations`),
# so the convention is decoded here rather than at each reader.
SYSTEM_SOURCE_PREFIX = "_"


def is_external_source(source: str) -> bool:
    return not source.startswith(SYSTEM_SOURCE_PREFIX)


# --- Content Truncation Limits ---

EMBEDDING_TEXT_LIMIT = 8000  # for embedder input only — see embedder.py


# --- Agent Limits ---

AGENT_MAX_DEPTH = 8
# Max concurrent background agents per session — a horizontal fan-out guard
# (depth is the vertical one) so a runaway loop can't spawn unbounded agents.
AGENT_MAX_CONCURRENT = 16
# Finite backstop so a single agent can never loop forever. This is PER-AGENT
# (each spawned child gets its own 200-step budget), matching reference harnesses'
# per-agent turn limits (Claude Code maxTurns, Codex). Fan-out across a spawn tree
# is bounded separately by AGENT_MAX_DEPTH + SUBAGENT_DEFAULT_TIMEOUT (wall-time
# per child) and, when set, the shared-subtree AGENT_MAX_OUTPUT_TOKENS. 200 steps
# in one agent is already pathological, so this never trips legitimate work.
AGENT_MAX_ITERATIONS = 200
AGENT_MAX_TOOL_CALLS = None
AGENT_MAX_TOOL_CALLS_PER_STEP = 10
AGENT_MAX_CONCURRENT_TOOLS = 6
AGENT_MAX_NO_PROGRESS_STEPS = 3
AGENT_MAX_WALL_TIME_SECONDS = None
AGENT_MAX_COST = None
# Hard ceiling on cumulative output (completion) tokens for a run subtree.
# Per-turn "+1m" style directives can override this, but the default must be
# finite so unattended agent trees cannot drift into multi-million-token runs.
AGENT_MAX_OUTPUT_TOKENS = 500_000

# Hard cap on render_html widget payloads (schema-enforced, no truncation logic).
RENDER_HTML_MAX_CHARS = 150_000

# Per-subagent wall-time guard. A spawned agent that hangs or loops (e.g. emitting
# many tool calls per LLM step, which the per-step iteration cap doesn't catch) is
# bounded here regardless. 30 min is generous for deep research while still
# killing a stuck child; tools can override via the spawn `timeout` param.
SUBAGENT_DEFAULT_TIMEOUT = 7200
# Boot-time respawns per interrupted detached agent. Each restart re-buys the
# same LLM work, so crash-looping servers must converge: after this many
# attempts the row stays interrupted.
BACKGROUND_AGENT_MAX_SPAWN_ATTEMPTS = 2
COMPACTION_TIMEOUT = 600
DEFAULT_EXTERNAL_TOOL_TIMEOUT_SECONDS = 120


# --- Text Processing ---

WEB_SEARCH_MAX_RESULTS = 20


# --- Conversation ---

CONVERSATION_GAP_THRESHOLD = 1800  # seconds (30 min) - show time gap note if exceeded


# --- Outbox ---

OUTBOX_BATCH_SIZE = 20
OUTBOX_MAX_RETRIES = 5
OUTBOX_POLL_INTERVAL = 1.0
OUTBOX_RETRY_BASE_SECONDS = 5
OUTBOX_RETRY_MAX_SECONDS = 300
OUTBOX_STALE_LOCK_SECONDS = 300
OUTBOX_COMPLETED_RETENTION_DAYS = 1
OUTBOX_PRUNE_BATCH_SIZE = 1000
OUTBOX_PRUNE_INTERVAL_SECONDS = 3600


# --- Session ---

HISTORY_MESSAGE_LIMIT = 50  # max user/assistant messages returned for UI history

# Durable session_events retention. Token deltas are never persisted (see
# EPHEMERAL_EVENT_TYPES); the remaining structural events per session are
# capped to the newest N rows — the SQLite equivalent of Redis XADD MAXLEN~,
# sized to mirror the in-memory replay buffer (RECENT_BUFFER_MAX). Trimming the
# oldest is inherently safe for an active run (its tail is the newest rows).
SESSION_EVENT_DURABLE_RETENTION = 10000
SESSION_EVENT_PRUNE_INTERVAL = 500  # prune a session after this many durable writes

# --- Context Compaction ---

COMPRESSION_THRESHOLD = 0.8  # % of model token limit to trigger compaction
COMPRESSION_TOKEN_HEADROOM = 0.95  # compact before the next prompt can push past threshold
MAX_MESSAGES = 120  # message count ceiling — compress regardless of tokens
COMPRESSION_KEEP_RATIO = 0.2  # the most recent % of messages to keep uncompressed
SESSION_HANDOFF_MARKER = "[Session State Handoff]"

# Tool result offloading: large results kept in context only as a head+tail preview.
# Manus/Claude Code pattern: full representation → session-local file for
# read_file/search ergonomics, compact preview → context. Durable raw evidence is
# separately content-addressed by core.raw_tool_results and indexed by tool_results.
#
# This is the ONE knob that gates tool-result truncation. Tools must not trim their own
# output without leaving a continuation path. Results above OFFLOAD_THRESHOLD are written
# to a session-local file (core.tool_result_files) and ArdenToolExecutor's result boundary
# returns a head+tail preview pointing at file_read(path=...) so the agent reads more by path.
ARDEN_TMP_BASE = "/tmp/arden"  # Background-task result staging.
OFFLOAD_THRESHOLD = 50000  # chars — results above this are reduced to a preview + durable file
OFFLOAD_PREVIEW_LINES = 30  # lines kept in compact reference (structural summary, not raw chars)
OFFLOAD_PREVIEW_CHARS = 12000  # hard cap for compact references; full content is in the durable file

# Durable event-log policy for raw tool results. The event log keeps small
# results inline; larger raw bodies are content-addressed under ~/.arden/blobs
# and session_events stores a pointer plus bounded preview.
RAW_TOOL_RESULT_INLINE_MAX_BYTES = 64 * 1024
RAW_TOOL_RESULT_PREVIEW_CHARS = OFFLOAD_PREVIEW_CHARS
RAW_TOOL_RESULT_DATA_KEY = "_raw_tool_result"
RAW_SEARCH_RESULT_RETENTION_DAYS = 7


# --- Display Truncation ---

EMAIL_SUBJECT_TRUNCATE = 40
EMAIL_FROM_TRUNCATE = 30


# --- Search & Retrieval ---

RRF_K = 60
RRF_OVERFETCH_FACTOR = 2

# --- Context Compression (Summarizer) ---

SUMMARY_MAX_TOKENS = 1500

# --- Automation ---

DAYS_IN_WEEK = 7
SCHEDULER_POLL_INTERVAL = 60  # seconds; safety-net poll. Real fire timing is
# event-driven: `handle_run_completed` fires session-bound loops the moment the
# target session goes idle, and `_start_run` advances `next_run_at` before the
# task body so the UI countdown ticks during long runs. This poll is the
# fallback for non-session-bound automations and post-crash reconciliation.
SCHEDULER_DEDUP_TTL = 86400  # 24 hours
SCHEDULER_EVENT_MAX_RETRIES = 5
SCHEDULER_EVENT_RETRY_BASE_SECONDS = 30
SCHEDULER_EVENT_RETRY_MAX_SECONDS = 1800

BUILTIN_MEMORY_CONSOLIDATE_ID = "builtin-memory-consolidate"
MEMORY_CAPTURE_AT = "02:30"
MEMORY_CAPTURE_IDLE_MINUTES = 10
MEMORY_CAPTURE_EVERY_N_RUNS = 10
MEMORY_CONSOLIDATE_AT = "03:00"
BUILTIN_MEMORY_SYNTHESIZE_ID = "builtin-memory-synthesize"
BUILTIN_WIKI_MAINTENANCE_ID = "builtin-wiki-maintenance"
BUILTIN_MEMORY_RETENTION_ID = "builtin-memory-retention"
BUILTIN_MEMORY_CAPTURE_ID = "builtin-memory-capture"
BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID = "builtin-memory-storage-maintenance"
MEMORY_RETENTION_AT = "03:45"
DAILY_NOTES_AUTOMATION_ID = "daily-notes"
RETIRED_BUILTIN_AUTOMATION_IDS = (
    "builtin-area-suggester",
    "builtin-automation-suggester-daily",
    "builtin-memory-dream",
)
# --- Monitor ---

MONITOR_POLL_INTERVAL = 300  # 5 minutes
MONITOR_EVENT_APPROACHING_HORIZON_MINUTES = 5 * 60  # 5 hours
AUTOMATION_EVENT_APPROACHING_DEFAULT_LEAD_MINUTES = 60
MONITOR_CALENDAR_DAYS = 1
MONITOR_CALENDAR_LIMIT = 50

SLACK_MONITOR_POLL_INTERVAL = 60  # seconds between Slack channel polls
MESSAGE_RECEIVED = "message_received"  # TriggerEvent.event_type for Slack messages

assert AUTOMATION_EVENT_APPROACHING_DEFAULT_LEAD_MINUTES <= MONITOR_EVENT_APPROACHING_HORIZON_MINUTES, (
    f"{AUTOMATION_EVENT_APPROACHING_DEFAULT_LEAD_MINUTES=} must be <= {MONITOR_EVENT_APPROACHING_HORIZON_MINUTES=}"
)


# --- Areas ---

AREAS_STATE_FILE = "areas-state.json"
AREAS_AGENT_STATE_FILE = "areas-agent-state.json"
AREA_AGENT_HANDLER = "area_agent"
AREA_AGENT_DAILY_AT = "06:30"

# Attention presets: the one dial that sets the custodian's envelope.
# min/max clamp the agent's self-chosen next check; runs/day caps event
# wakes + heartbeats together (budget guard); quiet_decay stretches the
# interval after consecutive quiet runs.
AREA_ATTENTION_PRESETS = {
    "active": {"min_hours": 2.0, "max_hours": 48.0, "runs_per_day": 8},
    "ambient": {"min_hours": 12.0, "max_hours": 24.0 * 7, "runs_per_day": 3},
    "dormant": {"min_hours": 24.0 * 3, "max_hours": 24.0 * 14, "runs_per_day": 1},
}
# Lane title for asks raised from a plain chat, which belongs to no area.
UNFILED_ASK_TITLE = "Chats"
AREA_QUIET_DECAY_FACTOR = 1.5  # applied after 2+ consecutive quiet runs
AREA_ASK_IGNORED_DAYS = 7  # unanswered this long → attention steps down

# How long a detached (fire-and-accept) automation run may stay in flight
# before the scheduler assumes its RunCompleted was lost and fails the run.
DETACHED_RUN_MAX_AGE = timedelta(hours=3)

# Executor protocol: a connected client executor holds one lease, renewed by
# heartbeat; expiry lets a reconnect supersede it and fences stale results.
EXECUTOR_LEASE_TTL_SECONDS = 60
EXECUTOR_STREAM_KEEPALIVE_SECONDS = 15

# Device-advertised skills (executor protocol): bounds on one snapshot.
DEVICE_SKILL_MAX_COUNT = 200
DEVICE_SKILL_MAX_DESCRIPTION_CHARS = 1024
DEVICE_SKILL_MAX_BODY_BYTES = 512 * 1024
