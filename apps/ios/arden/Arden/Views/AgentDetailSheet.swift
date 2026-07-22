import SwiftUI

// Full-screen detail for a spawned subagent — the sheet you push into from a
// SubagentRow. Header card carries the agent's identity (type tag, state badge,
// model, elapsed); below it the result (if settled) and the run trace timeline.
// Direction B: flat, one accent, near-black Done is not needed (plain tinted),
// SF Mono for technical metadata, grouped rounded cards on canvas.

struct AgentDetailSheet: View {
    let agent: MockSubagent

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    headerCard

                    if let result = agent.result, !result.isEmpty {
                        section(title: "Result") {
                            resultBody(result)
                        }
                    }

                    if !agent.trace.isEmpty {
                        section(title: "Trace") {
                            RunTraceView(events: agent.trace)
                                .padding(.horizontal, 14)
                                .padding(.vertical, 6)
                        }
                    }
                }
                .padding(.horizontal, 16)
                .padding(.top, 12)
                .padding(.bottom, 32)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(Theme.canvas)
            .navigationTitle(agent.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                        .font(.system(size: 17, weight: .semibold))
                        .tint(Theme.accent)
                }
            }
        }
    }

    // MARK: - Header

    private var headerCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(agent.type.uppercased())
                    .font(Theme.mono(11, weight: .medium))
                    .tracking(0.4)
                    .foregroundStyle(agent.state == .running ? Theme.accent : Theme.textSecondary)
                    .lineLimit(1)

                Spacer(minLength: 8)

                stateBadge
            }

            if !agent.detail.isEmpty {
                Text(agent.detail)
                    .font(.system(size: 14))
                    .foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Hairline()

            HStack(spacing: 16) {
                if let model = agent.model {
                    metaItem(icon: "cpu", value: model)
                }
                metaItem(icon: "clock", value: agent.elapsed)
                if agent.detached {
                    metaItem(icon: "arrow.up.forward.app", value: "detached")
                }
                Spacer(minLength: 0)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(Theme.surface)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .strokeBorder(Theme.groupOutline, lineWidth: 0.5)
        )
    }

    private var stateBadge: some View {
        HStack(spacing: 5) {
            if agent.state == .running {
                TimelineView(.periodic(from: Date(), by: 1.0 / 15.0)) { context in
                    let p = StatusPulse.phase(context.date)
                    Circle()
                        .fill(agent.state.color)
                        .frame(width: 6, height: 6)
                        .opacity(0.5 + 0.5 * (1 - p))
                }
            } else {
                Image(systemName: agent.state.symbol)
                    .font(.system(size: 10, weight: .bold))
            }
            Text(agent.state.label)
                .font(.system(size: 12, weight: .semibold))
        }
        .foregroundStyle(agent.state.color)
        .padding(.horizontal, 9)
        .padding(.vertical, 5)
        .background(Capsule().fill(agent.state.color.opacity(0.12)))
    }

    private func metaItem(icon: String, value: String) -> some View {
        HStack(spacing: 5) {
            Image(systemName: icon)
                .font(.system(size: 11, weight: .regular))
                .foregroundStyle(Theme.textTertiary)
            Text(value)
                .font(Theme.mono(12))
                .foregroundStyle(Theme.textSecondary)
                .lineLimit(1)
        }
    }

    // MARK: - Result

    @ViewBuilder
    private func resultBody(_ markdown: String) -> some View {
        Text(attributed(markdown))
            .font(.system(size: 15))
            .foregroundStyle(Theme.textPrimary)
            .textSelection(.enabled)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(Theme.surface)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .strokeBorder(Theme.groupOutline, lineWidth: 0.5)
            )
    }

    private func attributed(_ markdown: String) -> AttributedString {
        let options = AttributedString.MarkdownParsingOptions(
            interpretedSyntax: .inlineOnlyPreservingWhitespace
        )
        return (try? AttributedString(markdown: markdown, options: options))
            ?? AttributedString(markdown)
    }

    // MARK: - Section scaffold

    @ViewBuilder
    private func section<Content: View>(
        title: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title.uppercased())
                .font(.system(size: 12, weight: .semibold))
                .tracking(0.4)
                .foregroundStyle(Theme.textTertiary)
                .padding(.leading, 4)

            if title == "Trace" {
                content()
                    .background(
                        RoundedRectangle(cornerRadius: 16, style: .continuous)
                            .fill(Theme.surface)
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 16, style: .continuous)
                            .strokeBorder(Theme.groupOutline, lineWidth: 0.5)
                    )
            } else {
                content()
            }
        }
    }
}

// MARK: - Preview

#Preview("Agent detail") {
    let trace = [
        MockRunEvent(id: "1", kind: .thinking, title: "Scoping the audit",
                     detail: "Enumerate auth entry points, then check rate-limits and token TTLs.",
                     at: "0.0s", duration: "1.1s", state: .completed),
        MockRunEvent(id: "2", kind: .tool, title: "grep",
                     detail: "\"def login\" apps/server/arden",
                     at: "1.1s", duration: "0.2s", state: .completed),
        MockRunEvent(id: "3", kind: .tool, title: "read_file",
                     detail: "apps/server/arden/server/app.py",
                     at: "1.3s", duration: "0.1s", state: .completed),
        MockRunEvent(id: "4", kind: .error, title: "missing guard",
                     detail: "no rate-limit decorator on /login",
                     at: "2.0s", duration: nil, state: .failed),
        MockRunEvent(id: "5", kind: .result, title: "Findings",
                     detail: "2 issues filed.",
                     at: "12.4s", duration: nil, state: .completed),
    ]

    let agent = MockSubagent(
        id: "1",
        type: "Code review",
        name: "Audit auth middleware",
        state: .completed,
        detail: "Reviewed login, session refresh, and token issuance paths.",
        elapsed: "2m 10s",
        detached: true,
        model: "Sonnet 4.6 · Medium",
        result: """
        Found **2 issues**:

        1. Missing rate-limit on `/login` — brute-force exposure.
        2. Token TTL set to 30d; recommend 24h with refresh rotation.

        Both are low-risk to fix and covered by existing tests.
        """,
        trace: trace
    )

    return Color.clear
        .sheet(isPresented: .constant(true)) {
            AgentDetailSheet(agent: agent)
        }
}
