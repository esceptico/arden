import SwiftUI

// In-transcript element rendering a CHAIN of sequential tool calls as one
// grouped unit. Distinct from the single hairline-bracketed tool row in
// ChatView: here the signature is the vertical hairline connector running
// between step markers, implying sequence. Direction B — flat, one accent,
// SF Mono for technical metadata, hairline dividers, grouped rounded card.

struct ToolChainView: View {
    let steps: [MockToolStep]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            header
            card
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: 6) {
            Image(systemName: "link")
                .font(.system(size: 11, weight: .regular))
                .foregroundStyle(Theme.textTertiary)

            Text("\(steps.count) tool \(steps.count == 1 ? "call" : "calls")")
                .font(Theme.mono(12))
                .foregroundStyle(Theme.textTertiary)
        }
        .padding(.leading, 2)
    }

    // MARK: - Grouped card

    private var card: some View {
        VStack(spacing: 0) {
            ForEach(Array(steps.enumerated()), id: \.element.id) { index, step in
                ToolChainRow(
                    step: step,
                    isFirst: index == 0,
                    isLast: index == steps.count - 1
                )

                if index != steps.count - 1 {
                    Hairline()
                        .padding(.leading, ToolChainMetrics.dividerInset)
                }
            }
        }
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(Theme.surface)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(Theme.groupOutline, lineWidth: 0.5)
        )
    }
}

// MARK: - Layout constants

private enum ToolChainMetrics {
    static let railWidth: CGFloat = 22       // gutter that holds the marker + connector
    static let markerSize: CGFloat = 7       // step dot diameter
    static let rowVerticalPadding: CGFloat = 9
    static let rowHorizontalPadding: CGFloat = 13
    // Divider starts after the rail so the connector reads as continuous.
    static var dividerInset: CGFloat { rowHorizontalPadding + railWidth + 4 }
}

// MARK: - Step row

private struct ToolChainRow: View {
    let step: MockToolStep
    let isFirst: Bool
    let isLast: Bool
    @State private var showingDetail = false

    var body: some View {
        HStack(alignment: .center, spacing: 4) {
            connectorRail
            content
            trailing
        }
        .padding(.vertical, ToolChainMetrics.rowVerticalPadding)
        .padding(.horizontal, ToolChainMetrics.rowHorizontalPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
        .onTapGesture { showingDetail = true }
        .sheet(isPresented: $showingDetail) {
            ToolDetailSheet(name: step.name, command: step.detail, output: step.output, diff: step.diff)
        }
    }

    // The signature of a "chain": a thin vertical hairline segment passing
    // through each marker, joining consecutive steps. Not a decorative
    // full-height rail — it's a step connector, suppressed at the ends.
    private var connectorRail: some View {
        ZStack {
            Rectangle()
                .fill(Theme.sep)
                .frame(width: 1)
                .frame(maxHeight: .infinity)
                .padding(.top, isFirst ? ToolChainMetrics.railWidth / 2 : 0)
                .padding(.bottom, isLast ? ToolChainMetrics.railWidth / 2 : 0)

            marker
        }
        .frame(width: ToolChainMetrics.railWidth)
    }

    private var marker: some View {
        ZStack {
            // Running shows a small pulsing dot (not a spinner): same size as the
            // settled dots so the moat keeps a clean gap from the connector, and
            // it rides the shared 15fps clock like the other running indicators.
            if step.state == .running {
                TimelineView(.periodic(from: Date(), by: 1.0 / 15.0)) { context in
                    let p = StatusPulse.phase(context.date)
                    Circle()
                        .stroke(step.state.color, lineWidth: 1.5)
                        .frame(width: ToolChainMetrics.markerSize, height: ToolChainMetrics.markerSize)
                        .scaleEffect(1 + p * 1.1)
                        .opacity(0.5 * (1 - p))
                }
            }
            Circle()
                .fill(step.state.color)
                .frame(width: ToolChainMetrics.markerSize, height: ToolChainMetrics.markerSize)
        }
        // Solid surface "moat" fully masks the connector around the marker — a
        // stroked ring left a 1pt gap where the line poked out of the dot.
        .frame(width: ToolChainMetrics.markerSize + 7, height: ToolChainMetrics.markerSize + 7)
        .background(Circle().fill(Theme.surface))
    }

    private var content: some View {
        HStack(spacing: 7) {
            Text(step.name)
                .font(Theme.mono(13, weight: .semibold))
                .foregroundStyle(Theme.textPrimary)
                .layoutPriority(1)
                .lineLimit(1)

            Text(step.detail)
                .font(Theme.mono(13))
                .foregroundStyle(Theme.textSecondary)
                .lineLimit(1)
                .truncationMode(.tail)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    @ViewBuilder
    private var trailing: some View {
        switch step.state {
        case .completed:
            HStack(spacing: 6) {
                if let duration = step.duration {
                    Text(duration)
                        .font(Theme.mono(12))
                        .foregroundStyle(Theme.textTertiary)
                }
                Image(systemName: "checkmark")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(Theme.success)
            }
        case .running:
            HStack(spacing: 6) {
                ProgressView()
                    .controlSize(.mini)
                Text("running")
                    .font(Theme.mono(12))
                    .foregroundStyle(Theme.textSecondary)
            }
        case .failed:
            Image(systemName: "xmark")
                .font(.system(size: 11, weight: .bold))
                .foregroundStyle(Theme.destructive)
        case .cancelled, .pending, .waiting:
            Image(systemName: step.state.symbol)
                .font(.system(size: 11, weight: .regular))
                .foregroundStyle(Theme.textTertiary)
        }
    }
}

// MARK: - Preview

#Preview("Tool chain") {
    let steps = [
        MockToolStep(id: "1", name: "read_file", detail: "arden/core/agent.py", state: .completed, duration: "0.1s"),
        MockToolStep(id: "2", name: "bash", detail: "uv run pytest tests/test_tools.py -q", state: .completed, duration: "4.2s"),
        MockToolStep(id: "3", name: "grep", detail: "\"async def execute\" arden/tools", state: .completed, duration: "0.3s"),
        MockToolStep(id: "4", name: "edit_file", detail: "arden/tools/research.py", state: .running, duration: nil),
        MockToolStep(id: "5", name: "bash", detail: "ruff check .", state: .pending, duration: nil),
    ]

    return ScrollView {
        ToolChainView(steps: steps)
            .padding(20)
    }
    .background(Theme.doc)
}
