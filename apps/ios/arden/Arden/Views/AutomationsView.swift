import SwiftUI

// Direction B — flat, one accent, near-black pill action, SF Mono metadata,
// hairline dividers, grouped rounded cards. A dedicated screen pushed from the
// drawer (no NavigationStack here — the parent supplies one).

struct AutomationsView: View {
    @ObservedObject var store: ArdenMobileStore
    @State private var selected: MockAutomation?

    private var active: [MockAutomation] { store.automations.filter { !$0.builtin } }
    private var system: [MockAutomation] { store.automations.filter { $0.builtin } }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                if !active.isEmpty {
                    section(title: "Active", automations: active)
                }
                if !system.isEmpty {
                    section(title: "System", automations: system)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, 32)
        }
        .scrollContentBackground(.hidden)
        .background(Theme.canvas.ignoresSafeArea())
        .navigationTitle("Automations")
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $selected) { automation in
            AutomationDetailSheet(automation: automation) { id in
                Task { await store.runAutomation(id) }
            }
        }
        .refreshable { await store.loadAutomations() }
        .task { await store.loadAutomations() }
    }

    private func section(title: String, automations: [MockAutomation]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(Theme.textSecondary)
                .padding(.leading, 4)

            VStack(spacing: 10) {
                ForEach(automations) { automation in
                    AutomationCard(
                        automation: automation,
                        onToggle: { Task { await store.toggleAutomation(automation.id) } },
                        onOpen: { selected = automation }
                    )
                }
            }
        }
    }
}

// MARK: - Card

private struct AutomationCard: View {
    let automation: MockAutomation
    let onToggle: () -> Void
    let onOpen: () -> Void

    var body: some View {
        Button(action: onOpen) {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .top, spacing: 12) {
                    Toggle("", isOn: Binding(get: { automation.enabled }, set: { _ in onToggle() }))
                        .labelsHidden()
                        .toggleStyle(.switch)
                        .tint(Theme.accent)
                        .accessibilityLabel(automation.enabled ? "Disable \(automation.name)" : "Enable \(automation.name)")

                    VStack(alignment: .leading, spacing: 4) {
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text(automation.name)
                                .font(.system(size: 16, weight: .medium))
                                .foregroundStyle(Theme.textPrimary)
                                .lineLimit(1)

                            if automation.builtin {
                                SystemTag()
                            }
                        }

                        Text(automation.description.isEmpty ? "No description." : automation.description)
                            .font(.system(size: 13, weight: .regular))
                            .foregroundStyle(Theme.textSecondary)
                            .lineLimit(2)
                            .multilineTextAlignment(.leading)
                    }

                    Spacer(minLength: 0)
                }

                HStack(spacing: 8) {
                    ScheduleBadge(text: automation.schedule)
                    Spacer(minLength: 0)
                    MetaLine(state: automation.lastState, lastRun: automation.lastRun, nextRun: automation.nextRun)
                }
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.surface)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(Theme.groupOutline, lineWidth: 0.5)
            )
            .opacity(automation.enabled ? 1 : 0.62)
        }
        .buttonStyle(PressScaleButtonStyle())
    }
}

private struct ScheduleBadge: View {
    let text: String

    var body: some View {
        Text(text)
            .font(Theme.mono(12))
            .foregroundStyle(Theme.textSecondary)
            .lineLimit(1)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(Theme.raised)
            .clipShape(Capsule())
    }
}

private struct MetaLine: View {
    let state: RunState?
    let lastRun: String?
    let nextRun: String?

    var body: some View {
        HStack(spacing: 6) {
            if let state {
                Circle()
                    .fill(state.color)
                    .frame(width: 6, height: 6)
            }

            if lastRun != nil || nextRun != nil {
                (
                    Text(lastRun ?? "—")
                        .font(Theme.mono(12))
                    + Text(nextRun != nil ? "  ·  next " : "")
                        .font(.system(size: 12))
                    + Text(nextRun ?? "")
                        .font(Theme.mono(12))
                )
                .foregroundStyle(Theme.textTertiary)
                .lineLimit(1)
            }
        }
    }
}

private struct SystemTag: View {
    var body: some View {
        Text("System")
            .font(.system(size: 11, weight: .medium))
            .foregroundStyle(Theme.textTertiary)
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .background(Theme.raised)
            .clipShape(Capsule())
    }
}

// MARK: - Detail sheet

private struct AutomationDetailSheet: View {
    let automation: MockAutomation
    var onRun: (String) -> Void = { _ in }
    @Environment(\.dismiss) private var dismiss
    @State private var isRunning = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    header
                    infoCard
                    runNowButton
                    runHistory
                }
                .padding(.horizontal, 16)
                .padding(.top, 8)
                .padding(.bottom, 32)
            }
            .scrollContentBackground(.hidden)
            .background(Theme.canvas.ignoresSafeArea())
            .navigationTitle(automation.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { dismiss() } label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 16, weight: .semibold))
                            .foregroundStyle(Theme.textSecondary)
                    }
                    .buttonStyle(PressScaleButtonStyle())
                    .accessibilityLabel("Close")
                }
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text(automation.name)
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(Theme.textPrimary)
                if automation.builtin {
                    SystemTag()
                }
            }
            if !automation.description.isEmpty {
                Text(automation.description)
                    .font(.system(size: 15, weight: .regular))
                    .foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.top, 4)
    }

    private var infoCard: some View {
        VStack(spacing: 0) {
            InfoRow(label: "Schedule", value: automation.schedule)
            Hairline().padding(.leading, 16)
            InfoRow(label: "Trigger", value: automation.trigger)
            Hairline().padding(.leading, 16)
            InfoRow(label: "Next run", value: automation.nextRun ?? "—")
        }
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(Theme.groupOutline, lineWidth: 0.5)
        )
    }

    private var runNowButton: some View {
        Button {
            guard !isRunning else { return }
            isRunning = true
            onRun(automation.id)
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { isRunning = false }
        } label: {
            HStack(spacing: 8) {
                if isRunning {
                    Image(systemName: "circle.dotted")
                        .font(.system(size: 15, weight: .semibold))
                } else {
                    Image(systemName: "play.fill")
                        .font(.system(size: 13, weight: .semibold))
                }
                Text(isRunning ? "Running…" : "Run now")
                    .font(.system(size: 16, weight: .semibold))
            }
            .foregroundStyle(Theme.pillText)
            .frame(maxWidth: .infinity)
            .frame(height: 48)
            .background(Theme.pill)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
        .buttonStyle(PressScaleButtonStyle())
        .disabled(isRunning)
    }

    private var runHistory: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Run history")
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(Theme.textSecondary)
                .padding(.leading, 4)

            if automation.runs.isEmpty {
                Text("No runs recorded yet.")
                    .font(.system(size: 13, weight: .regular))
                    .foregroundStyle(Theme.textTertiary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(14)
                    .background(Theme.surface)
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .stroke(Theme.groupOutline, lineWidth: 0.5)
                    )
            } else {
                VStack(spacing: 0) {
                    let runs = automation.runs
                    ForEach(Array(runs.enumerated()), id: \.element.id) { index, run in
                        RunRow(run: run)
                        if index < runs.count - 1 {
                            Hairline().padding(.leading, 16)
                        }
                    }
                }
                .background(Theme.surface)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                        .stroke(Theme.groupOutline, lineWidth: 0.5)
                )
            }
        }
    }
}

private struct InfoRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack(spacing: 12) {
            Text(label)
                .font(.system(size: 15, weight: .regular))
                .foregroundStyle(Theme.textSecondary)
            Spacer(minLength: 12)
            Text(value)
                .font(Theme.mono(14))
                .foregroundStyle(Theme.textPrimary)
                .multilineTextAlignment(.trailing)
                .lineLimit(2)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 13)
    }
}

private struct RunRow: View {
    let run: MockAutomationRun

    private var failed: Bool { run.state == .failed }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Circle()
                    .fill(run.state.color)
                    .frame(width: 7, height: 7)

                Text(run.state.label)
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(Theme.textPrimary)

                Spacer(minLength: 8)

                Text(run.started)
                    .font(Theme.mono(12))
                    .foregroundStyle(Theme.textSecondary)
                Text(run.duration)
                    .font(Theme.mono(12))
                    .foregroundStyle(Theme.textTertiary)
            }

            if let summary = run.summary, !summary.isEmpty {
                Text(summary)
                    .font(.system(size: 13, weight: .regular))
                    .foregroundStyle(failed ? Theme.destructive : Theme.textSecondary)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.leading, 15)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 16)
        .padding(.vertical, 13)
    }
}
