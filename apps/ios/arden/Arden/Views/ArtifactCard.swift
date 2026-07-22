import SwiftUI

// An in-transcript artifact chip — the iOS sibling of the desktop render_html /
// artifact preview card. A produced document (HTML / React / SVG / Markdown)
// surfaces as one calm, tappable row: a tinted icon tile, the title, a mono
// "kind · updated" caption, and a trailing open glyph. Tapping it raises the
// full-screen ArtifactViewerSheet. Direction B: flat, one accent, SF Mono for
// technical metadata, hairline-free single card, no glass.
struct ArtifactCard: View {
    let artifact: MockArtifact
    var onOpen: () -> Void

    var body: some View {
        Button(action: onOpen) {
            HStack(spacing: 12) {
                iconTile

                VStack(alignment: .leading, spacing: 3) {
                    Text(artifact.title)
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(Theme.textPrimary)
                        .lineLimit(1)
                        .truncationMode(.tail)

                    Text("\(artifact.kind) · \(artifact.updated)")
                        .font(Theme.mono(12))
                        .foregroundStyle(Theme.textSecondary)
                        .lineLimit(1)
                }

                Spacer(minLength: 8)

                Image(systemName: "arrow.up.right.square")
                    .font(.system(size: 16, weight: .regular))
                    .foregroundStyle(Theme.textTertiary)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.surface)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .strokeBorder(Theme.groupOutline, lineWidth: 1)
            )
            .contentShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
        .buttonStyle(PressScaleButtonStyle())
        .accessibilityLabel("Open artifact \(artifact.title)")
        .accessibilityHint("\(artifact.kind), updated \(artifact.updated)")
    }

    private var iconTile: some View {
        RoundedRectangle(cornerRadius: 9, style: .continuous)
            .fill(Theme.accentTint)
            .frame(width: 38, height: 38)
            .overlay(
                Image(systemName: symbol)
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(Theme.accent)
            )
    }

    // Map the artifact kind onto a representative SF Symbol; everything falls
    // back to the rich-document glyph.
    private var symbol: String {
        switch artifact.kind.lowercased() {
        case "html", "react", "svg": return "chart.bar.doc.horizontal"
        case "markdown", "md", "text": return "doc.richtext"
        default: return "doc.richtext"
        }
    }
}

#Preview {
    ScrollView {
        VStack(spacing: 12) {
            ArtifactCard(
                artifact: MockArtifact(
                    id: "1",
                    title: "Vault entropy dashboard",
                    kind: "HTML",
                    updated: "just now",
                    html: "<h1>Hello</h1>"
                ),
                onOpen: {}
            )
            ArtifactCard(
                artifact: MockArtifact(
                    id: "2",
                    title: "Memory consolidation flow",
                    kind: "SVG",
                    updated: "2m ago",
                    html: "<svg></svg>"
                ),
                onOpen: {}
            )
            ArtifactCard(
                artifact: MockArtifact(
                    id: "3",
                    title: "Weekly review summary",
                    kind: "Markdown",
                    updated: "yesterday",
                    html: "# Review"
                ),
                onOpen: {}
            )
        }
        .padding(16)
    }
    .background(Theme.canvas)
}
