import Combine
import Foundation

@MainActor
final class ArdenMobileStore: ObservableObject {
    @Published var config: AppConfig
    @Published var sessions: [SessionListItem] = []
    @Published var selectedSessionID: String?
    @Published var transcript = MobileTranscript()
    @Published var isLoading = false
    @Published var isSending = false
    @Published var isStreaming = false
    @Published var connectionLabel = "Disconnected"
    @Published var errorMessage: String?
    @Published var useMockData: Bool
    @Published var automations: [MockAutomation] = MockArdenData.automations

    private let api: ArdenAPIClient
    private let configStore: KeychainConfigStore
    private var streamTask: Task<Void, Never>?
    private static let mockModeKey = "arden.mobile.useMockData"

    var selectedSession: SessionListItem? {
        sessions.first { $0.sessionID == selectedSessionID }
    }

    var needsConfiguration: Bool {
        // The server requires a Bearer key on every endpoint except /health, so a
        // blank key means real mode can't work — treat it as unconfigured.
        !useMockData && (config.normalized.serverURL.isEmpty || config.normalized.apiKey.isEmpty)
    }

    init(api: ArdenAPIClient = ArdenAPIClient(), configStore: KeychainConfigStore = KeychainConfigStore()) {
        self.api = api
        self.configStore = configStore
        self.config = configStore.load()
        self.useMockData = UserDefaults.standard.object(forKey: Self.mockModeKey) as? Bool ?? true
    }

    deinit {
        streamTask?.cancel()
    }

    func bootstrap() async {
        if useMockData {
            loadMockState()
            return
        }

        guard !needsConfiguration else {
            connectionLabel = "Configure"
            return
        }
        await reload()
    }

    func saveConfig(_ next: AppConfig) async {
        do {
            config = try configStore.save(next)
            await reload()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func reload() async {
        if useMockData {
            loadMockState()
            return
        }

        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            _ = try await api.health(config: config)
            // /health only reports `auth` when a token is sent AND a key is set,
            // so it can't be trusted — the first authed call is the real signal.
            sessions = try await api.listSessions(config: config)
            connectionLabel = "Connected"
            await loadAutomations()

            if selectedSessionID == nil {
                selectedSessionID = sessions.first?.sessionID
            }
            if let selectedSessionID {
                await loadSession(selectedSessionID)
            }
        } catch let error as ArdenAPIError where error.isUnauthorized {
            connectionLabel = "Auth failed"
            errorMessage = "Invalid API key — check the key in Settings."
        } catch {
            connectionLabel = "Disconnected"
            errorMessage = error.localizedDescription
        }
    }

    func createSession() async {
        if useMockData {
            createMockSession()
            return
        }

        do {
            let created = try await api.createSession(config: config)
            let item = SessionListItem(
                sessionID: created.sessionID,
                startedAt: created.startedAt,
                lastActivity: created.lastActivity,
                name: created.name,
                messageCount: created.messageCount ?? 0,
                sessionType: "chat",
                activeRunID: nil,
                runStatus: nil,
                pendingApprovalsCount: nil
            )
            sessions.insert(item, at: 0)
            await selectSession(created.sessionID)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func selectSession(_ sessionID: String) async {
        guard selectedSessionID != sessionID || transcript.messages.isEmpty else { return }
        selectedSessionID = sessionID
        if useMockData {
            transcript = MockArdenData.transcript(for: sessionID)
            isStreaming = transcript.activeRunID != nil
            connectionLabel = "Stub data"
            return
        }
        await loadSession(sessionID)
    }

    func send(_ rawText: String) async {
        let text = rawText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }

        if useMockData {
            await sendMockMessage(text)
            return
        }

        isSending = true
        errorMessage = nil
        defer { isSending = false }

        do {
            let sessionID = try await ensureSession()
            let clientID = UUID().uuidString
            mutateTranscript { $0.appendUser(text: text, clientID: clientID) }

            let response = try await api.sendMessage(config: config, sessionID: sessionID, text: text, clientID: clientID)
            mutateTranscript { $0.markRunStarted(runID: response.runID) }
            isStreaming = true
            startStream(sessionID: sessionID, afterSeq: transcript.latestSeq)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func resolve(_ approval: PendingApproval, approved: Bool) async {
        if useMockData {
            mutateTranscript {
                $0.removeApproval(toolID: approval.toolID)
                $0.appendActivity(approved ? "Approved \(approval.toolName)" : "Rejected \(approval.toolName)", id: "mock-approval-\(UUID().uuidString)")
                $0.markRunFinished()
            }
            isStreaming = false
            touchSelectedMockSession()
            return
        }

        guard let runID = approval.runID ?? transcript.activeRunID else {
            errorMessage = "No active run for approval"
            return
        }

        do {
            try await api.submitApproval(config: config, runID: runID, toolID: approval.toolID, approved: approved)
            mutateTranscript { $0.removeApproval(toolID: approval.toolID) }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func cancelRun() async {
        if useMockData {
            streamTask?.cancel()
            isStreaming = false
            mutateTranscript {
                $0.markRunFinished()
                $0.appendActivity("Run cancelled", id: "mock-cancelled-\(UUID().uuidString)")
            }
            touchSelectedMockSession()
            return
        }

        guard transcript.activeRunID != nil || selectedSessionID != nil else { return }

        do {
            try await api.cancelRun(config: config, runID: transcript.activeRunID, sessionID: selectedSessionID)
            streamTask?.cancel()
            isStreaming = false
            mutateTranscript { $0.markRunFinished() }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func setMockDataEnabled(_ enabled: Bool) async {
        guard useMockData != enabled else { return }
        streamTask?.cancel()
        useMockData = enabled
        UserDefaults.standard.set(enabled, forKey: Self.mockModeKey)
        await reload()
    }

    func loadAutomations() async {
        if useMockData {
            automations = MockArdenData.automations
            return
        }
        if let items = try? await api.listAutomations(config: config) {
            automations = items.map(Self.mapAutomation)
        }
    }

    func toggleAutomation(_ id: String) async {
        guard let index = automations.firstIndex(where: { $0.id == id }) else { return }
        if useMockData {
            automations[index].enabled.toggle()
            return
        }
        let previous = automations[index].enabled
        automations[index].enabled.toggle() // optimistic
        do {
            let enabled = try await api.toggleAutomation(config: config, id: id)
            if let i = automations.firstIndex(where: { $0.id == id }) {
                automations[i].enabled = enabled
            }
        } catch {
            if let i = automations.firstIndex(where: { $0.id == id }) {
                automations[i].enabled = previous
            }
            errorMessage = error.localizedDescription
        }
    }

    func runAutomation(_ id: String) async {
        guard !useMockData else { return }
        do {
            _ = try await api.runAutomation(config: config, id: id)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    nonisolated private static func mapAutomation(_ item: AutomationItem) -> MockAutomation {
        MockAutomation(
            id: item.taskID,
            name: item.name,
            description: item.description,
            enabled: item.enabled,
            schedule: scheduleSummary(item.triggers),
            trigger: item.triggers.first?.type ?? "—",
            lastState: mapRunState(item.lastStatus),
            lastRun: relativeTimeLabel(item.lastRunAt),
            nextRun: relativeTimeLabel(item.nextRunAt),
            builtin: item.builtin,
            runs: []
        )
    }

    nonisolated private static func scheduleSummary(_ triggers: [AutomationTrigger]) -> String {
        guard let trigger = triggers.first else { return "Manual" }
        switch trigger.type {
        case "time":
            if case .string(let at)? = trigger.params["at"] { return "Daily · \(at)" }
            if case .string(let every)? = trigger.params["every"] { return "Every \(every)" }
            return "Scheduled"
        case "message":
            if case .array(let channels)? = trigger.params["channels"], case .string(let first)? = channels.first { return "#\(first)" }
            return "On message"
        case "event":
            return "On event"
        default:
            return trigger.type.capitalized
        }
    }

    nonisolated private static func mapRunState(_ status: String?) -> RunState? {
        switch status {
        case "completed", "success": return .completed
        case "failed", "error": return .failed
        case "running": return .running
        case "cancelled": return .cancelled
        case .some: return .pending
        default: return nil
        }
    }

    nonisolated private static func relativeTimeLabel(_ iso: String?) -> String? {
        guard let iso, let date = ISO8601DateFormatter().date(from: iso) else { return iso }
        let interval = Date().timeIntervalSince(date)
        if interval < 0 {
            let future = -interval
            if future < 3600 { return "in \(Int(future / 60))m" }
            if future < 86400 { return "in \(Int(future / 3600))h" }
            return "in \(Int(future / 86400))d"
        }
        if interval < 60 { return "now" }
        if interval < 3600 { return "\(Int(interval / 60))m ago" }
        if interval < 86400 { return "\(Int(interval / 3600))h ago" }
        return "\(Int(interval / 86400))d ago"
    }

    private func ensureSession() async throws -> String {
        if let selectedSessionID {
            return selectedSessionID
        }
        let created = try await api.createSession(config: config)
        let item = SessionListItem(
            sessionID: created.sessionID,
            startedAt: created.startedAt,
            lastActivity: created.lastActivity,
            name: created.name,
            messageCount: created.messageCount ?? 0,
            sessionType: "chat",
            activeRunID: nil,
            runStatus: nil,
            pendingApprovalsCount: nil
        )
        sessions.insert(item, at: 0)
        selectedSessionID = created.sessionID
        return created.sessionID
    }

    private func loadMockState() {
        streamTask?.cancel()
        isLoading = false
        isSending = false
        isStreaming = false
        errorMessage = nil
        connectionLabel = "Stub data"
        sessions = MockArdenData.sessions
        automations = MockArdenData.automations

        if selectedSessionID == nil || sessions.contains(where: { $0.sessionID == selectedSessionID }) == false {
            selectedSessionID = sessions.first?.sessionID
        }

        transcript = MockArdenData.transcript(for: selectedSessionID)
        isStreaming = transcript.activeRunID != nil
    }

    private func createMockSession() {
        let id = "mock-\(UUID().uuidString)"
        let item = SessionListItem(
            sessionID: id,
            startedAt: "2026-06-14T14:00:00Z",
            lastActivity: "2026-06-14T14:00:00Z",
            name: "New mobile chat",
            messageCount: 0,
            sessionType: "chat",
            activeRunID: nil,
            runStatus: nil,
            pendingApprovalsCount: nil
        )
        sessions.insert(item, at: 0)
        selectedSessionID = id
        transcript = MobileTranscript()
        connectionLabel = "Stub data"
    }

    private func ensureMockSession() -> String {
        if let selectedSessionID {
            return selectedSessionID
        }
        createMockSession()
        return selectedSessionID ?? "mock-local"
    }

    private func sendMockMessage(_ text: String) async {
        streamTask?.cancel()
        let sessionID = ensureMockSession()
        let clientID = UUID().uuidString

        if let trigger = MockArdenData.richTrigger(for: text) {
            mutateTranscript { transcript in
                transcript.appendUser(text: text, clientID: clientID)
                switch trigger {
                case .workflow:
                    transcript.appendWorkflow(id: "wf-\(clientID)", MockArdenData.demoWorkflow())
                case .subagents:
                    transcript.appendSubagents(id: "sa-\(clientID)", MockArdenData.demoSubagents())
                case .toolChain:
                    transcript.appendToolChain(id: "tc-\(clientID)", steps: MockArdenData.demoToolChain())
                case .artifact:
                    transcript.appendArtifact(id: "art-\(clientID)", MockArdenData.demoArtifact())
                }
            }
            touchSelectedMockSession(messageDelta: 1)
            return
        }

        let runID = "mock-run-\(UUID().uuidString)"
        let responseID = "mock-response-\(UUID().uuidString)"
        let reply = MockArdenData.reply(for: text)
        let chunks = MockArdenData.chunks(for: reply)

        isSending = true
        errorMessage = nil
        mutateTranscript {
            $0.appendUser(text: text, clientID: clientID)
            $0.markRunStarted(runID: runID)
            $0.appendAssistant(id: responseID, isStreaming: true)
        }
        touchSelectedMockSession(messageDelta: 1, activeRunID: runID, runStatus: "running")
        isSending = false
        isStreaming = true

        streamTask = Task { [weak self] in
            for chunk in chunks {
                if Task.isCancelled { return }
                try? await Task.sleep(nanoseconds: 55_000_000)
                if Task.isCancelled { return }
                await self?.appendMockChunk(chunk, messageID: responseID, sessionID: sessionID)
            }
            if Task.isCancelled { return }
            await self?.finishMockResponse(reply: reply, messageID: responseID, runID: runID, sessionID: sessionID)
        }
    }

    private func appendMockChunk(_ chunk: String, messageID: String, sessionID: String) {
        guard selectedSessionID == sessionID else { return }
        mutateTranscript { $0.appendDelta(toMessageID: messageID, delta: chunk) }
    }

    private func finishMockResponse(reply: String, messageID: String, runID: String, sessionID: String) {
        guard selectedSessionID == sessionID else { return }
        mutateTranscript {
            $0.finishMessage(id: messageID, content: reply)
            $0.markRunFinished()
        }
        isStreaming = false
        touchSelectedMockSession(messageDelta: 1)
    }

    private func touchSelectedMockSession(messageDelta: Int = 0, activeRunID: String? = nil, runStatus: String? = nil) {
        guard let selectedSessionID, let index = sessions.firstIndex(where: { $0.sessionID == selectedSessionID }) else { return }
        let current = sessions[index]
        sessions[index] = SessionListItem(
            sessionID: current.sessionID,
            startedAt: current.startedAt,
            lastActivity: "2026-06-14T14:00:00Z",
            name: current.name,
            messageCount: max(0, current.messageCount + messageDelta),
            sessionType: current.sessionType,
            activeRunID: activeRunID,
            runStatus: runStatus,
            pendingApprovalsCount: transcript.pendingApprovals.isEmpty ? nil : transcript.pendingApprovals.count
        )
    }

    private func loadSession(_ sessionID: String) async {
        streamTask?.cancel()
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let history = try await api.history(config: config, sessionID: sessionID)
            guard selectedSessionID == sessionID else { return }
            mutateTranscript { $0.load(history: history) }
            await rehydrateWorkflows(sessionID: sessionID)
            isStreaming = transcript.activeRunID != nil
            startStream(sessionID: sessionID, afterSeq: transcript.latestSeq)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Replay the session's persisted workflow-domain events (bounded to the
    /// transcript checkpoint) through the reducer so workflow/subagent cards
    /// rebuild after a history load. The live SSE tail re-delivers anything beyond
    /// the checkpoint, so this never double-applies. Best-effort — a failure just
    /// means the cards rebuild from the next live event.
    private func rehydrateWorkflows(sessionID: String) async {
        guard let events = try? await api.workflowEvents(config: config, sessionID: sessionID),
              !events.isEmpty,
              selectedSessionID == sessionID else { return }
        mutateTranscript { transcript in
            for event in events { transcript.apply(event) }
        }
    }

    /// Reload history + replay persisted workflow events (best-effort). Returns
    /// false if the history fetch failed, in which case the caller keeps its cursor.
    @discardableResult
    private func reanchorFromHistory(sessionID: String) async -> Bool {
        guard let fresh = try? await api.history(config: config, sessionID: sessionID),
              selectedSessionID == sessionID else { return false }
        mutateTranscript { $0.load(history: fresh) }
        await rehydrateWorkflows(sessionID: sessionID)
        isStreaming = transcript.activeRunID != nil
        return true
    }

    private func startStream(sessionID: String, afterSeq: Int?) {
        streamTask?.cancel()
        streamTask = Task { [weak self] in
            await self?.runStreamLoop(sessionID: sessionID, afterSeq: afterSeq)
        }
    }

    // Consume the SSE stream, reconnecting with backoff and re-anchoring the
    // cursor from fresh history when the connection drops mid-run. A `stream_reset`
    // event (the server compacted the ledger) breaks out of the read loop and
    // triggers an immediate history reload + reconnect — crucially WITHOUT
    // cancelling this task, so the stream recovers instead of dying silently.
    private func runStreamLoop(sessionID: String, afterSeq: Int?) async {
        var cursor = afterSeq
        var attempt = 0
        while !Task.isCancelled {
            guard selectedSessionID == sessionID else { return }
            var resetRequested = false
            do {
                let stream = try await api.streamEvents(config: config, sessionID: sessionID, afterSeq: cursor)
                attempt = 0
                connectionLabel = "Connected"
                for try await event in stream {
                    if Task.isCancelled { return }
                    await handleStreamEvent(event, sessionID: sessionID)
                    cursor = transcript.latestSeq
                    if transcript.needsHistoryReload {
                        mutateTranscript { $0.clearReloadRequest() }
                        resetRequested = true
                        break
                    }
                }
                guard selectedSessionID == sessionID else { return }
                // Clean end with no reset: stop if the run finished, else reconnect.
                if !resetRequested, transcript.activeRunID == nil {
                    isStreaming = false
                    return
                }
            } catch is CancellationError {
                return
            } catch {
                guard !Task.isCancelled, selectedSessionID == sessionID else { return }
                if (error as? ArdenAPIError)?.isUnauthorized == true {
                    connectionLabel = "Auth failed"
                    errorMessage = "Invalid API key"
                    isStreaming = false
                    return
                }
            }

            if resetRequested {
                // Not an error: reload + reconnect immediately, no backoff, and the
                // attempt counter stays put.
                connectionLabel = "Reconnecting"
                if await reanchorFromHistory(sessionID: sessionID) {
                    cursor = transcript.latestSeq
                }
                guard !Task.isCancelled, selectedSessionID == sessionID else { return }
                continue
            }

            attempt += 1
            guard attempt <= 6 else {
                connectionLabel = "Disconnected"
                isStreaming = false
                return
            }
            connectionLabel = "Reconnecting"
            let delayMs = min(8_000, 250 << min(attempt, 5))
            try? await Task.sleep(nanoseconds: UInt64(delayMs) * 1_000_000)
            guard !Task.isCancelled, selectedSessionID == sessionID else { return }

            // Re-anchor from fresh history before reconnecting.
            if await reanchorFromHistory(sessionID: sessionID) {
                cursor = transcript.latestSeq
                if transcript.activeRunID == nil {
                    connectionLabel = "Connected"
                    isStreaming = false
                    return
                }
            }
        }
    }

    private func handleStreamEvent(_ event: StreamEvent, sessionID: String) async {
        guard selectedSessionID == sessionID else { return }
        mutateTranscript { $0.apply(event) }
        isStreaming = transcript.activeRunID != nil
    }

    private func mutateTranscript(_ body: (inout MobileTranscript) -> Void) {
        body(&transcript)
    }
}
