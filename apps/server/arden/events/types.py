from enum import StrEnum


class EventType(StrEnum):
    # AG-UI canonical events.
    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_ERROR = "RUN_ERROR"

    TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
    TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
    TEXT_MESSAGE_END = "TEXT_MESSAGE_END"

    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
    TOOL_CALL_END = "TOOL_CALL_END"
    TOOL_CALL_RESULT = "TOOL_CALL_RESULT"

    REASONING_START = "REASONING_START"
    REASONING_MESSAGE_START = "REASONING_MESSAGE_START"
    REASONING_MESSAGE_CONTENT = "REASONING_MESSAGE_CONTENT"
    REASONING_MESSAGE_END = "REASONING_MESSAGE_END"
    REASONING_END = "REASONING_END"

    # Arden-specific events.
    THINKING = "thinking"
    APPROVAL_NEEDED = "approval_needed"
    INPUT_NEEDED = "input_needed"
    CONNECTION_NEEDED = "connection_needed"
    BACKGROUND_TASK = "background_task"
    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_FINISHED = "workflow_finished"
    RUN_CANCELLED = "run_cancelled"
    RUN_BACKGROUNDED = "run_backgrounded"
    MESSAGE_INGESTED = "message_ingested"
    STREAM_RESET = "stream_reset"
    STREAM_KEEPALIVE = "stream_keepalive"
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    TASK_FINISHED = "task_finished"
    AUTOMATION_PROGRESS = "automation_progress"
    AUTOMATION_FINISHED = "automation_finished"
    COMPACTION_STARTED = "compaction_started"
    COMPACTION_FINISHED = "compaction_finished"
    TOKEN_USAGE = "token_usage"
    GOAL_UPDATED = "goal_updated"
    GOAL_CLEARED = "goal_cleared"
    MEMORY_CHANGED = "memory_changed"
    AREAS_CHANGED = "areas_changed"
    TODO_UPDATED = "todo_updated"
    SESSION_UPDATED = "session_updated"
    SESSION_CREATED = "session_created"
    SESSION_ACTIVITY = "session_activity"
    NAVIGATION_REQUESTED = "navigation_requested"
