import SwiftUI

// A vertical run timeline — the chronological record of a single run or agent.
// Each MockRunEvent is a marker on a left rail joined to its neighbours by a
// thin hairline connector (the "this happened, then this" signature). Distinct
// from ToolChainView (a grouped card of sibling tool calls): a trace is a flat
// scroll of mixed-kind events with timing in the trailing gutter. Embeddable —
// no NavigationStack, no card chrome of its own. Direction B: flat, one accent,
// SF Mono for technical metadata, hairline dividers.

struct RunTraceView: View {
    let events: [MockRunEvent]

    var body: some View {
        VStack(spacing: 0) {
            ForEach(Array(events.enumerated()), id: \.element.id) { index, event in
                RunTraceRow(
                    event: event,
                    isFirst: index == 0,
                    isLast: index == events.count - 1
                )
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - Layout constants

private enum RunTraceMetrics {
    static let railWidth: CGFloat = 24       // gutter holding the marker + connector
    static let dotSize: CGFloat = 8          // event marker diameter
    static let rowVerticalPadding: CGFloat = 11
    static let contentSpacing: CGFloat = 10  // rail → content gap
}

// MARK: - Event row

private struct RunTraceRow: View {
    let event: MockRunEvent
    let isFirst: Bool
    let isLast: Bool

    var body: some View {
        HStack(alignment: .top, spacing: RunTraceMetrics.contentSpacing) {
            connectorRail
            content
            Spacer(minLength: 10)
            trailing
        }
        .padding(.vertical, RunTraceMetrics.rowVerticalPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // Thin hairline segment passing through the marker, joining consecutive
    // events. Suppressed above the first and below the last so the line reads
    // as a connector, not a full-height decorative rail.
    private var connectorRail: some View {
        ZStack(alignment: .top) {
            GeometryReader { geo in
                // Segment above the marker (joins to the previous event) and
                // below it (joins to the next), each suppressed at the ends.
                let center = markerCenter
                Rectangle()
                    .fill(Theme.sep)
                    .frame(width: 1)
                    .frame(
                        height: max(0, geo.size.height - (isFirst ? center : 0) - (isLast ? geo.size.height - center : 0))
                    )
                    .offset(x: (RunTraceMetrics.railWidth - 1) / 2, y: isFirst ? center : 0)
            }

            marker
                .padding(.top, markerCenter - moat / 2)
        }
        .frame(width: RunTraceMetrics.railWidth)
    }

    // A solid canvas "moat" disc behind the dot fully masks the connector around
    // the marker — a stroked ring leaves a 1pt gap where the line pokes out.
    private var marker: some View {
        Group {
            if event.state == .running {
                TimelineView(.periodic(from: Date(), by: 1.0 / 15.0)) { context in
                    let p = StatusPulse.phase(context.date)
                    ZStack {
                        Circle()
                            .stroke(event.state.color.opacity(0.35), lineWidth: 2)
                            .scaleEffect(1 + p * 0.9)
                            .opacity(0.8 * (1 - p))
                        Circle()
                            .fill(event.state.color)
                    }
                    .frame(width: RunTraceMetrics.dotSize, height: RunTraceMetrics.dotSize)
                }
            } else {
                Circle()
                    .fill(event.state.color)
                    .frame(width: RunTraceMetrics.dotSize, height: RunTraceMetrics.dotSize)
            }
        }
        .frame(width: moat, height: moat)
        .background(Circle().fill(Theme.canvas))
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Image(systemName: glyph)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(glyphColor)
                    .frame(width: 16)

                Text(event.title)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Theme.textPrimary)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }

            if let detail = event.detail, !detail.isEmpty {
                Text(detail)
                    .font(detailFont)
                    .foregroundStyle(detailColor)
                    .lineLimit(isTechnical ? 2 : 3)
                    .truncationMode(.tail)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                    .padding(.leading, 22) // align under the title, past the glyph
            }
        }
    }

    private var trailing: some View {
        VStack(alignment: .trailing, spacing: 3) {
            Text(event.at)
                .font(Theme.mono(12))
                .foregroundStyle(Theme.textTertiary)
                .monospacedDigit()
            if let duration = event.duration {
                Text(duration)
                    .font(Theme.mono(12))
                    .foregroundStyle(Theme.textTertiary)
                    .monospacedDigit()
            }
        }
        .padding(.top, 1)
    }

    // MARK: - Per-kind styling

    private var isTechnical: Bool {
        event.kind == .tool || event.kind == .error
    }

    private var detailFont: Font {
        isTechnical ? Theme.mono(13) : .system(size: 13)
    }

    private var detailColor: Color {
        event.kind == .error ? Theme.destructive : Theme.textSecondary
    }

    private var glyphColor: Color {
        switch event.kind {
        case .error: return Theme.destructive
        case .result: return Theme.success
        case .tool: return Theme.textSecondary
        case .thinking, .message: return Theme.textTertiary
        }
    }

    private var glyph: String {
        switch event.kind {
        case .thinking: return "brain"
        case .tool: return "wrench.and.screwdriver"
        case .message: return "text.bubble"
        case .result: return "checkmark.seal"
        case .error: return "exclamationmark.triangle"
        }
    }

    // MARK: - Rail geometry

    private var moat: CGFloat { RunTraceMetrics.dotSize + 7 }
    // Vertical center of the marker within the row, matching the title's center.
    private var markerCenter: CGFloat { RunTraceMetrics.rowVerticalPadding + 8 }
}

// MARK: - Preview

#Preview("Run trace") {
    let events = [
        MockRunEvent(id: "1", kind: .thinking, title: "Planning the change",
                     detail: "Locate the tool execute path, then thread the new param through ResearchTool.",
                     at: "0.0s", duration: "1.4s", state: .completed),
        MockRunEvent(id: "2", kind: .tool, title: "read_file",
                     detail: "apps/server/arden/tools/research.py",
                     at: "1.4s", duration: "0.1s", state: .completed),
        MockRunEvent(id: "3", kind: .tool, title: "bash",
                     detail: "uv run pytest tests/test_research_tools.py -q",
                     at: "1.6s", duration: "4.2s", state: .completed),
        MockRunEvent(id: "4", kind: .message, title: "Note to self",
                     detail: "Two tests assert the old signature — update them before editing the tool.",
                     at: "5.9s", duration: nil, state: .completed),
        MockRunEvent(id: "5", kind: .error, title: "bash failed",
                     detail: "ruff: F401 'os' imported but unused (research_artifacts.py:3)",
                     at: "6.1s", duration: "0.3s", state: .failed),
        MockRunEvent(id: "6", kind: .tool, title: "edit_file",
                     detail: "apps/server/arden/tools/research_artifacts.py",
                     at: "6.4s", duration: nil, state: .running),
        MockRunEvent(id: "7", kind: .result, title: "Final answer",
                     detail: "Patched the artifact builder and refreshed the test fixtures.",
                     at: "—", duration: nil, state: .pending),
    ]

    return ScrollView {
        RunTraceView(events: events)
            .padding(.horizontal, 20)
            .padding(.vertical, 16)
    }
    .background(Theme.canvas)
}
