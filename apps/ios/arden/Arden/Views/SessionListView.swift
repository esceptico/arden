import SwiftUI

struct SessionListView: View {
    @ObservedObject var store: ArdenMobileStore
    @Binding var showingSettings: Bool
    var dismissOnSelect = false
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    header
                    navLinks

                    if store.sessions.isEmpty {
                        emptyState
                    } else {
                        sessionList
                    }
                }
                .padding(.bottom, 110)
            }
            .background(Theme.canvas)
            .scrollContentBackground(.hidden)
            .safeAreaInset(edge: .bottom) {
                newChatPill
            }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    IconButton(
                        systemName: "xmark",
                        size: 17,
                        weight: .semibold,
                        color: Theme.textSecondary,
                        accessibilityLabel: "Close"
                    ) {
                        dismiss()
                    }
                }

                ToolbarItem(placement: .topBarTrailing) {
                    IconButton(
                        systemName: "gearshape",
                        size: 19,
                        color: Theme.textSecondary,
                        accessibilityLabel: "Settings"
                    ) {
                        dismiss()
                        showingSettings = true
                    }
                }
            }
            .toolbarBackground(Theme.canvas, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .navigationBarTitleDisplayMode(.inline)
            .refreshable {
                await store.reload()
            }
        }
        .tint(Theme.accent)
    }

    private var header: some View {
        HStack {
            Text("ARDEN")
                .font(.system(size: 24, weight: .bold))
                .tracking(-0.6)
                .foregroundStyle(Theme.textPrimary)

            Spacer()

            Circle()
                .fill(Theme.pill)
                .frame(width: 34, height: 34)
                .overlay(
                    Text("TG")
                        .font(.system(size: 13, weight: .semibold))
                        .tracking(-0.2)
                        .foregroundStyle(Theme.pillText)
                )
                .accessibilityLabel("Account")
        }
        .padding(.horizontal, 16)
        .padding(.top, 6)
        .padding(.bottom, 2)
    }

    private var navLinks: some View {
        VStack(spacing: 0) {
            NavigationLink {
                AutomationsView(store: store)
            } label: {
                DrawerNavRow(icon: "bolt.badge.clock", title: "Automations")
            }
            .buttonStyle(PressScaleButtonStyle())
        }
        .padding(.horizontal, 8)
        .padding(.top, 8)
    }

    private var sessionList: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Recents")
                .font(.system(size: 13, weight: .medium))
                .tracking(0.2)
                .foregroundStyle(Theme.textSecondary)
                .padding(.horizontal, 20)
                .padding(.top, 18)
                .padding(.bottom, 6)

            VStack(spacing: 0) {
                ForEach(Array(store.sessions.enumerated()), id: \.element.id) { index, session in
                    Button {
                        Task { await store.selectSession(session.sessionID) }
                        if dismissOnSelect {
                            dismiss()
                        }
                    } label: {
                        SessionRow(
                            session: session,
                            isSelected: session.sessionID == store.selectedSessionID
                        )
                    }
                    .buttonStyle(PressScaleButtonStyle())

                    if index < store.sessions.count - 1 {
                        Hairline()
                            .padding(.leading, 46)
                    }
                }
            }
            .padding(.horizontal, 8)
        }
    }

    private var emptyState: some View {
        ContentUnavailableView {
            Label("No chats", systemImage: "bubble.left.and.bubble.right")
        } description: {
            Text("Start a new chat to begin.")
                .font(.system(size: 15))
                .foregroundStyle(Theme.textSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 80)
    }

    private var newChatPill: some View {
        Button {
            Task { await store.createSession() }
            if dismissOnSelect {
                dismiss()
            }
        } label: {
            HStack(spacing: 9) {
                Image(systemName: "square.and.pencil")
                    .font(.system(size: 17, weight: .medium))
                Text("New chat")
                    .font(.system(size: 15, weight: .medium))
                    .tracking(-0.2)
            }
            .foregroundStyle(Theme.pillText)
            .padding(.horizontal, 22)
            .frame(height: 48)
            .background(Theme.pill, in: Capsule())
            .shadow(color: .black.opacity(0.28), radius: 14, x: 0, y: 8)
            .shadow(color: .black.opacity(0.18), radius: 4, x: 0, y: 2)
        }
        .buttonStyle(PressScaleButtonStyle())
        .accessibilityLabel("New chat")
        .padding(.bottom, 14)
    }
}

private struct DrawerNavRow: View {
    let icon: String
    let title: String

    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: icon)
                .font(.system(size: 18, weight: .regular))
                .foregroundStyle(Theme.textPrimary)
                .frame(width: 24)
            Text(title)
                .font(.system(size: 16))
                .foregroundStyle(Theme.textPrimary)
            Spacer()
            Image(systemName: "chevron.right")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(Theme.textTertiary)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 11)
        .contentShape(Rectangle())
    }
}

private struct SessionRow: View {
    let session: SessionListItem
    let isSelected: Bool

    private var isLive: Bool { session.activeRunID != nil }

    var body: some View {
        HStack(spacing: 12) {
            glyph
                .frame(width: 22)

            VStack(alignment: .leading, spacing: 2) {
                Text(session.title)
                    .font(.system(size: 16, weight: isSelected ? .medium : .regular))
                    .tracking(-0.3)
                    .foregroundStyle(Theme.textPrimary)
                    .lineLimit(1)

                metaLine
            }

            Spacer(minLength: 8)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 11)
        .background(isSelected ? Theme.raised : Color.clear, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .contentShape(Rectangle())
    }

    @ViewBuilder
    private var glyph: some View {
        if isLive {
            Circle()
                .fill(Theme.accent)
                .frame(width: 12, height: 12)
        } else {
            Image(systemName: "bubble.left")
                .font(.system(size: 17, weight: .regular))
                .foregroundStyle(Theme.textTertiary)
        }
    }

    private var metaLine: some View {
        HStack(spacing: 7) {
            Text("\(session.messageCount) messages")
                .font(Theme.mono(13))
                .foregroundStyle(Theme.textSecondary)

            if let count = session.pendingApprovalsCount, count > 0 {
                HStack(spacing: 3) {
                    Image(systemName: "shield")
                        .font(.system(size: 11, weight: .semibold))
                    Text("\(count) to approve")
                        .font(.system(size: 12, weight: .semibold))
                }
                .foregroundStyle(Theme.accent)
            } else if let relative = relativeTime(session.lastActivity) {
                Text("· \(relative)")
                    .font(Theme.mono(13))
                    .foregroundStyle(Theme.textSecondary)
            }
        }
    }
}

private let isoFormatter: ISO8601DateFormatter = {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime]
    return formatter
}()

private func relativeTime(_ iso: String) -> String? {
    guard let date = isoFormatter.date(from: iso) else { return nil }
    let interval = Date().timeIntervalSince(date)
    if interval < 60 { return "now" }
    let minutes = Int(interval / 60)
    if minutes < 60 { return "\(minutes)m" }
    let hours = Int(interval / 3600)
    if hours < 24 { return "\(hours)h" }
    let days = Int(interval / 86400)
    if days == 1 { return "yesterday" }
    if days < 7 { return "\(days)d" }
    let weeks = days / 7
    return "\(weeks)w"
}
