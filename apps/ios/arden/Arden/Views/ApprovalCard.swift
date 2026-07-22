import SwiftUI

struct ApprovalCard: View {
    let approval: PendingApproval
    let approve: () -> Void
    let reject: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: "checkmark.shield")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(Theme.accent)
                Text(approval.toolName)
                    .font(Theme.mono(15, weight: .semibold))
                    .foregroundStyle(Theme.textPrimary)
            }

            if let preview = approval.preview, !preview.isEmpty {
                Text(preview)
                    .font(.system(size: 14))
                    .foregroundStyle(Theme.textSecondary)
                    .lineLimit(5)
                    .textSelection(.enabled)
            }

            if let diff = approval.diff, !diff.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    Text(diff)
                        .font(Theme.mono(12))
                        .foregroundStyle(Theme.textPrimary)
                        .textSelection(.enabled)
                        .padding(10)
                }
                .frame(maxHeight: 120)
                .background(Theme.canvas)
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            }

            HStack {
                Button(action: reject) {
                    Text("Reject")
                        .font(.system(size: 15, weight: .medium))
                        .foregroundStyle(Theme.destructive)
                        .frame(height: 38)
                        .contentShape(Rectangle())
                }
                .buttonStyle(PressScaleButtonStyle())

                Spacer()

                Button(action: approve) {
                    Text("Approve")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(Theme.pillText)
                        .padding(.horizontal, 18)
                        .frame(height: 38)
                        .background(Theme.pill)
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                }
                .buttonStyle(PressScaleButtonStyle())
            }
        }
        .padding(14)
        .background(Theme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .strokeBorder(Theme.groupOutline, lineWidth: 1)
        )
    }
}
