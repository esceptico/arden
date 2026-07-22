import SwiftUI

struct ModelPickerSheet: View {
    @Binding var model: String
    @Binding var effort: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    PickerSection(header: "Model") {
                        OptionGroup(
                            options: MockArdenData.models,
                            selection: model,
                            glyph: "sparkle",
                            select: { model = $0 }
                        )
                    }

                    PickerSection(header: "Reasoning effort") {
                        OptionGroup(
                            options: MockArdenData.efforts,
                            selection: effort,
                            glyph: "gauge.with.dots.needle.50percent",
                            select: { effort = $0 }
                        )
                    }
                }
                .padding(.horizontal, 16)
                .padding(.top, 12)
                .padding(.bottom, 32)
            }
            .background(Theme.canvas.ignoresSafeArea())
            .navigationTitle("Model")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(Theme.accent)
                        .buttonStyle(PressScaleButtonStyle())
                        .accessibilityLabel("Done")
                }
            }
        }
    }
}

// MARK: - Section scaffolding

private struct PickerSection<Content: View>: View {
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

private struct OptionGroup: View {
    let options: [String]
    let selection: String
    let glyph: String
    let select: (String) -> Void

    var body: some View {
        VStack(spacing: 0) {
            ForEach(Array(options.enumerated()), id: \.element) { index, option in
                Button {
                    select(option)
                } label: {
                    OptionRow(
                        title: option,
                        glyph: glyph,
                        selected: option == selection
                    )
                }
                .buttonStyle(PressScaleButtonStyle())

                if index < options.count - 1 {
                    Hairline()
                        .padding(.leading, 52)
                }
            }
        }
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(Theme.groupOutline, lineWidth: 0.5)
        )
    }
}

private struct OptionRow: View {
    let title: String
    let glyph: String
    let selected: Bool

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: glyph)
                .font(.system(size: 18, weight: .medium))
                .foregroundStyle(selected ? Theme.accent : Theme.textSecondary)
                .frame(width: 24)

            Text(title)
                .font(.system(size: 17, weight: selected ? .medium : .regular))
                .foregroundStyle(Theme.textPrimary)

            Spacer(minLength: 12)

            if selected {
                Image(systemName: "checkmark")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(Theme.accent)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 13)
        .frame(maxWidth: .infinity)
        .contentShape(Rectangle())
    }
}

// MARK: - Preview

#Preview {
    struct PreviewHost: View {
        @State private var model = MockArdenData.models.first ?? "Opus 4.8"
        @State private var effort = "Medium"
        var body: some View {
            ModelPickerSheet(model: $model, effort: $effort)
        }
    }
    return PreviewHost()
}
