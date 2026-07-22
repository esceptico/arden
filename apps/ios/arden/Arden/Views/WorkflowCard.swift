import SwiftUI

// An in-transcript expandable card showing Orchestra multi-agent progress —
// the iOS sibling of the desktop WorkflowProgressCard + WorkflowDetail. Identity
// + status + meta up top, a segmented phase bar (one segment per phase), then the
// phases → agents tree, expanded by default. Direction B: flat, one accent, SF
// Mono for technical metadata, hairline dividers, no decorative rails — status
// rides on small dots, segment color, and the badge.
struct WorkflowCard: View {
    let workflow: MockWorkflow

    // Default OPEN: this is an inline progress readout, the agents are the point.
    @State private var expandedPhases: Set<String>

    init(workflow: MockWorkflow) {
        self.workflow = workflow
        _expandedPhases = State(initialValue: Set(workflow.phases.map(\.id)))
    }

    private var isRunning: Bool { workflow.state.isActive }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            metaLine
            segmentBar
            phaseList
        }
        .padding(14)
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(Theme.groupOutline, lineWidth: 1)
        )
    }

    // MARK: Header

    private var header: some View {
        HStack(spacing: 8) {
            Image(systemName: "point.3.connected.trianglepath.dotted")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(isRunning ? Theme.accent : Theme.textTertiary)

            Text(workflow.name)
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(Theme.textPrimary)
                .lineLimit(1)
                .truncationMode(.tail)

            Spacer(minLength: 8)

            StatusBadge(state: workflow.state)
        }
    }

    private var metaLine: some View {
        HStack(spacing: 6) {
            Text("Σ \(workflow.tokens)")
            metaDot
            Text(workflow.elapsed)
            metaDot
            Text("\(workflow.settledAgents)/\(workflow.totalAgents) agents")
        }
        .font(Theme.mono(12))
        .foregroundStyle(Theme.textSecondary)
        .lineLimit(1)
    }

    private var metaDot: some View {
        Text("·").foregroundStyle(Theme.textTertiary)
    }

    // MARK: Phase segment bar

    private var segmentBar: some View {
        HStack(spacing: 3) {
            ForEach(workflow.phases) { phase in
                RoundedRectangle(cornerRadius: 1.5, style: .continuous)
                    .fill(phase.state == .pending ? Theme.sep : phase.state.color)
                    .frame(maxWidth: .infinity)
                    .frame(height: 3)
            }
        }
    }

    // MARK: Phases → agents

    private var phaseList: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(workflow.phases.enumerated()), id: \.element.id) { index, phase in
                if index > 0 {
                    Hairline().padding(.vertical, 8)
                }
                PhaseSection(
                    phase: phase,
                    isExpanded: expandedPhases.contains(phase.id),
                    toggle: { togglePhase(phase.id) }
                )
            }
        }
    }

    private func togglePhase(_ id: String) {
        withAnimation(.spring(response: 0.3, dampingFraction: 0.86)) {
            if expandedPhases.contains(id) {
                expandedPhases.remove(id)
            } else {
                expandedPhases.insert(id)
            }
        }
    }
}

// MARK: - Status badge

private struct StatusBadge: View {
    let state: RunState

    var body: some View {
        Text(state.label)
            .font(Theme.mono(12, weight: .medium))
            .foregroundStyle(state.color)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(state.color.opacity(0.12))
            .clipShape(Capsule(style: .continuous))
    }
}

// MARK: - Phase section

private struct PhaseSection: View {
    let phase: MockWorkflowPhase
    let isExpanded: Bool
    let toggle: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: toggle) {
                phaseHeader
            }
            .buttonStyle(PressScaleButtonStyle())

            if isExpanded {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(phase.agents) { agent in
                        AgentRow(agent: agent)
                    }
                }
                .padding(.top, 4)
                .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
    }

    private var phaseHeader: some View {
        HStack(spacing: 8) {
            Image(systemName: "chevron.right")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Theme.textTertiary)
                .rotationEffect(.degrees(isExpanded ? 90 : 0))

            Text(phase.name)
                .font(Theme.mono(13))
                .foregroundStyle(Theme.textPrimary)
                .lineLimit(1)

            DotSparkline(agents: phase.agents)

            Spacer(minLength: 6)

            Text("\(phase.agents.count)")
                .font(Theme.mono(12))
                .foregroundStyle(Theme.textTertiary)
        }
        .contentShape(Rectangle())
    }
}

// MARK: - Dot sparkline (one dot per agent, colored by state)

private struct DotSparkline: View {
    let agents: [MockWorkflowAgent]

    var body: some View {
        HStack(spacing: 3) {
            ForEach(agents) { agent in
                Circle()
                    .fill(agent.state.color)
                    .frame(width: 4, height: 4)
            }
        }
    }
}

// MARK: - Agent row

private struct AgentRow: View {
    let agent: MockWorkflowAgent

    var body: some View {
        HStack(spacing: 8) {
            StatusDot(state: agent.state)

            Text(agent.name)
                .font(.system(size: 14))
                .foregroundStyle(agent.state == .failed ? Theme.destructive : Theme.textPrimary)
                .lineLimit(1)
                .truncationMode(.tail)

            Spacer(minLength: 8)

            HStack(spacing: 8) {
                if let tokens = agent.tokens {
                    Text(tokens)
                }
                if let elapsed = agent.elapsed {
                    Text(elapsed)
                }
            }
            .font(Theme.mono(12))
            .foregroundStyle(Theme.textSecondary)
        }
        .padding(.leading, 22)
        .padding(.vertical, 4)
    }
}

// MARK: - Status dot (breathing halo while running)

private struct StatusDot: View {
    let state: RunState

    private var isRunning: Bool { state == .running || state == .waiting }

    var body: some View {
        ZStack {
            if isRunning {
                TimelineView(.periodic(from: Date(), by: 1.0 / 15.0)) { context in
                    let p = StatusPulse.phase(context.date)
                    Circle()
                        .stroke(state.color, lineWidth: 1.5)
                        .frame(width: 6, height: 6)
                        .scaleEffect(1 + p * 1.2)
                        .opacity(0.5 * (1 - p))
                }
            }
            Circle()
                .fill(state.color)
                .frame(width: 6, height: 6)
        }
        .frame(width: 6, height: 6)
    }
}

#Preview {
    let workflow = MockWorkflow(
        id: "wf1",
        name: "Refactor memory subsystem",
        state: .running,
        elapsed: "1m 12s",
        tokens: "12.4k",
        phases: [
            MockWorkflowPhase(
                id: "p1",
                name: "explore",
                state: .completed,
                agents: [
                    MockWorkflowAgent(id: "a1", name: "Map facts module", state: .completed, tokens: "3.3k", elapsed: "45s"),
                    MockWorkflowAgent(id: "a2", name: "Audit retrieval path", state: .completed, tokens: "2.1k", elapsed: "38s"),
                ]
            ),
            MockWorkflowPhase(
                id: "p2",
                name: "implement",
                state: .running,
                agents: [
                    MockWorkflowAgent(id: "a3", name: "Rewrite consolidation loop", state: .running, tokens: "4.0k", elapsed: "29s"),
                    MockWorkflowAgent(id: "a4", name: "Update entity expansion", state: .running, tokens: nil, elapsed: "12s"),
                ]
            ),
            MockWorkflowPhase(
                id: "p3",
                name: "verify",
                state: .pending,
                agents: [
                    MockWorkflowAgent(id: "a5", name: "Run test suite", state: .pending, tokens: nil, elapsed: nil),
                ]
            ),
        ]
    )
    return ScrollView {
        WorkflowCard(workflow: workflow)
            .padding()
    }
    .background(Theme.doc)
}
