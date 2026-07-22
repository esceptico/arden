import Foundation

// Mock-only models for desktop-parity feature surfaces (workflows, subagents,
// tool-call chains, automations). The API is fake right now, so these are built
// directly by MockArdenData and rendered as rich transcript elements / screens.

enum RunState: String, Equatable {
    case running
    case completed
    case failed
    case cancelled
    case pending
    case waiting

    var isActive: Bool { self == .running || self == .pending || self == .waiting }
}

// MARK: - Tool-call chain

struct MockToolStep: Identifiable, Equatable {
    let id: String
    let name: String       // "bash", "read_file"
    let detail: String     // command or path
    let state: RunState
    let duration: String?  // "0.3s"
    var output: String? = nil   // full result text for the detail sheet
    var diff: String? = nil     // optional unified diff
}

// MARK: - Workflow (Orchestra multi-agent)

struct MockWorkflowAgent: Identifiable, Equatable {
    let id: String
    let name: String
    let state: RunState
    let tokens: String?    // "3.3k"
    let elapsed: String?   // "45s"
}

struct MockWorkflowPhase: Identifiable, Equatable {
    let id: String
    let name: String
    let state: RunState
    let agents: [MockWorkflowAgent]
}

struct MockWorkflow: Identifiable, Equatable {
    let id: String
    let name: String
    let state: RunState
    let elapsed: String    // "1m 12s"
    let tokens: String     // "12.4k"
    let phases: [MockWorkflowPhase]

    var totalAgents: Int { phases.reduce(0) { $0 + $1.agents.count } }
    var settledAgents: Int {
        phases.reduce(0) { $0 + $1.agents.filter { !$0.state.isActive }.count }
    }
}

// MARK: - Subagents

struct MockSubagent: Identifiable, Equatable {
    let id: String
    let type: String       // "Research", "Code review"
    let name: String       // task title
    let state: RunState
    let detail: String     // live progress or result snippet
    let elapsed: String    // "45s"
    let detached: Bool
    var model: String? = nil       // e.g. "Sonnet 4.6 · Medium"
    var result: String? = nil      // full result markdown for the detail sheet
    var trace: [MockRunEvent] = [] // the agent's run timeline
}

// MARK: - Run trace (timeline of a run / agent)

struct MockRunEvent: Identifiable, Equatable {
    enum Kind: String { case thinking, tool, message, result, error }

    let id: String
    let kind: Kind
    let title: String       // headline ("read_file", "Thinking", "Final answer")
    let detail: String?     // command/path/snippet under the headline
    let at: String          // relative offset "0.0s"
    let duration: String?   // "1.4s"
    let state: RunState
}

// MARK: - Artifacts

struct MockArtifact: Identifiable, Equatable {
    let id: String
    let title: String
    let kind: String        // "HTML", "React", "Markdown", "SVG"
    let updated: String     // "just now"
    let html: String        // self-contained HTML rendered in a web view
}

// MARK: - Automations

struct MockAutomationRun: Identifiable, Equatable {
    let id: String
    let state: RunState
    let started: String    // "Today, 09:00"
    let duration: String   // "2m 34s"
    let summary: String?   // result or error
}

struct MockAutomation: Identifiable, Equatable {
    let id: String
    var name: String
    var description: String
    var enabled: Bool
    let schedule: String       // "Weekdays · 9:00" (rendered SF Mono)
    let trigger: String        // "time" | "message" | "event"
    let lastState: RunState?
    let lastRun: String?       // "5h ago"
    let nextRun: String?       // "Tomorrow, 9:00"
    let builtin: Bool
    let runs: [MockAutomationRun]
}
