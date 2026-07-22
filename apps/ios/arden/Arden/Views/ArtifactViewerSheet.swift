import SwiftUI
import WebKit

// Full-screen artifact viewer — the iOS sibling of the desktop render_html
// preview shell. A NavigationStack with an inline principal title (the artifact
// name + a mono kind subtitle) and a Done button; the body is an edge-to-edge
// web view rendering the artifact's self-contained HTML. Direction B: flat,
// native nav bar, no glass; the document owns the canvas.
struct ArtifactViewerSheet: View {
    let artifact: MockArtifact
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ArtifactWebView(html: artifact.html)
                .ignoresSafeArea(edges: .bottom)
                .background(Theme.doc)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .principal) {
                        VStack(spacing: 1) {
                            Text(artifact.title)
                                .font(.system(size: 16, weight: .semibold))
                                .foregroundStyle(Theme.textPrimary)
                                .lineLimit(1)
                                .truncationMode(.tail)
                            Text(artifact.kind)
                                .font(Theme.mono(11, weight: .medium))
                                .tracking(0.4)
                                .foregroundStyle(Theme.textSecondary)
                                .lineLimit(1)
                        }
                    }
                    ToolbarItem(placement: .topBarTrailing) {
                        Button { dismiss() } label: {
                            Text("Done")
                                .font(.system(size: 16, weight: .semibold))
                                .foregroundStyle(Theme.accent)
                        }
                        .buttonStyle(PressScaleButtonStyle())
                        .accessibilityLabel("Done")
                    }
                }
        }
    }
}

// MARK: - Web view

// Renders the artifact's self-contained HTML. Transparent (so the sheet's
// background shows through during load), non-scroll-bouncing into a colored
// chrome, and re-renders only when the html string changes.
private struct ArtifactWebView: UIViewRepresentable {
    let html: String

    func makeUIView(context: Context) -> WKWebView {
        let webView = WKWebView(frame: .zero)
        webView.isOpaque = false
        webView.backgroundColor = .clear
        webView.scrollView.backgroundColor = .clear
        webView.scrollView.contentInsetAdjustmentBehavior = .always
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        guard context.coordinator.loadedHTML != html else { return }
        context.coordinator.loadedHTML = html
        webView.loadHTMLString(html, baseURL: nil)
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    final class Coordinator {
        var loadedHTML: String?
    }
}

#Preview {
    ArtifactViewerSheet(
        artifact: MockArtifact(
            id: "1",
            title: "Vault entropy dashboard",
            kind: "HTML",
            updated: "just now",
            html: """
            <!doctype html>
            <html>
              <head>
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                  body { font-family: -apple-system, system-ui, sans-serif; margin: 24px; color: #0E1216; }
                  h1 { font-size: 22px; margin: 0 0 4px; }
                  p { color: #62676D; line-height: 1.5; }
                  .bar { height: 10px; border-radius: 5px; background: #2B6CF0; margin: 12px 0; }
                  code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: #F2F2F7; padding: 2px 6px; border-radius: 6px; }
                </style>
              </head>
              <body>
                <h1>Vault entropy dashboard</h1>
                <p>Open facts: <code>1,284</code> · Consolidation backlog: <code>37</code></p>
                <div class="bar" style="width: 72%"></div>
                <div class="bar" style="width: 41%"></div>
                <div class="bar" style="width: 18%"></div>
                <p>Entropy trending down over the last 7 days.</p>
              </body>
            </html>
            """
        )
    )
}
