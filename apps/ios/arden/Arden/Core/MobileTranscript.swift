import Foundation

enum MobileMessageRole: String, Codable, Equatable {
    case user
    case assistant
    case tool
    case activity
    case error
    case workflow
    case subagents
    case toolChain
    case artifact

    init(serverRole: String?) {
        switch serverRole?.lowercased() {
        case "user":
            self = .user
        case "tool":
            self = .tool
        case "system":
            self = .activity
        case "error":
            self = .error
        default:
            self = .assistant
        }
    }
}

struct MobileMessage: Identifiable, Equatable {
    let id: String
    var role: MobileMessageRole
    var content: String
    var detail: String?
    var isStreaming: Bool

    // Rich mock-only payloads (nil for real/server messages).
    var workflow: MockWorkflow?
    var subagents: [MockSubagent]?
    var toolSteps: [MockToolStep]?
    var artifact: MockArtifact?

    init(id: String, role: MobileMessageRole, content: String, detail: String? = nil, isStreaming: Bool = false) {
        self.id = id
        self.role = role
        self.content = content
        self.detail = detail
        self.isStreaming = isStreaming
    }

    init(history: HistoryMessage) {
        let content = history.content.isEmpty ? (history.reasoningContent ?? "") : history.content
        self.init(
            id: history.stableID,
            role: MobileMessageRole(serverRole: history.role),
            content: content,
            detail: history.toolCalls?.first?.displayName ?? history.toolCalls?.first?.name,
            isStreaming: false
        )
    }
}

struct MobileTranscript: Equatable {
    private(set) var messages: [MobileMessage] = []
    private(set) var pendingApprovals: [PendingApproval] = []
    private(set) var latestSeq: Int?
    private(set) var activeRunID: String?
    private(set) var needsHistoryReload = false

    // Live workflow/subagent domains, built from server events (task_*/workflow_*/
    // token_usage/background_task). Keyed by workflow_id and task_id respectively.
    // The reducer mutates these, then re-projects each into a stable-id MobileMessage
    // so WorkflowCard / SubagentList render from real progress.
    private var workflows: [String: LiveWorkflow] = [:]
    private var standaloneAgents: [String: LiveAgent] = [:]
    private var standaloneOrder: [String] = []
    // Run that owns the standalone-subagents block; pinned on the first standalone
    // event so the message id stays stable after the run goes inactive.
    private var standaloneRunID: String?

    mutating func load(history: HistoryResponse) {
        messages = history.messages.map(MobileMessage.init(history:))
        pendingApprovals = history.runtime?.pendingApprovals ?? []
        latestSeq = history.runtime?.latestEventSeq ?? history.messages.compactMap(\.seq).max()
        activeRunID = history.runtime?.activeRun?.runID ?? history.activeRunID
        needsHistoryReload = false
        // A history load fully rebuilds the transcript, so drop the in-memory
        // workflow/subagent domains too — the caller re-seeds them from the
        // persisted `/workflows` events (bounded to this checkpoint). Without this
        // they'd bleed across sessions and the projected cards would never rebuild.
        workflows.removeAll()
        standaloneAgents.removeAll()
        standaloneOrder.removeAll()
        standaloneRunID = nil
    }

    mutating func appendUser(text: String, clientID: String) {
        messages.append(MobileMessage(id: clientID, role: .user, content: text))
    }

    mutating func appendAssistant(id: String, text: String = "", detail: String? = nil, isStreaming: Bool = false) {
        messages.append(MobileMessage(id: id, role: .assistant, content: text, detail: detail, isStreaming: isStreaming))
    }

    mutating func appendTool(id: String, name: String, content: String, isStreaming: Bool = false) {
        messages.append(MobileMessage(id: id, role: .tool, content: content, detail: name, isStreaming: isStreaming))
    }

    mutating func appendToolChain(id: String, steps: [MockToolStep]) {
        var message = MobileMessage(id: id, role: .toolChain, content: "")
        message.toolSteps = steps
        messages.append(message)
    }

    mutating func appendWorkflow(id: String, _ workflow: MockWorkflow) {
        var message = MobileMessage(id: id, role: .workflow, content: "")
        message.workflow = workflow
        messages.append(message)
    }

    mutating func appendSubagents(id: String, _ agents: [MockSubagent]) {
        var message = MobileMessage(id: id, role: .subagents, content: "")
        message.subagents = agents
        messages.append(message)
    }

    mutating func appendArtifact(id: String, _ artifact: MockArtifact) {
        var message = MobileMessage(id: id, role: .artifact, content: "")
        message.artifact = artifact
        messages.append(message)
    }

    mutating func appendDelta(toMessageID id: String, delta: String) {
        guard let index = messageIndex(id: id) else { return }
        messages[index].content += delta
        messages[index].isStreaming = true
    }

    mutating func finishMessage(id: String, content: String? = nil) {
        guard let index = messageIndex(id: id) else { return }
        if let content {
            messages[index].content = content
        }
        messages[index].isStreaming = false
    }

    mutating func markRunStarted(runID: String) {
        activeRunID = runID
    }

    mutating func markRunFinished() {
        activeRunID = nil
        pendingApprovals.removeAll()
    }

    mutating func removeApproval(toolID: String) {
        pendingApprovals.removeAll { $0.toolID == toolID }
    }

    mutating func clearReloadRequest() {
        needsHistoryReload = false
    }

    mutating func apply(_ event: StreamEvent) {
        updateLatestSeq(event)

        switch event.type {
        case "RUN_STARTED":
            activeRunID = event.runID ?? activeRunID

        case "RUN_FINISHED":
            clearActiveRunIfCurrent(event.runID)

        case "run_cancelled":
            clearActiveRunIfCurrent(event.runID)
            appendActivity("Run cancelled", id: "cancelled-\(event.runID ?? UUID().uuidString)")

        case "RUN_ERROR":
            clearActiveRunIfCurrent(event.runID)
            appendError(event.message ?? "Run failed", id: "error-\(event.runID ?? UUID().uuidString)")

        case "TEXT_MESSAGE_START":
            startTextMessage(event)

        case "TEXT_MESSAGE_CONTENT":
            appendTextDelta(event)

        case "TEXT_MESSAGE_END":
            finishTextMessage(event)

        case "TOOL_CALL_START":
            upsertToolMessage(event, isStreaming: true)

        case "TOOL_CALL_END":
            finishToolMessage(event)

        case "TOOL_CALL_RESULT":
            upsertToolMessage(event, isStreaming: false)

        case "approval_needed":
            addApproval(event)

        case "workflow_started":
            reduceWorkflowStarted(event)

        case "workflow_finished":
            reduceWorkflowFinished(event)

        case "task_started":
            reduceTaskEvent(event, kind: .started)

        case "task_progress":
            reduceTaskEvent(event, kind: .progress)

        case "task_finished":
            reduceTaskEvent(event, kind: .finished)

        case "background_task":
            reduceBackgroundTask(event)

        case "token_usage":
            reduceTokenUsage(event)

        case "run_backgrounded":
            break

        case "stream_reset":
            needsHistoryReload = true

        default:
            break
        }
    }

    private mutating func updateLatestSeq(_ event: StreamEvent) {
        if let seq = event.latestSeq ?? event.seq {
            latestSeq = max(latestSeq ?? seq, seq)
        }
    }

    private mutating func clearActiveRunIfCurrent(_ runID: String?) {
        if runID == nil || activeRunID == nil || runID == activeRunID {
            activeRunID = nil
            pendingApprovals.removeAll { approval in
                approval.runID == nil || approval.runID == runID
            }
        }
    }

    private mutating func startTextMessage(_ event: StreamEvent) {
        let id = event.messageID ?? fallbackMessageID(for: event)
        guard messageIndex(id: id) == nil else { return }
        messages.append(MobileMessage(id: id, role: MobileMessageRole(serverRole: event.role), content: "", isStreaming: true))
    }

    private mutating func appendTextDelta(_ event: StreamEvent) {
        let id = event.messageID ?? fallbackMessageID(for: event)
        if let index = messageIndex(id: id) {
            messages[index].content += event.delta ?? ""
            messages[index].isStreaming = true
        } else {
            messages.append(MobileMessage(id: id, role: .assistant, content: event.delta ?? "", isStreaming: true))
        }
    }

    private mutating func finishTextMessage(_ event: StreamEvent) {
        let id = event.messageID ?? fallbackMessageID(for: event)
        if let index = messageIndex(id: id) {
            if let content = event.content {
                messages[index].content = content
            }
            messages[index].isStreaming = false
        }
    }

    private mutating func upsertToolMessage(_ event: StreamEvent, isStreaming: Bool) {
        let id = event.toolCallID ?? event.toolID ?? "tool-\(event.seq ?? Int.random(in: 0...999_999))"
        let title = event.displayName ?? event.toolCallName ?? event.name ?? "Tool"
        let content = event.content ?? event.preview ?? event.contentPreview ?? event.path ?? ""
        let message = MobileMessage(id: id, role: .tool, content: content, detail: title, isStreaming: isStreaming)
        if let index = messageIndex(id: id) {
            messages[index] = message
        } else {
            messages.append(message)
        }
    }

    private mutating func finishToolMessage(_ event: StreamEvent) {
        guard let id = event.toolCallID ?? event.toolID, let index = messageIndex(id: id) else { return }
        messages[index].isStreaming = false
    }

    private mutating func addApproval(_ event: StreamEvent) {
        guard let toolID = event.toolID else { return }
        let approval = PendingApproval(
            toolID: toolID,
            toolName: event.displayName ?? event.name ?? event.toolCallName ?? "Tool",
            preview: event.contentPreview ?? event.preview ?? event.path,
            diff: event.diff,
            runID: event.runID ?? activeRunID
        )
        if let index = pendingApprovals.firstIndex(where: { $0.toolID == toolID }) {
            pendingApprovals[index] = approval
        } else {
            pendingApprovals.append(approval)
        }
    }

    mutating func appendActivity(_ content: String, id: String) {
        messages.append(MobileMessage(id: id, role: .activity, content: content))
    }

    mutating func appendError(_ content: String, id: String) {
        messages.append(MobileMessage(id: id, role: .error, content: content))
    }

    private func messageIndex(id: String) -> Int? {
        messages.firstIndex { $0.id == id }
    }

    private func fallbackMessageID(for event: StreamEvent) -> String {
        "message-\(event.runID ?? activeRunID ?? "local")-\(event.seq ?? 0)"
    }
}

// MARK: - Live workflow / subagent domain

// Internal accumulators built from the event stream. They mirror the desktop
// workflow-domain + background-agent-domain reducers: workflows nest
// phases → agents; standalone tasks are a flat agent list. Derived state
// (phase status, durations, totals) is computed at projection time, not cached.
extension MobileTranscript {
    private enum TaskKind { case started, progress, finished }

    private struct LiveAgent: Equatable {
        var taskId: String
        var name: String?
        var agentType: String?
        var command: String?
        var detail: String?
        var state: RunState
        var startedAt: Date
        var completedAt: Date?
        var detached: Bool
        var promptTokens: Int
        var completionTokens: Int
        var totalTokens: Int
        var hasTokens: Bool
        var lastTokenSeq: Int?
    }

    private struct LivePhase: Equatable {
        var name: String
        var agentOrder: [String]
        var agentsByTaskId: [String: LiveAgent]
    }

    private struct LiveWorkflow: Equatable {
        var workflowId: String
        var name: String?
        var status: RunState
        var phaseOrder: [String]
        var phasesByName: [String: LivePhase]
        var startedAt: Date
        var completedAt: Date?
    }

    // MARK: Reducers

    private mutating func reduceWorkflowStarted(_ event: StreamEvent) {
        guard let workflowID = event.workflowID else { return }
        let prev = workflows[workflowID]
        var phaseOrder = prev?.phaseOrder ?? []
        var phasesByName = prev?.phasesByName ?? [:]
        // Seed declared phases as empty (render pending) — only on the fresh-row
        // case; a replayed started over a live-built row keeps the built phases.
        if prev == nil, let declared = event.phases {
            for name in declared where phasesByName[name] == nil {
                phasesByName[name] = LivePhase(name: name, agentOrder: [], agentsByTaskId: [:])
                phaseOrder.append(name)
            }
        }
        workflows[workflowID] = LiveWorkflow(
            workflowId: workflowID,
            name: event.name ?? prev?.name,
            status: prev?.status ?? .running,
            phaseOrder: phaseOrder,
            phasesByName: phasesByName,
            startedAt: prev?.startedAt ?? Date(),
            completedAt: prev?.completedAt
        )
        projectWorkflow(workflowID)
    }

    private mutating func reduceWorkflowFinished(_ event: StreamEvent) {
        guard let workflowID = event.workflowID, var workflow = workflows[workflowID] else { return }
        // Prune declared phases that never got an agent — they'd read as eternally
        // pending on a settled run.
        workflow.phaseOrder = workflow.phaseOrder.filter { name in
            !(workflow.phasesByName[name]?.agentsByTaskId.isEmpty ?? true)
        }
        workflow.phasesByName = workflow.phasesByName.filter { !$0.value.agentsByTaskId.isEmpty }
        workflow.status = runState(fromStatus: event.status, fallback: .completed)
        workflow.completedAt = Date()
        workflows[workflowID] = workflow
        projectWorkflow(workflowID)
    }

    private mutating func reduceTaskEvent(_ event: StreamEvent, kind: TaskKind) {
        // Workflow-tagged tasks feed the workflow card; untagged tasks are
        // standalone subagents. workflow_id presence is the discriminator.
        if let workflowID = event.workflowID {
            reduceWorkflowTask(event, workflowID: workflowID, kind: kind)
        } else {
            reduceStandaloneTask(event, kind: kind)
        }
    }

    private mutating func reduceWorkflowTask(_ event: StreamEvent, workflowID: String, kind: TaskKind) {
        guard var workflow = workflows[workflowID] else { return }
        let taskID = event.childRunID ?? event.taskID ?? ""
        guard !taskID.isEmpty else { return }
        let phaseName = event.phase ?? "default"

        var phase = workflow.phasesByName[phaseName] ?? LivePhase(name: phaseName, agentOrder: [], agentsByTaskId: [:])
        if workflow.phasesByName[phaseName] == nil {
            workflow.phaseOrder.append(phaseName)
        }

        let prevAgent = phase.agentsByTaskId[taskID]
        var agent = prevAgent ?? makeEmptyAgent(taskID: taskID)
        agent.name = event.name ?? prevAgent?.name
        agent.agentType = event.agentType ?? prevAgent?.agentType
        agent.state = nextTaskState(kind: kind, status: event.status, prev: prevAgent?.state)
        if kind == .finished {
            agent.completedAt = Date()
        }
        if phase.agentsByTaskId[taskID] == nil {
            phase.agentOrder.append(taskID)
        }
        phase.agentsByTaskId[taskID] = agent
        workflow.phasesByName[phaseName] = phase
        workflows[workflowID] = workflow
        projectWorkflow(workflowID)
    }

    private mutating func reduceStandaloneTask(_ event: StreamEvent, kind: TaskKind) {
        let taskID = event.childRunID ?? event.taskID ?? ""
        guard !taskID.isEmpty else { return }
        let prev = standaloneAgents[taskID]
        var agent = prev ?? makeEmptyAgent(taskID: taskID)
        agent.name = event.name ?? prev?.name
        agent.agentType = event.agentType ?? prev?.agentType
        agent.detail = event.summary ?? prev?.detail
        agent.state = nextTaskState(kind: kind, status: event.status, prev: prev?.state)
        if let wait = event.wait { agent.detached = !wait }
        if kind == .finished {
            agent.completedAt = Date()
        }
        upsertStandalone(taskID: taskID, agent: agent)
    }

    private mutating func reduceBackgroundTask(_ event: StreamEvent) {
        let taskID = event.childRunID ?? event.taskID ?? ""
        guard !taskID.isEmpty else { return }
        let prev = standaloneAgents[taskID]
        var agent = prev ?? makeEmptyAgent(taskID: taskID)
        agent.command = event.command ?? prev?.command
        agent.agentType = event.agentType ?? prev?.agentType
        agent.detail = event.detail ?? prev?.detail
        agent.state = runState(fromStatus: event.status, fallback: .running)
        if let wait = event.wait { agent.detached = !wait }
        if !agent.state.isActive {
            agent.completedAt = agent.completedAt ?? Date()
        }
        upsertStandalone(taskID: taskID, agent: agent)
    }

    private mutating func reduceTokenUsage(_ event: StreamEvent) {
        // Only workflow-scoped, task-tagged usage lands on an agent. Run-level
        // usage (no workflow_id/task_id) is the session budget and is ignored here.
        guard let workflowID = event.workflowID,
              var workflow = workflows[workflowID],
              let usage = event.usage else { return }
        let taskID = event.childRunID ?? event.taskID ?? ""
        guard !taskID.isEmpty else { return }
        let phaseName = event.phase ?? "default"
        guard var phase = workflow.phasesByName[phaseName],
              var agent = phase.agentsByTaskId[taskID] else { return }

        // Token spend ACCUMULATES, so a replayed seq must be skipped (it would
        // double Σ on a session revisit). seq is the dedupe key.
        if let seq = event.seq, let last = agent.lastTokenSeq, seq <= last { return }

        let prompt = usage.prompt ?? 0
        let completion = usage.completion ?? 0
        let total = usage.total ?? (prompt + completion)
        agent.promptTokens += prompt
        agent.completionTokens += completion
        agent.totalTokens += total
        agent.hasTokens = true
        agent.lastTokenSeq = event.seq ?? agent.lastTokenSeq
        phase.agentsByTaskId[taskID] = agent
        workflow.phasesByName[phaseName] = phase
        workflows[workflowID] = workflow
        projectWorkflow(workflowID)
    }

    // MARK: Domain helpers

    private func makeEmptyAgent(taskID: String) -> LiveAgent {
        LiveAgent(
            taskId: taskID, name: nil, agentType: nil, command: nil, detail: nil,
            state: .running, startedAt: Date(), completedAt: nil, detached: false,
            promptTokens: 0, completionTokens: 0, totalTokens: 0, hasTokens: false,
            lastTokenSeq: nil
        )
    }

    private mutating func upsertStandalone(taskID: String, agent: LiveAgent) {
        if standaloneRunID == nil {
            standaloneRunID = activeRunID ?? taskID
        }
        if standaloneAgents[taskID] == nil {
            standaloneOrder.append(taskID)
        }
        standaloneAgents[taskID] = agent
        projectStandalone()
    }

    private func nextTaskState(kind: TaskKind, status: String?, prev: RunState?) -> RunState {
        if kind == .finished {
            return runState(fromStatus: status, fallback: .completed)
        }
        if status == "failed" { return .failed }
        if status == "cancelled" { return .cancelled }
        // Sticky running: a progress event without a terminal status keeps it live.
        return prev.map { $0.isActive ? .running : $0 } ?? .running
    }

    // Server status string → RunState. Mirrors normalizeBackgroundAgentStatus:
    // anything not in the valid set falls back to the caller's default.
    private func runState(fromStatus status: String?, fallback: RunState) -> RunState {
        switch status {
        case "completed": return .completed
        case "failed": return .failed
        case "cancelled": return .cancelled
        case "interrupted": return .failed
        case "cancel_requested": return .running
        case "running", "started", "activity": return .running
        case "pending": return .pending
        case "waiting": return .waiting
        default: return fallback
        }
    }

    // MARK: Projection — build the rich models and upsert the stable-id messages

    private mutating func projectWorkflow(_ workflowID: String) {
        guard let workflow = workflows[workflowID] else { return }
        let phases: [MockWorkflowPhase] = workflow.phaseOrder.compactMap { phaseName in
            guard let phase = workflow.phasesByName[phaseName] else { return nil }
            let agents: [MockWorkflowAgent] = phase.agentOrder.compactMap { taskID in
                guard let agent = phase.agentsByTaskId[taskID] else { return nil }
                return MockWorkflowAgent(
                    id: agent.taskId,
                    name: agentLabel(agent),
                    state: agent.state,
                    tokens: agent.hasTokens ? Self.formatTokens(agent.totalTokens) : nil,
                    elapsed: Self.formatElapsed(from: agent.startedAt, to: agent.completedAt)
                )
            }
            return MockWorkflowPhase(
                id: phaseName,
                name: phaseName,
                state: derivePhaseState(phase),
                agents: agents
            )
        }

        let totalTokens = workflow.phasesByName.values
            .flatMap { $0.agentsByTaskId.values }
            .reduce(0) { $0 + $1.totalTokens }

        let mock = MockWorkflow(
            id: workflow.workflowId,
            name: workflow.name ?? "Workflow",
            state: workflow.status,
            elapsed: Self.formatElapsed(from: workflow.startedAt, to: workflow.completedAt),
            tokens: Self.formatTokens(totalTokens),
            phases: phases
        )
        upsertWorkflowMessage(id: "workflow-\(workflow.workflowId)", mock)
    }

    private mutating func projectStandalone() {
        guard let runID = standaloneRunID else { return }
        let agents: [MockSubagent] = standaloneOrder.compactMap { taskID in
            guard let agent = standaloneAgents[taskID] else { return nil }
            return MockSubagent(
                id: agent.taskId,
                type: humanize(agent.agentType),
                name: agentLabel(agent),
                state: agent.state,
                detail: agent.detail ?? "",
                elapsed: Self.formatElapsed(from: agent.startedAt, to: agent.completedAt),
                detached: agent.detached
            )
        }
        guard !agents.isEmpty else { return }
        upsertSubagentsMessage(id: "subagents-\(runID)", agents)
    }

    private func derivePhaseState(_ phase: LivePhase) -> RunState {
        let states = phase.agentOrder.compactMap { phase.agentsByTaskId[$0]?.state }
        if states.isEmpty { return .pending }
        if states.contains(.running) || states.contains(.waiting) || states.contains(.pending) {
            return .running
        }
        if states.contains(.failed) { return .failed }
        if states.allSatisfy({ $0 == .cancelled }) { return .cancelled }
        return .completed
    }

    private func agentLabel(_ agent: LiveAgent) -> String {
        agent.name ?? agent.command ?? agent.detail ?? humanize(agent.agentType)
    }

    private func humanize(_ agentType: String?) -> String {
        guard let raw = agentType, !raw.isEmpty else { return "Agent" }
        return raw
            .replacingOccurrences(of: "_", with: " ")
            .replacingOccurrences(of: "-", with: " ")
            .split(separator: " ")
            .map { $0.prefix(1).uppercased() + $0.dropFirst() }
            .joined(separator: " ")
    }

    // MARK: Stable-id upserts

    private mutating func upsertWorkflowMessage(id: String, _ workflow: MockWorkflow) {
        if let index = messageIndex(id: id) {
            messages[index].workflow = workflow
        } else {
            var message = MobileMessage(id: id, role: .workflow, content: "")
            message.workflow = workflow
            messages.append(message)
        }
    }

    private mutating func upsertSubagentsMessage(id: String, _ agents: [MockSubagent]) {
        if let index = messageIndex(id: id) {
            messages[index].subagents = agents
        } else {
            var message = MobileMessage(id: id, role: .subagents, content: "")
            message.subagents = agents
            messages.append(message)
        }
    }

    // MARK: Formatting

    private static func formatTokens(_ total: Int) -> String {
        if total >= 1_000_000 {
            return String(format: "%.1fM", Double(total) / 1_000_000)
        }
        if total >= 1_000 {
            return String(format: "%.1fk", Double(total) / 1_000)
        }
        return "\(total)"
    }

    private static func formatElapsed(from start: Date, to end: Date?) -> String {
        let seconds = Int((end ?? Date()).timeIntervalSince(start).rounded())
        let clamped = max(0, seconds)
        if clamped < 60 { return "\(clamped)s" }
        let minutes = clamped / 60
        let rem = clamped % 60
        return rem == 0 ? "\(minutes)m" : "\(minutes)m \(rem)s"
    }
}
