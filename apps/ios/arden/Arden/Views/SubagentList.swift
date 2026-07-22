import SwiftUI

// An in-transcript stack of spawned subagents — the iOS counterpart to the
// desktop right-sidebar agents hub (AgentRunRow / AgentRightSidebar). Each agent
// reads as the same object wherever it appears: leading status dot, a
// type-tagged name, a live/result detail line, and trailing elapsed. Display
// only — calm, flat, recede-not-shout (Direction B).

struct SubagentList: View {
    let agents: [MockSubagent]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            header
            card
        }
    }

    private var header: some View {
        HStack(spacing: 5) {
            Text("Agents")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(Theme.textSecondary)
            Text("· \(agents.count)")
                .font(.system(size: 13))
                .foregroundStyle(Theme.textTertiary)
            Spacer(minLength: 0)
        }
    }

    private var card: some View {
        VStack(spacing: 0) {
            ForEach(Array(agents.enumerated()), id: \.element.id) { index, agent in
                SubagentRow(agent: agent)
                if index < agents.count - 1 {
                    Hairline()
                        .padding(.leading, 22)
                }
            }
        }
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(Theme.groupOutline, lineWidth: 1)
        )
    }
}

private struct SubagentRow: View {
    let agent: MockSubagent
    @State private var showingDetail = false

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            StatusDot(state: agent.state)
                .padding(.top, 5)

            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(agent.type.uppercased())
                        .font(Theme.mono(11, weight: .medium))
                        .tracking(0.4)
                        .foregroundStyle(typeColor)
                        .lineLimit(1)
                        .layoutPriority(1)
                    Text(agent.name)
                        .font(.system(size: 15))
                        .foregroundStyle(Theme.textPrimary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }

                if !agent.detail.isEmpty {
                    Text(agent.detail)
                        .font(.system(size: 13))
                        .foregroundStyle(Theme.textSecondary)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            Spacer(minLength: 8)

            VStack(alignment: .trailing, spacing: 5) {
                Text(agent.elapsed)
                    .font(Theme.mono(12))
                    .foregroundStyle(Theme.textTertiary)
                    .monospacedDigit()
                if agent.detached {
                    Text("detached")
                        .font(Theme.mono(10, weight: .medium))
                        .foregroundStyle(Theme.textTertiary)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Theme.raised)
                        .clipShape(Capsule())
                }
            }
            .padding(.top, 1)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
        .onTapGesture { showingDetail = true }
        .sheet(isPresented: $showingDetail) {
            AgentDetailSheet(agent: agent)
        }
    }

    // Running agents lift the type tag to the accent so the live row reads first;
    // settled rows keep the tag muted and recede.
    private var typeColor: Color {
        agent.state == .running ? Theme.accent : Theme.textSecondary
    }
}

// 8pt status dot with a soft breathing ring while the agent is running.
private struct StatusDot: View {
    let state: RunState

    var body: some View {
        Circle()
            .fill(state.color)
            .frame(width: 8, height: 8)
            .overlay {
                if state == .running {
                    TimelineView(.periodic(from: Date(), by: 1.0 / 15.0)) { context in
                        let p = StatusPulse.phase(context.date)
                        Circle()
                            .stroke(state.color.opacity(0.35), lineWidth: 2)
                            .scaleEffect(1 + p * 0.9)
                            .opacity(0.8 * (1 - p))
                    }
                }
            }
    }
}

#Preview {
    ScrollView {
        SubagentList(agents: [
            MockSubagent(
                id: "1",
                type: "Research",
                name: "Survey vector DB options",
                state: .running,
                detail: "Reading pgvector vs. Qdrant benchmarks; comparing recall at 1M vectors.",
                elapsed: "45s",
                detached: false
            ),
            MockSubagent(
                id: "2",
                type: "Code review",
                name: "Audit auth middleware",
                state: .completed,
                detail: "Found 2 issues: missing rate-limit on /login, token TTL too long.",
                elapsed: "2m 10s",
                detached: true
            ),
            MockSubagent(
                id: "3",
                type: "Build",
                name: "Run iOS test suite",
                state: .failed,
                detail: "SubagentTests.testRowLayout failed — snapshot mismatch.",
                elapsed: "1m 02s",
                detached: false
            ),
            MockSubagent(
                id: "4",
                type: "Index",
                name: "Reindex vault transcripts",
                state: .pending,
                detail: "Queued behind the active research run.",
                elapsed: "—",
                detached: false
            ),
        ])
        .padding(16)
    }
    .background(Theme.canvas)
}
