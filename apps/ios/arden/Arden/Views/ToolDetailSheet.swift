import SwiftUI

// Full detail for a single tool call: command, raw output, and an optional diff.
// Presented via .sheet — flat Direction B styling, no glass.
struct ToolDetailSheet: View {
    let name: String
    let command: String
    let output: String?
    let diff: String?

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    commandSection
                    if let output, !output.isEmpty {
                        outputSection(output)
                    }
                    if let diff, !diff.isEmpty {
                        diffSection(diff)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.top, 12)
                .padding(.bottom, 32)
            }
            .background(Theme.canvas.ignoresSafeArea())
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text(name)
                        .font(Theme.mono(15, weight: .medium))
                        .foregroundStyle(Theme.textPrimary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        dismiss()
                    } label: {
                        Text("Done")
                            .font(.system(size: 16, weight: .semibold))
                            .foregroundStyle(Theme.accent)
                    }
                    .buttonStyle(PressScaleButtonStyle())
                    .accessibilityLabel("Done")
                }
            }
            .toolbarBackground(Theme.canvas, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
        }
    }

    // MARK: - Sections

    private var commandSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            sectionHeader("Command")
            codeBlock {
                Text(command)
                    .font(Theme.mono(13))
                    .foregroundStyle(Theme.textPrimary)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func outputSection(_ output: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            sectionHeader("Output")
            codeBlock {
                Text(output)
                    .font(Theme.mono(12.5))
                    .foregroundStyle(Theme.textPrimary)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func diffSection(_ diff: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            sectionHeader("Diff")
            codeBlock {
                VStack(alignment: .leading, spacing: 1) {
                    ForEach(Array(diff.components(separatedBy: "\n").enumerated()), id: \.offset) { _, line in
                        Text(line.isEmpty ? " " : line)
                            .font(Theme.mono(12.5))
                            .foregroundStyle(diffColor(for: line))
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    // MARK: - Pieces

    private func sectionHeader(_ title: String) -> some View {
        Text(title)
            .font(.system(size: 13, weight: .medium))
            .foregroundStyle(Theme.textSecondary)
    }

    private func codeBlock<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        content()
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .strokeBorder(Theme.groupOutline, lineWidth: 0.5)
            )
    }

    private func diffColor(for line: String) -> Color {
        if line.hasPrefix("@@") { return Theme.textTertiary }
        if line.hasPrefix("+") { return Theme.success }
        if line.hasPrefix("-") { return Theme.destructive }
        return Theme.textPrimary
    }
}

#Preview {
    ToolDetailSheet(
        name: "edit_file",
        command: "edit_file path=arden/server/app.py",
        output: """
        Applied 1 edit to arden/server/app.py
        - 3 insertions(+)
        - 1 deletion(-)
        Lints passed.
        """,
        diff: """
        @@ -41,7 +41,9 @@ def create_app() -> FastAPI:
             app = FastAPI()
        -    app.include_router(legacy_router)
        +    app.include_router(chat_router)
        +    app.include_router(tools_router)
             return app
        """
    )
}
