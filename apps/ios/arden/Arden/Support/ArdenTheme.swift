import SwiftUI

extension Color {
    init(hex: UInt, alpha: Double = 1) {
        let red = Double((hex >> 16) & 0xFF) / 255
        let green = Double((hex >> 8) & 0xFF) / 255
        let blue = Double(hex & 0xFF) / 255
        self.init(.sRGB, red: red, green: green, blue: blue, opacity: alpha)
    }

    // Build the dynamic color from raw RGB components ONLY. Never call
    // UIColor(SwiftUI.Color) inside the provider: SwiftUI's AsyncRenderer evaluates
    // the provider off the main thread during a colorScheme change, and that
    // conversion asserts the main queue → _dispatch_assert_queue_fail crash.
    static func dynamic(
        lightHex: UInt,
        darkHex: UInt,
        lightAlpha: Double = 1,
        darkAlpha: Double = 1
    ) -> Color {
        Color(uiColor: UIColor { trait in
            trait.userInterfaceStyle == .dark
                ? UIColor(hex: darkHex, alpha: darkAlpha)
                : UIColor(hex: lightHex, alpha: lightAlpha)
        })
    }
}

extension UIColor {
    convenience init(hex: UInt, alpha: Double = 1) {
        let red = CGFloat((hex >> 16) & 0xFF) / 255
        let green = CGFloat((hex >> 8) & 0xFF) / 255
        let blue = CGFloat(hex & 0xFF) / 255
        self.init(red: red, green: green, blue: blue, alpha: CGFloat(alpha))
    }
}

enum Theme {
    // Grouped background.
    static let canvas = Color.dynamic(lightHex: 0xF2F2F7, darkHex: 0x1C1C1E)
    // Chat transcript canvas.
    static let doc = Color.dynamic(lightHex: 0xFFFFFF, darkHex: 0x000000)
    // Cards, rows, composer.
    static let surface = Color.dynamic(lightHex: 0xFFFFFF, darkHex: 0x1C1C1E)
    // Selected / pressed row.
    static let raised = Color.dynamic(lightHex: 0xEAEAEF, darkHex: 0x2C2C2E)
    // User bubble fill (cool grey).
    static let bubble = Color.dynamic(lightHex: 0xF1F2F4, darkHex: 0x2A2A2C)
    // Hairline separator (~0.5pt).
    static let sep = Color.dynamic(lightHex: 0x3C3C43, darkHex: 0x545458, lightAlpha: 0.20, darkAlpha: 0.55)
    static let groupOutline = Color.dynamic(lightHex: 0xE3E3E8, darkHex: 0x2C2C2E)
    static let composerBorder = Color.dynamic(lightHex: 0xD6D8DD, darkHex: 0x3A3A3C)
    // Near-black, cool.
    static let textPrimary = Color.dynamic(lightHex: 0x0E1216, darkHex: 0xF2F2F3)
    // Subtitles.
    static let textSecondary = Color.dynamic(lightHex: 0x62676D, darkHex: 0x9BA0A6)
    // Placeholder / caption.
    static let textTertiary = Color.dynamic(lightHex: 0x9AA0A6, darkHex: 0x6C7176)
    // Precise cool blue — selection / links / live. The one accent.
    static let accent = Color.dynamic(lightHex: 0x2B6CF0, darkHex: 0x4C8DFF)
    static let accentTint = Color.dynamic(lightHex: 0x2B6CF0, darkHex: 0x4C8DFF, lightAlpha: 0.10, darkAlpha: 0.16)
    // Near-black primary action.
    static let pill = Color.dynamic(lightHex: 0x131417, darkHex: 0xFFFFFF)
    static let pillText = Color.dynamic(lightHex: 0xFFFFFF, darkHex: 0x0A0A0A)
    static let sendDisabled = Color.dynamic(lightHex: 0xC4C8CE, darkHex: 0x4A4D52)
    static let destructive = Color.dynamic(lightHex: 0xD7373B, darkHex: 0xFF6166)
    static let errorFill = Color.dynamic(lightHex: 0xFDECEC, darkHex: 0x3A1D1E)
    static let success = Color.dynamic(lightHex: 0x1F9D57, darkHex: 0x34C77B)

    static func mono(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight, design: .monospaced)
    }
}

enum AppAppearance: String, CaseIterable, Identifiable {
    case light, dark, system

    var id: String { rawValue }
    var label: String { rawValue.capitalized }

    var colorScheme: ColorScheme? {
        switch self {
        case .light: return .light
        case .dark: return .dark
        case .system: return nil
        }
    }
}

struct PressScaleButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.96 : 1)
            .animation(.spring(response: 0.18, dampingFraction: 0.82), value: configuration.isPressed)
    }
}

struct Hairline: View {
    var body: some View {
        Rectangle()
            .fill(Theme.sep)
            .frame(height: 0.5)
    }
}

struct IconButton: View {
    let systemName: String
    var size: CGFloat = 22
    var weight: Font.Weight = .regular
    var color: Color = Theme.textSecondary
    var frame: CGFloat = 44
    let accessibilityLabel: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: size, weight: weight))
                .foregroundStyle(color)
                .frame(width: frame, height: frame)
                .contentShape(Rectangle())
        }
        .buttonStyle(PressScaleButtonStyle())
        .accessibilityLabel(accessibilityLabel)
    }
}

extension RunState {
    var color: Color {
        switch self {
        case .running: return Theme.accent
        case .completed: return Theme.success
        case .failed: return Theme.destructive
        case .cancelled: return Theme.textTertiary
        case .pending: return Theme.textTertiary
        case .waiting: return Theme.accent
        }
    }

    var symbol: String {
        switch self {
        case .running: return "circle.dotted"
        case .completed: return "checkmark"
        case .failed: return "xmark"
        case .cancelled: return "minus"
        case .pending: return "circle"
        case .waiting: return "clock"
        }
    }

    var label: String { rawValue.capitalized }
}

// Time-driven pulse for "running" indicators. Use inside TimelineView(.animation)
// instead of withAnimation(.repeatForever) — a repeating animation transaction
// inside a LazyVStack/ScrollView fights the scroll offset (jank / "magnet" feel).
enum StatusPulse {
    /// 0→1 sawtooth over `period` seconds, derived from the timeline's date.
    static func phase(_ date: Date, period: Double = 1.4) -> Double {
        date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: period) / period
    }
}
