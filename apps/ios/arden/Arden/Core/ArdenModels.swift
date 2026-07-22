import Foundation

struct AppConfig: Equatable, Codable {
    var serverURL: String
    var apiKey: String

    static let `default` = AppConfig(serverURL: "http://localhost:6877", apiKey: "")

    var normalized: AppConfig {
        AppConfig(
            serverURL: serverURL.trimmingCharacters(in: .whitespacesAndNewlines).trimmingTrailingSlash(),
            apiKey: apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        )
    }
}

struct HealthResponse: Decodable, Equatable {
    let auth: Bool?
    let version: String?
    let hasProviders: Bool?

    enum CodingKeys: String, CodingKey {
        case auth
        case version
        case hasProviders = "has_providers"
    }
}

struct SessionListResponse: Decodable, Equatable {
    let sessions: [SessionListItem]
}

struct SessionListItem: Identifiable, Decodable, Equatable {
    let sessionID: String
    let startedAt: String
    let lastActivity: String
    let name: String?
    let messageCount: Int
    let sessionType: String?
    let activeRunID: String?
    let runStatus: String?
    let pendingApprovalsCount: Int?

    var id: String { sessionID }
    var title: String { name?.isEmpty == false ? name! : "Untitled" }

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case startedAt = "started_at"
        case lastActivity = "last_activity"
        case name
        case messageCount = "message_count"
        case sessionType = "session_type"
        case activeRunID = "active_run_id"
        case runStatus = "run_status"
        case pendingApprovalsCount = "pending_approvals_count"
    }
}

struct CreateSessionRequest: Encodable, Equatable {
    var name: String?
}

struct CreateSessionResponse: Decodable, Equatable {
    let sessionID: String
    let name: String?
    let startedAt: String
    let lastActivity: String
    let messageCount: Int?

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case name
        case startedAt = "started_at"
        case lastActivity = "last_activity"
        case messageCount = "message_count"
    }
}

struct HistoryResponse: Decodable, Equatable {
    let messages: [HistoryMessage]
    let activeRunID: String?
    let runtime: SessionRuntimeSnapshot?
    let page: HistoryPage?

    enum CodingKeys: String, CodingKey {
        case messages
        case activeRunID = "active_run_id"
        case runtime
        case page
    }
}

struct HistoryPage: Decodable, Equatable {
    let hasMoreBefore: Bool
    let hasMoreAfter: Bool
    let before: String?
    let after: String?

    enum CodingKeys: String, CodingKey {
        case hasMoreBefore = "has_more_before"
        case hasMoreAfter = "has_more_after"
        case before
        case after
    }
}

struct SessionRuntimeSnapshot: Decodable, Equatable {
    let sessionID: String
    let latestEventSeq: Int
    let checkpointSeq: Int
    let activeRun: ActiveRunSnapshot?
    let pendingApprovals: [PendingApproval]

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case latestEventSeq = "latest_event_seq"
        case checkpointSeq = "checkpoint_seq"
        case activeRun = "active_run"
        case pendingApprovals = "pending_approvals"
    }
}

struct ActiveRunSnapshot: Decodable, Equatable {
    let runID: String
    let status: String

    enum CodingKeys: String, CodingKey {
        case runID = "run_id"
        case status
    }
}

struct HistoryMessage: Identifiable, Decodable, Equatable {
    let role: String
    let content: String
    let reasoningContent: String?
    let toolCalls: [HistoryToolCall]?
    let toolCallID: String?
    let id: String?
    let messageID: String?
    let seq: Int?
    let createdAt: String?
    let isMeta: Bool?

    var stableID: String { id ?? messageID ?? "\(role)-\(seq ?? 0)-\(content.hashValue)" }

    enum CodingKeys: String, CodingKey {
        case role
        case content
        case reasoningContent = "reasoning_content"
        case toolCalls = "tool_calls"
        case toolCallID = "tool_call_id"
        case id
        case messageID = "message_id"
        case seq
        case createdAt = "created_at"
        case isMeta = "is_meta"
    }
}

struct HistoryToolCall: Decodable, Equatable {
    let id: String
    let name: String
    let arguments: String
    let displayName: String?
    let kind: String?

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case arguments
        case displayName = "display_name"
        case kind
    }
}

struct ChatMessageRequest: Codable, Equatable {
    let message: String
    let sessionID: String
    let clientID: String
    var skipApprovals: Bool = false

    enum CodingKeys: String, CodingKey {
        case message
        case sessionID = "session_id"
        case clientID = "client_id"
        case skipApprovals = "skip_approvals"
    }
}

struct ChatMessageResponse: Decodable, Equatable {
    let runID: String

    enum CodingKeys: String, CodingKey {
        case runID = "run_id"
    }
}

struct ToolResultRequest: Encodable, Equatable {
    let runID: String
    let toolID: String
    let result: String
    let approved: Bool

    enum CodingKeys: String, CodingKey {
        case runID = "run_id"
        case toolID = "tool_id"
        case result
        case approved
    }
}

struct CancelRunRequest: Encodable, Equatable {
    let runID: String?
    let sessionID: String?

    enum CodingKeys: String, CodingKey {
        case runID = "run_id"
        case sessionID = "session_id"
    }
}

struct PendingApproval: Identifiable, Decodable, Equatable {
    let toolID: String
    let toolName: String
    let preview: String?
    let diff: String?
    let runID: String?

    var id: String { toolID }

    init(toolID: String, toolName: String, preview: String?, diff: String?, runID: String?) {
        self.toolID = toolID
        self.toolName = toolName
        self.preview = preview
        self.diff = diff
        self.runID = runID
    }

    enum CodingKeys: String, CodingKey {
        case toolID = "tool_id"
        case toolName = "tool_name"
        case preview
        case diff
        case runID = "run_id"
    }
}

struct StreamEvent: Decodable, Equatable {
    let type: String
    let seq: Int?
    let sessionID: String?
    let runID: String?
    let messageID: String?
    let role: String?
    let delta: String?
    let content: String?
    let message: String?
    let toolID: String?
    let toolCallID: String?
    let toolCallName: String?
    let displayName: String?
    let name: String?
    let path: String?
    let diff: String?
    let contentPreview: String?
    let preview: String?
    let reason: String?
    let clientID: String?
    let latestSeq: Int?
    let usage: StreamUsage?
    let messageCount: Int?
    let taskID: String?
    let workflowID: String?
    let childRunID: String?
    let childSessionID: String?
    let parentTaskID: String?
    let agentType: String?
    let status: String?
    let summary: String?
    let depth: Int?
    let phase: String?
    let phases: [String]?
    let description: String?
    let command: String?
    let detail: String?
    let terminal: Bool?
    let wait: Bool?
    let resetSeq: Int?
    let scope: String?

    enum CodingKeys: String, CodingKey {
        case type
        case seq
        case sessionID = "session_id"
        case runID = "run_id"
        case messageID = "message_id"
        case role
        case delta
        case content
        case message
        case toolID = "tool_id"
        case toolCallID = "tool_call_id"
        case toolCallName = "tool_call_name"
        case displayName = "display_name"
        case name
        case path
        case diff
        case contentPreview = "content_preview"
        case preview
        case reason
        case clientID = "client_id"
        case latestSeq = "latest_seq"
        case usage
        case messageCount = "message_count"
        case taskID = "task_id"
        case workflowID = "workflow_id"
        case childRunID = "child_run_id"
        case childSessionID = "child_session_id"
        case parentTaskID = "parent_task_id"
        case agentType = "agent_type"
        case status
        case summary
        case depth
        case phase
        case phases
        case description
        case command
        case detail
        case terminal
        case wait
        case resetSeq = "reset_seq"
        case scope
    }
}

struct StreamUsage: Decodable, Equatable {
    let prompt: Int?
    let completion: Int?
    let total: Int?
    let cost: Double?
}

extension String {
    func trimmingTrailingSlash() -> String {
        var value = self
        while value.count > 1, value.hasSuffix("/") {
            value.removeLast()
        }
        return value
    }
}
