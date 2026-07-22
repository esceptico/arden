import Foundation

// Real (non-mock) response models for the automation + child-agent endpoints.
// CodingKeys mirror the server payloads exactly (see automation.py / chat.py).

// MARK: - Automations

struct AutomationListResponse: Decodable, Equatable {
    let automations: [AutomationItem]
}

/// One row from `_automation_to_dict` in automation.py. Fields that the server
/// can emit as null (timestamps, last result) are optional. `triggers` is a
/// heterogeneous `{type, ...params}` list, so its params are kept as raw JSON.
struct AutomationItem: Identifiable, Decodable, Equatable {
    let taskID: String
    let name: String
    let description: String
    let model: String?
    let triggers: [AutomationTrigger]
    let enabled: Bool
    let createdAt: String
    let lastRunAt: String?
    let nextRunAt: String?
    let lastResult: String?
    let autoApprove: Bool
    let runningSince: String?
    let handler: String?
    let builtin: Bool
    let cooldownMinutes: Int?
    let kind: String?
    let readHistory: Bool?
    let recentStatuses: [String]
    let lastStatus: String?

    var id: String { taskID }

    enum CodingKeys: String, CodingKey {
        case taskID = "task_id"
        case name
        case description
        case model
        case triggers
        case enabled
        case createdAt = "created_at"
        case lastRunAt = "last_run_at"
        case nextRunAt = "next_run_at"
        case lastResult = "last_result"
        case autoApprove = "auto_approve"
        case runningSince = "running_since"
        case handler
        case builtin
        case cooldownMinutes = "cooldown_minutes"
        case kind
        case readHistory = "read_history"
        case recentStatuses = "recent_statuses"
        case lastStatus = "last_status"
    }
}

/// `{"type": "time"|"message"|"event"|..., ...params}` — only `type` is fixed;
/// the remaining trigger params vary by type and are preserved as raw JSON.
struct AutomationTrigger: Decodable, Equatable {
    let type: String
    let params: [String: JSONValue]

    enum CodingKeys: String, CodingKey {
        case type
    }

    init(from decoder: Decoder) throws {
        let typed = try decoder.container(keyedBy: CodingKeys.self)
        type = try typed.decode(String.self, forKey: .type)
        let dynamic = try decoder.container(keyedBy: JSONCodingKey.self)
        var rest: [String: JSONValue] = [:]
        for key in dynamic.allKeys where key.stringValue != CodingKeys.type.rawValue {
            rest[key.stringValue] = try dynamic.decode(JSONValue.self, forKey: key)
        }
        params = rest
    }
}

/// `POST /automations/{task_id}/toggle` → `{"enabled": bool}`.
struct AutomationToggleResponse: Decodable, Equatable {
    let enabled: Bool
}

/// `POST /automations/{task_id}/run` → `{"status": "started"}`.
struct AutomationRunResponse: Decodable, Equatable {
    let status: String
}

// MARK: - Workflow rehydration

/// `GET /chat/{session_id}/workflows` → `{"events": [<SSE-shaped event>, ...]}`,
/// bounded to the transcript checkpoint. Replayed through the transcript reducer
/// to rebuild workflow/subagent cards after a history load (the live workflows
/// domain is in-memory and is otherwise lost when the transcript is rebuilt).
struct WorkflowEventsResponse: Decodable, Equatable {
    let events: [StreamEvent]
}

// MARK: - Child agents

/// `GET /chat/child-agents/{child_run_id}/result` → `ChildAgentResultResponse`.
struct ChildAgentResultResponse: Decodable, Equatable {
    let taskID: String
    let childRunID: String
    let sessionID: String
    let status: String
    let terminal: Bool
    // The server stores the child agent's result as a TEXT column and the route's
    // response_model types it `str | None` (chat.py / schemas.py), so the wire
    // value is always a JSON string or null — never a decoded object.
    let result: String?
    let resultRef: String?

    enum CodingKeys: String, CodingKey {
        case taskID = "task_id"
        case childRunID = "child_run_id"
        case sessionID = "session_id"
        case status
        case terminal
        case result
        case resultRef = "result_ref"
    }
}

/// `POST /chat/child-agents/{child_run_id}/cancel` → `{status, task_id, child_run_id, command}`.
struct ChildAgentCancelResponse: Decodable, Equatable {
    let status: String
    let taskID: String
    let childRunID: String
    let command: String?

    enum CodingKeys: String, CodingKey {
        case status
        case taskID = "task_id"
        case childRunID = "child_run_id"
        case command
    }
}

// MARK: - JSON helpers

/// Type-erased JSON value for the heterogeneous, schema-free payloads the server
/// returns (automation trigger params).
enum JSONValue: Decodable, Equatable {
    case string(String)
    case int(Int)
    case double(Double)
    case bool(Bool)
    case array([JSONValue])
    case object([String: JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Int.self) {
            self = .int(value)
        } else if let value = try? container.decode(Double.self) {
            self = .double(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unsupported JSON value"
            )
        }
    }
}

private struct JSONCodingKey: CodingKey {
    let stringValue: String
    let intValue: Int?

    init(stringValue: String) {
        self.stringValue = stringValue
        intValue = nil
    }

    init?(intValue: Int) {
        stringValue = String(intValue)
        self.intValue = intValue
    }
}
