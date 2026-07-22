import Foundation

enum ArdenAPIError: Error, LocalizedError, Equatable {
    case invalidURL(String)
    case noHTTPResponse
    case badStatus(Int, String)

    var errorDescription: String? {
        switch self {
        case .invalidURL(let value):
            "Invalid URL: \(value)"
        case .noHTTPResponse:
            "No HTTP response"
        case .badStatus(let status, let message):
            message.isEmpty ? "HTTP \(status)" : message
        }
    }

    var status: Int? {
        if case .badStatus(let code, _) = self { return code }
        return nil
    }

    /// 401 = missing/invalid API key.
    var isUnauthorized: Bool { status == 401 }
}

final class ArdenAPIClient: @unchecked Sendable {
    private let session: URLSession
    private let streamSession: URLSession
    private let decoder = JSONDecoder()
    private static let encoder = JSONEncoder()

    init(session: URLSession = .shared) {
        self.session = session
        // SSE is a long-lived connection (keepalives every ~5s). The default 30s
        // request timeout would kill it mid-stream, so use a dedicated session
        // that tolerates long idle gaps and waits for connectivity.
        let streamConfig = URLSessionConfiguration.default
        streamConfig.timeoutIntervalForRequest = 120
        streamConfig.timeoutIntervalForResource = .infinity
        streamConfig.waitsForConnectivity = true
        self.streamSession = URLSession(configuration: streamConfig)
    }

    func health(config: AppConfig) async throws -> HealthResponse {
        try await request(config: config, path: "/health")
    }

    func listSessions(config: AppConfig) async throws -> [SessionListItem] {
        let response: SessionListResponse = try await request(config: config, path: "/sessions")
        return response.sessions
    }

    func createSession(config: AppConfig) async throws -> CreateSessionResponse {
        try await request(config: config, path: "/sessions", method: "POST", body: CreateSessionRequest(name: nil))
    }

    func history(config: AppConfig, sessionID: String, limit: Int = 100) async throws -> HistoryResponse {
        let path = "/session/history?session_id=\(Self.queryValue(sessionID))&limit=\(limit)"
        return try await request(config: config, path: path)
    }

    /// Persisted workflow-domain events (bounded to the transcript checkpoint) so
    /// the client can rebuild its workflow/subagent cards after a history load —
    /// the live in-memory domain is lost when the transcript is rebuilt.
    func workflowEvents(config: AppConfig, sessionID: String) async throws -> [StreamEvent] {
        let response: WorkflowEventsResponse = try await request(
            config: config,
            path: "/chat/\(Self.pathComponent(sessionID))/workflows"
        )
        return response.events
    }

    func sendMessage(config: AppConfig, sessionID: String, text: String, clientID: String) async throws -> ChatMessageResponse {
        try await request(
            config: config,
            path: "/chat/message",
            method: "POST",
            body: ChatMessageRequest(message: text, sessionID: sessionID, clientID: clientID)
        )
    }

    func submitApproval(config: AppConfig, runID: String, toolID: String, approved: Bool) async throws {
        let _: EmptyResponse = try await request(
            config: config,
            path: "/tools/result",
            method: "POST",
            body: ToolResultRequest(runID: runID, toolID: toolID, result: "", approved: approved)
        )
    }

    func cancelRun(config: AppConfig, runID: String?, sessionID: String?) async throws {
        let _: EmptyResponse = try await request(
            config: config,
            path: "/cancel",
            method: "POST",
            body: CancelRunRequest(runID: runID, sessionID: sessionID)
        )
    }

    func listAutomations(config: AppConfig) async throws -> [AutomationItem] {
        let response: AutomationListResponse = try await request(config: config, path: "/automations")
        return response.automations
    }

    /// Flips an automation's enabled flag. The real route is a stateless toggle
    /// (POST /automations/{id}/toggle) — there is no enabled body param — so the
    /// server decides the new value and returns it.
    @discardableResult
    func toggleAutomation(config: AppConfig, id: String) async throws -> Bool {
        let response: AutomationToggleResponse = try await request(
            config: config,
            path: "/automations/\(Self.pathComponent(id))/toggle",
            method: "POST"
        )
        return response.enabled
    }

    @discardableResult
    func runAutomation(config: AppConfig, id: String) async throws -> String {
        let response: AutomationRunResponse = try await request(
            config: config,
            path: "/automations/\(Self.pathComponent(id))/run",
            method: "POST"
        )
        return response.status
    }

    func childAgentResult(
        config: AppConfig,
        childRunID: String,
        sessionID: String,
        wait: Bool = false,
        timeoutSeconds: Double = 0.0
    ) async throws -> ChildAgentResultResponse {
        var path = "/chat/child-agents/\(Self.pathComponent(childRunID))/result"
        path += "?session_id=\(Self.queryValue(sessionID))&wait=\(wait)&timeout_seconds=\(timeoutSeconds)"
        return try await request(config: config, path: path)
    }

    @discardableResult
    func cancelChildAgent(config: AppConfig, childRunID: String, sessionID: String) async throws -> ChildAgentCancelResponse {
        try await request(
            config: config,
            path: "/chat/child-agents/\(Self.pathComponent(childRunID))/cancel?session_id=\(Self.queryValue(sessionID))",
            method: "POST"
        )
    }

    func streamEvents(config: AppConfig, sessionID: String, afterSeq: Int?) async throws -> AsyncThrowingStream<StreamEvent, Error> {
        var path = "/chat/events/\(Self.pathComponent(sessionID))?stream=true"
        if let afterSeq {
            path += "&after_seq=\(afterSeq)"
        }
        var built = try Self.makeRequest(config: config, path: path, method: "GET", body: Optional<EmptyRequest>.none)
        built.timeoutInterval = 120 // per-request override; keepalives reset the idle timer
        built.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        let request = built // capture an immutable copy so the stream Task can't race the builder
        return AsyncThrowingStream { continuation in
            let session = self.streamSession
            let task = Task {
                do {
                    let (bytes, response) = try await session.bytes(for: request)
                    if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                        throw ArdenAPIError.badStatus(http.statusCode, "")
                    }

                    var parser = SSEParser()
                    let decoder = JSONDecoder()
                    for try await line in bytes.lines {
                        let messages = parser.feed(line + "\n")
                        for message in messages {
                            guard let data = message.data.data(using: .utf8) else { continue }
                            continuation.yield(try decoder.decode(StreamEvent.self, from: data))
                        }
                    }
                    continuation.finish()
                } catch {
                    Task.isCancelled ? continuation.finish() : continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private func request<T: Decodable>(config: AppConfig, path: String, method: String = "GET") async throws -> T {
        try await request(config: config, path: path, method: method, body: Optional<EmptyRequest>.none)
    }

    private func request<T: Decodable, Body: Encodable>(
        config: AppConfig,
        path: String,
        method: String = "GET",
        body: Body?
    ) async throws -> T {
        let request = try Self.makeRequest(config: config, path: path, method: method, body: body)
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw ArdenAPIError.noHTTPResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            throw ArdenAPIError.badStatus(http.statusCode, Self.errorMessage(from: data, status: http.statusCode))
        }
        if T.self == EmptyResponse.self {
            return EmptyResponse() as! T
        }
        return try decoder.decode(T.self, from: data)
    }

    static func makeRequest<Body: Encodable>(
        config: AppConfig,
        path: String,
        method: String,
        body: Body?
    ) throws -> URLRequest {
        let normalized = config.normalized
        guard let baseURL = URL(string: normalized.serverURL), let url = URL(string: path, relativeTo: baseURL) else {
            throw ArdenAPIError.invalidURL(normalized.serverURL + path)
        }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if !normalized.apiKey.isEmpty {
            request.setValue("Bearer \(normalized.apiKey)", forHTTPHeaderField: "Authorization")
        }
        if let body {
            request.httpBody = try encoder.encode(body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    static func pathComponent(_ value: String) -> String {
        var allowed = CharacterSet.urlPathAllowed
        allowed.remove(charactersIn: "/?#[]@!$&'()*+,;=")
        return value.addingPercentEncoding(withAllowedCharacters: allowed) ?? value
    }

    static func queryValue(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? value
    }

    private static func errorMessage(from data: Data, status: Int) -> String {
        // The server returns `detail` as a String OR an object {code, message, ...}.
        if let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            if let detail = object["detail"] as? String {
                return detail
            }
            if let detail = object["detail"] as? [String: Any], let message = detail["message"] as? String {
                return message
            }
            if let message = object["message"] as? String {
                return message
            }
        }
        return String(data: data, encoding: .utf8) ?? "HTTP \(status)"
    }
}

private struct EmptyRequest: Encodable {}
private struct EmptyResponse: Decodable {}
