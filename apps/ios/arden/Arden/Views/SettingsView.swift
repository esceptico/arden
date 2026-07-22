import SwiftUI

struct SettingsView: View {
    @ObservedObject var store: ArdenMobileStore
    @Environment(\.dismiss) private var dismiss
    @State private var serverURL = ""
    @State private var apiKey = ""
    @State private var useMockData = true
    @State private var isSaving = false
    @State private var showingScanner = false
    @AppStorage("arden.appearance") private var appearance: AppAppearance = .system
    @AppStorage("arden.haptics") private var haptics = true

    private var canSave: Bool {
        useMockData || !serverURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    connectionSection
                    serverSection
                    scanSection
                    appearanceSection
                    behaviorSection
                }
                .padding(.horizontal, 16)
                .padding(.top, 12)
                .padding(.bottom, 32)
            }
            .background(Theme.canvas.ignoresSafeArea())
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .sheet(isPresented: $showingScanner) {
                QRScannerView { code in
                    showingScanner = false
                    guard let pair = parseArdenPairing(code) else { return }
                    serverURL = pair.url
                    apiKey = pair.key
                    useMockData = false
                    Task { await save() }
                }
            }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        dismiss()
                    } label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 16, weight: .semibold))
                            .foregroundStyle(Theme.textSecondary)
                    }
                    .buttonStyle(PressScaleButtonStyle())
                    .accessibilityLabel("Close")
                }

                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await save() }
                    } label: {
                        Image(systemName: "checkmark")
                            .font(.system(size: 17, weight: .semibold))
                            .foregroundStyle(canSave && !isSaving ? Theme.accent : Theme.sendDisabled)
                    }
                    .buttonStyle(PressScaleButtonStyle())
                    .disabled(isSaving || !canSave)
                    .accessibilityLabel("Save settings")
                }
            }
            .onAppear {
                serverURL = store.config.serverURL
                apiKey = store.config.apiKey
                useMockData = store.useMockData
            }
        }
    }

    // MARK: Connection mode

    private var connectionSection: some View {
        SettingsSection(header: "Connection") {
            SettingsGroup {
                Toggle(isOn: Binding(
                    get: { useMockData },
                    set: { next in
                        useMockData = next
                        Task { await store.setMockDataEnabled(next) }
                    }
                )) {
                    SettingsRowLabel(
                        icon: "sparkles",
                        title: "Stub API",
                        subtitle: "Use placeholder sessions for UI work"
                    )
                }
                .toggleStyle(.switch)
                .tint(Theme.accent)
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
        }
    }

    // MARK: Server

    private var serverSection: some View {
        SettingsSection(header: "Server") {
            SettingsGroup {
                VStack(spacing: 0) {
                    SettingsTextField(
                        icon: "network",
                        title: "Server URL",
                        placeholder: "http://100.x.x.x:6877",
                        text: $serverURL,
                        mono: true,
                        keyboardType: .URL
                    )

                    Hairline()
                        .padding(.leading, 52)

                    SettingsTextField(
                        icon: "key",
                        title: "API key",
                        placeholder: "Optional",
                        text: $apiKey,
                        mono: true,
                        isSecure: true
                    )
                }
            }
        }
        .opacity(useMockData ? 0.5 : 1)
        .allowsHitTesting(!useMockData)
        .animation(.easeInOut(duration: 0.2), value: useMockData)
    }

    // MARK: Appearance

    private var appearanceSection: some View {
        SettingsSection(header: "Appearance") {
            SettingsGroup {
                HStack(spacing: 12) {
                    ForEach(AppAppearance.allCases) { mode in
                        Button {
                            appearance = mode
                        } label: {
                            AppearancePreview(
                                title: mode.label,
                                selected: appearance == mode,
                                mode: mode
                            )
                        }
                        .buttonStyle(PressScaleButtonStyle())
                    }
                }
                .padding(16)
            }
        }
    }

    // MARK: Behavior

    private var behaviorSection: some View {
        SettingsSection(header: "Behavior") {
            SettingsGroup {
                Toggle(isOn: $haptics) {
                    SettingsRowLabel(
                        icon: "hand.tap",
                        title: "Haptic feedback",
                        subtitle: "Vibrate on replies and actions"
                    )
                }
                .toggleStyle(.switch)
                .tint(Theme.accent)
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
        }
    }

    private var scanSection: some View {
        Button {
            showingScanner = true
        } label: {
            HStack(spacing: 8) {
                Image(systemName: "qrcode.viewfinder")
                    .font(.system(size: 17, weight: .medium))
                Text("Scan to connect")
                    .font(.system(size: 15, weight: .medium))
            }
            .foregroundStyle(Theme.pillText)
            .frame(maxWidth: .infinity)
            .frame(height: 50)
            .background(Capsule().fill(Theme.pill))
        }
        .buttonStyle(PressScaleButtonStyle())
    }

    private func save() async {
        isSaving = true
        // Persist the config BEFORE flipping mock off. While mock is still on,
        // saveConfig's reload() short-circuits (no network) but writes the new
        // keychain config; then setMockDataEnabled(false) does exactly one real
        // reload against the correct config — instead of one failing request
        // against the stale/default config and a "Disconnected" alert flash.
        if !useMockData {
            await store.saveConfig(AppConfig(serverURL: serverURL, apiKey: apiKey))
        }
        await store.setMockDataEnabled(useMockData)
        isSaving = false
        dismiss()
    }
}

// MARK: - Section scaffolding

private struct SettingsSection<Content: View>: View {
    let header: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(header)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(Theme.textSecondary)
                .padding(.leading, 4)
            content
        }
    }
}

private struct SettingsGroup<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        content
            .background(Theme.surface)
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(Theme.groupOutline, lineWidth: 0.5)
            )
    }
}

// MARK: - Rows

private struct SettingsRowLabel: View {
    let icon: String
    let title: String
    let subtitle: String

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 18, weight: .medium))
                .foregroundStyle(Theme.textSecondary)
                .frame(width: 24)

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.system(size: 17, weight: .regular))
                    .foregroundStyle(Theme.textPrimary)
                Text(subtitle)
                    .font(.system(size: 13, weight: .regular))
                    .foregroundStyle(Theme.textSecondary)
            }
        }
    }
}

private struct SettingsTextField: View {
    let icon: String
    let title: String
    let placeholder: String
    @Binding var text: String
    var mono = false
    var keyboardType: UIKeyboardType = .default
    var isSecure = false

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 18, weight: .medium))
                .foregroundStyle(Theme.textSecondary)
                .frame(width: 24)

            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Theme.textSecondary)

                Group {
                    if isSecure {
                        SecureField(placeholder, text: $text)
                    } else {
                        TextField(placeholder, text: $text)
                    }
                }
                .font(mono ? Theme.mono(15) : .system(size: 17, weight: .regular))
                .foregroundStyle(Theme.textPrimary)
                .tint(Theme.accent)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(keyboardType)
                .textFieldStyle(.plain)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }
}

// MARK: - Appearance preview

private struct AppearancePreview: View {
    let title: String
    let selected: Bool
    let mode: AppAppearance

    private var fill: Color {
        switch mode {
        case .light: return Color(hex: 0xFFFFFF)
        case .dark: return Color(hex: 0x0A0A0A)
        case .system: return Color(hex: 0xFFFFFF)
        }
    }

    private var lineColor: Color {
        mode == .dark ? Color(hex: 0xFFFFFF, alpha: 0.55) : Color(hex: 0x0E1216, alpha: 0.30)
    }

    private var lineColorFaint: Color {
        mode == .dark ? Color(hex: 0xFFFFFF, alpha: 0.30) : Color(hex: 0x0E1216, alpha: 0.18)
    }

    var body: some View {
        VStack(spacing: 8) {
            ZStack(alignment: .topLeading) {
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(fill)
                    .overlay {
                        if mode == .system {
                            GeometryReader { proxy in
                                Path { path in
                                    path.move(to: CGPoint(x: proxy.size.width, y: 0))
                                    path.addLine(to: CGPoint(x: proxy.size.width, y: proxy.size.height))
                                    path.addLine(to: CGPoint(x: 0, y: proxy.size.height))
                                    path.closeSubpath()
                                }
                                .fill(Color(hex: 0x0A0A0A))
                            }
                            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                        }
                    }
                    .overlay(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .stroke(selected ? Theme.accent : Theme.groupOutline,
                                    lineWidth: selected ? 2 : 0.5)
                    )

                VStack(alignment: .leading, spacing: 5) {
                    Capsule().fill(lineColor).frame(width: 48, height: 5)
                    Capsule().fill(lineColorFaint).frame(width: 32, height: 5)
                }
                .padding(11)
            }
            .frame(height: 72)

            Text(title)
                .font(.system(size: 14, weight: selected ? .semibold : .regular))
                .foregroundStyle(selected ? Theme.accent : Theme.textPrimary)
        }
        .frame(maxWidth: .infinity)
    }
}
