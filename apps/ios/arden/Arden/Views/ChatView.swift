import SwiftUI
import Combine

struct ChatView: View {
    @ObservedObject var store: ArdenMobileStore
    @Binding var showingSettings: Bool
    @Binding var showingSessions: Bool
    @State private var showingRunTrace = false

    init(
        store: ArdenMobileStore,
        showingSettings: Binding<Bool> = .constant(false),
        showingSessions: Binding<Bool> = .constant(false)
    ) {
        self.store = store
        self._showingSettings = showingSettings
        self._showingSessions = showingSessions
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.doc
                    .ignoresSafeArea()

                TranscriptList(store: store)
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { toolbarContent }
            .safeAreaInset(edge: .bottom) {
                Composer(
                    isStreaming: store.isStreaming,
                    isSending: store.isSending,
                    onPlus: { showingSessions = true },
                    onSend: { text in Task { await store.send(text) } },
                    onStop: { Task { await store.cancelRun() } }
                )
                .equatable()
            }
            .sheet(isPresented: $showingRunTrace) {
                NavigationStack {
                    ScrollView {
                        RunTraceView(events: MockArdenData.demoRunTrace())
                            .padding(16)
                    }
                    .background(Theme.canvas)
                    .navigationTitle("Run trace")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .topBarTrailing) {
                            Button("Done") { showingRunTrace = false }
                                .tint(Theme.accent)
                        }
                    }
                }
            }
        }
    }

    // MARK: - Navigation bar

    @ToolbarContentBuilder
    private var toolbarContent: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            Button {
                showingSessions = true
            } label: {
                Image(systemName: "line.3.horizontal")
                    .font(.system(size: 18, weight: .medium))
                    .foregroundStyle(Theme.textPrimary)
            }
            .accessibilityLabel("Open chats")
        }

        ToolbarItem(placement: .principal) {
            VStack(spacing: 1) {
                Text(store.selectedSession?.title ?? "Arden")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(Theme.textPrimary)
                    .lineLimit(1)
                    .truncationMode(.tail)

                HStack(spacing: 5) {
                    if store.isStreaming {
                        Circle()
                            .fill(Theme.accent)
                            .frame(width: 6, height: 6)
                    }
                    Text(subtitle)
                        .font(Theme.mono(12))
                        .foregroundStyle(Theme.textSecondary)
                        .lineLimit(1)
                }
            }
            .frame(maxWidth: 220)
        }

        ToolbarItemGroup(placement: .topBarTrailing) {
            Button {
                Task { await store.createSession() }
            } label: {
                Image(systemName: "square.and.pencil")
                    .font(.system(size: 17, weight: .medium))
                    .foregroundStyle(Theme.textPrimary)
            }
            .accessibilityLabel("New chat")

            Menu {
                Button {
                    showingRunTrace = true
                } label: {
                    Label("Run trace", systemImage: "list.bullet.indent")
                }
                Button {
                    showingSettings = true
                } label: {
                    Label("Settings", systemImage: "gearshape")
                }
            } label: {
                Image(systemName: "ellipsis")
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(Theme.textPrimary)
            }
            .accessibilityLabel("More")
        }
    }

    // MARK: - Derived

    private var subtitle: String {
        if store.useMockData {
            return "stub api"
        }

        if let host = URLComponents(string: store.config.normalized.serverURL)?.host, !host.isEmpty {
            return "\(store.connectionLabel.lowercased()) · \(host)"
        }

        return store.connectionLabel.lowercased()
    }

}

// MARK: - Transcript list
//
// Holds the transcript ScrollView. ChatView still re-evaluates on store changes
// (object-level ObservableObject), but the streaming hot path is cheap: rows are
// .equatable() (unchanged rows skip), markdown isn't parsed mid-stream, and the
// Composer is .equatable() so it's skipped while a reply streams.
private struct TranscriptList: View {
    @ObservedObject var store: ArdenMobileStore
    @AppStorage("arden.haptics") private var haptics = true

    var body: some View {
        Group {
            if store.selectedSessionID == nil && store.transcript.messages.isEmpty {
                emptyState
            } else {
                transcript
            }
        }
        .sensoryFeedback(trigger: store.transcript.messages.count) { _, _ in
            haptics ? .impact(weight: .light) : nil
        }
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 22) {
                    ForEach(store.transcript.messages) { message in
                        TranscriptMessageView(message: message)
                            .equatable()
                            .id(message.id)
                            .transition(.asymmetric(
                                insertion: .opacity.combined(with: .move(edge: .bottom)),
                                removal: .opacity
                            ))
                    }

                    if !store.transcript.pendingApprovals.isEmpty {
                        VStack(spacing: 12) {
                            ForEach(store.transcript.pendingApprovals) { approval in
                                ApprovalCard(
                                    approval: approval,
                                    approve: { Task { await store.resolve(approval, approved: true) } },
                                    reject: { Task { await store.resolve(approval, approved: false) } }
                                )
                            }
                        }
                        .transition(.opacity.combined(with: .move(edge: .bottom)))
                    }

                    if store.isLoading && store.transcript.messages.isEmpty {
                        ProgressView()
                            .frame(maxWidth: .infinity)
                            .padding(.top, 40)
                    }

                    Color.clear
                        .frame(height: 1)
                        .id("bottom")
                }
                .padding(.horizontal, 20)
                .padding(.top, 14)
                .padding(.bottom, 18)
                .animation(.spring(response: 0.28, dampingFraction: 0.88), value: store.transcript.messages.count)
                .animation(.spring(response: 0.28, dampingFraction: 0.88), value: store.transcript.pendingApprovals.count)
            }
            .scrollDismissesKeyboard(.interactively)
            .onChange(of: store.transcript.messages.count) { _, _ in
                scrollToBottom(proxy)
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 16) {
            Image(systemName: "bubble.left.and.text.bubble.right")
                .font(.system(size: 40, weight: .light))
                .foregroundStyle(Theme.textTertiary)

            Text(store.isLoading ? "Loading" : "Start a chat")
                .font(.system(size: 20, weight: .semibold))
                .foregroundStyle(Theme.textPrimary)

            Button {
                Task { await store.createSession() }
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "square.and.pencil")
                        .font(.system(size: 16, weight: .medium))
                    Text("New chat")
                        .font(.system(size: 15, weight: .medium))
                }
                .foregroundStyle(Theme.pillText)
                .padding(.horizontal, 22)
                .frame(height: 48)
                .background(Capsule().fill(Theme.pill))
            }
            .buttonStyle(PressScaleButtonStyle())
        }
        .padding(.horizontal, 28)
    }

    private func scrollToBottom(_ proxy: ScrollViewProxy) {
        withAnimation(.spring(response: 0.28, dampingFraction: 0.88)) {
            proxy.scrollTo("bottom", anchor: .bottom)
        }
    }
}

// MARK: - Composer
//
// Owns its own text + focus locally so typing and tapping the field never
// republish the store or re-render the transcript (that re-render was stalling
// the keyboard and making typing laggy). It commits to the store only on send.
private struct Composer: View, Equatable {
    let isStreaming: Bool
    let isSending: Bool
    var onPlus: () -> Void
    var onSend: (String) -> Void
    var onStop: () -> Void

    // The closures can't be compared and are recreated on every ChatView.body
    // pass; compare only the value inputs so `.equatable()` skips re-evaluating
    // the composer while the transcript streams (it owns its own text/focus).
    static func == (lhs: Composer, rhs: Composer) -> Bool {
        lhs.isStreaming == rhs.isStreaming && lhs.isSending == rhs.isSending
    }

    @AppStorage("arden.model") private var model = "Opus 4.8"
    @AppStorage("arden.effort") private var effort = "High"
    @FocusState private var focused: Bool
    @State private var text = ""
    @State private var showingModelPicker = false

    private var canSend: Bool {
        !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        VStack(spacing: 8) {
            TextField("Ask Arden", text: $text, axis: .vertical)
                .focused($focused)
                .font(.system(size: 17, weight: .regular))
                .foregroundStyle(Theme.textPrimary)
                .lineLimit(1...6)
                .textFieldStyle(.plain)
                .autocorrectionDisabled()
                .disabled(isSending)
                .padding(.horizontal, 4)
                .padding(.top, 4)

            HStack(spacing: 6) {
                Button(action: onPlus) {
                    Image(systemName: "plus")
                        .font(.system(size: 19, weight: .regular))
                        .foregroundStyle(Theme.textPrimary)
                        .frame(width: 32, height: 32)
                        .contentShape(Rectangle())
                }
                .buttonStyle(PressScaleButtonStyle())
                .accessibilityLabel("Open actions")

                Button { showingModelPicker = true } label: {
                    HStack(spacing: 5) {
                        Image(systemName: "sparkle")
                            .font(.system(size: 14, weight: .regular))
                            .foregroundStyle(Theme.accent)
                        Text(model)
                            .font(.system(size: 14, weight: .medium))
                            .foregroundStyle(Theme.textPrimary)
                        Text(" · \(effort)")
                            .font(.system(size: 14, weight: .regular))
                            .foregroundStyle(Theme.textSecondary)
                    }
                    .lineLimit(1)
                    .contentShape(Rectangle())
                }
                .buttonStyle(PressScaleButtonStyle())
                .accessibilityLabel("Model")

                Spacer(minLength: 0)

                Image(systemName: "mic")
                    .font(.system(size: 18, weight: .regular))
                    .foregroundStyle(Theme.textSecondary)
                    .frame(width: 32, height: 32)

                sendButton
            }
        }
        .padding(9)
        .background(
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .fill(Theme.surface)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .strokeBorder(Theme.composerBorder, lineWidth: 1)
        )
        .padding(.horizontal, 16)
        .padding(.top, 8)
        .padding(.bottom, 6)
        .background(
            Theme.doc
                .mask(LinearGradient(
                    colors: [.clear, Theme.doc, Theme.doc],
                    startPoint: .top,
                    endPoint: .bottom
                ))
                .ignoresSafeArea(edges: .bottom)
        )
        .sheet(isPresented: $showingModelPicker) {
            ModelPickerSheet(model: $model, effort: $effort)
        }
    }

    private var sendButton: some View {
        Button {
            if isStreaming {
                onStop()
            } else {
                let outgoing = text
                text = ""
                onSend(outgoing)
            }
        } label: {
            Image(systemName: isStreaming ? "stop.fill" : "arrow.up")
                .font(.system(size: isStreaming ? 13 : 16, weight: .semibold))
                .foregroundStyle(glyphColor)
                .frame(width: 32, height: 32)
                .background(Circle().fill(fillColor))
                .contentShape(Circle())
        }
        .buttonStyle(PressScaleButtonStyle())
        .disabled(isSending || (!canSend && !isStreaming))
        .accessibilityLabel(isStreaming ? "Stop" : "Send message")
    }

    private var fillColor: Color {
        if isStreaming { return Theme.pill }
        return canSend ? Theme.pill : Theme.canvas
    }

    private var glyphColor: Color {
        if isStreaming { return Theme.pillText }
        return canSend ? Theme.pillText : Theme.sendDisabled
    }
}

// MARK: - Message rows

private struct TranscriptMessageView: View, Equatable {
    let message: MobileMessage
    @State private var showingToolDetail = false
    @State private var showingArtifact = false

    static func == (lhs: TranscriptMessageView, rhs: TranscriptMessageView) -> Bool {
        lhs.message == rhs.message
    }

    var body: some View {
        switch message.role {
        case .user:
            userMessage
        case .assistant:
            assistantMessage
        case .tool:
            toolMessage
        case .activity:
            activityMessage
        case .error:
            errorMessage
        case .workflow:
            if let workflow = message.workflow {
                WorkflowCard(workflow: workflow)
            }
        case .subagents:
            if let subagents = message.subagents {
                SubagentList(agents: subagents)
            }
        case .toolChain:
            if let steps = message.toolSteps {
                ToolChainView(steps: steps)
            }
        case .artifact:
            if let artifact = message.artifact {
                ArtifactCard(artifact: artifact, onOpen: { showingArtifact = true })
                    .sheet(isPresented: $showingArtifact) {
                        ArtifactViewerSheet(artifact: artifact)
                    }
            }
        }
    }

    private var userMessage: some View {
        HStack {
            Spacer(minLength: 40)
            Text(message.content)
                .font(.system(size: 17, weight: .regular))
                .foregroundStyle(Theme.textPrimary)
                .lineSpacing(5)
                .textSelection(.enabled)
                .padding(.horizontal, 15)
                .padding(.vertical, 11)
                .frame(maxWidth: 290, alignment: .leading)
                .background(
                    RoundedRectangle(cornerRadius: 19, style: .continuous)
                        .fill(Theme.bubble)
                )
        }
    }

    private var assistantMessage: some View {
        Group {
            if message.content.isEmpty && message.isStreaming {
                StreamingIndicator()
            } else {
                AssistantContent(content: message.content, streaming: message.isStreaming)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var toolMessage: some View {
        VStack(spacing: 0) {
            Hairline()
            HStack(spacing: 9) {
                Image(systemName: "terminal")
                    .font(.system(size: 14, weight: .regular))
                    .foregroundStyle(Theme.textSecondary)

                Text(message.detail ?? "tool")
                    .font(Theme.mono(13, weight: .semibold))
                    .foregroundStyle(Theme.textPrimary)
                    .layoutPriority(1)

                Text(message.content)
                    .font(Theme.mono(13))
                    .foregroundStyle(Theme.textSecondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)

                if message.isStreaming {
                    ProgressView()
                        .controlSize(.mini)
                } else {
                    Image(systemName: "checkmark")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(Theme.success)
                }
            }
            .padding(.vertical, 8)
            Hairline()
        }
        .contentShape(Rectangle())
        .onTapGesture { showingToolDetail = true }
        .sheet(isPresented: $showingToolDetail) {
            let detail = MockArdenData.toolOutput(forName: message.detail ?? "tool", command: message.content)
            ToolDetailSheet(
                name: message.detail ?? "tool",
                command: message.content,
                output: detail.output,
                diff: detail.diff
            )
        }
    }

    private var activityMessage: some View {
        HStack {
            Spacer()
            Text(message.content)
                .font(Theme.mono(12))
                .foregroundStyle(Theme.textTertiary)
                .multilineTextAlignment(.center)
            Spacer()
        }
    }

    private var errorMessage: some View {
        Text(message.content)
            .font(.system(size: 15, weight: .regular))
            .foregroundStyle(Theme.destructive)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(13)
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(Theme.errorFill)
            )
    }

}

// MARK: - Assistant content (paragraphs + bullets)

private enum AssistantBlock {
    case paragraph(AttributedString)
    case bullet(AttributedString)
}

private final class AssistantBlocksBox {
    let blocks: [AssistantBlock]
    init(_ blocks: [AssistantBlock]) { self.blocks = blocks }
}

// Markdown parsing (AttributedString(markdown:)) is expensive and was running on
// every layout pass — so each keyboard-open / composer height change re-parsed
// the whole transcript, stalling the keyboard and lagging typing. Parse once per
// content string and cache.
private let assistantBlocksCache: NSCache<NSString, AssistantBlocksBox> = {
    let cache = NSCache<NSString, AssistantBlocksBox>()
    cache.countLimit = 256
    return cache
}()

private func assistantBlocks(for content: String, streaming: Bool) -> [AssistantBlock] {
    // While streaming the content grows every chunk, so it would never hit the
    // cache and would re-run AttributedString(markdown:) over the whole O(n)
    // string each tick (and poison the cache with throwaway partials). Render
    // plain text with the same paragraph/bullet structure until the message
    // settles, then parse markdown once (bold/italic snap in on the last frame).
    if streaming {
        return splitLines(content).map { line in
            line.isBullet ? .bullet(AttributedString(line.text)) : .paragraph(AttributedString(line.text))
        }
    }
    if let box = assistantBlocksCache.object(forKey: content as NSString) {
        return box.blocks
    }
    let blocks: [AssistantBlock] = splitLines(content).map { line in
        line.isBullet ? .bullet(assistantInline(line.text)) : .paragraph(assistantInline(line.text))
    }
    assistantBlocksCache.setObject(AssistantBlocksBox(blocks), forKey: content as NSString)
    return blocks
}

private func splitLines(_ content: String) -> [(text: String, isBullet: Bool)] {
    content
        .components(separatedBy: "\n")
        .map { $0.trimmingCharacters(in: .whitespaces) }
        .filter { !$0.isEmpty }
        .map { line in
            (line.hasPrefix("- ") || line.hasPrefix("* "))
                ? (String(line.dropFirst(2)), true)
                : (line, false)
        }
}

private func assistantInline(_ text: String) -> AttributedString {
    (try? AttributedString(
        markdown: text,
        options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
    )) ?? AttributedString(text)
}

private struct AssistantContent: View {
    let content: String
    var streaming = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(assistantBlocks(for: content, streaming: streaming).enumerated()), id: \.offset) { _, block in
                switch block {
                case .paragraph(let text):
                    Text(text)
                        .font(.system(size: 17, weight: .regular))
                        .foregroundStyle(Theme.textPrimary)
                        .lineSpacing(5)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                case .bullet(let text):
                    HStack(alignment: .top, spacing: 10) {
                        Circle()
                            .fill(Theme.textSecondary)
                            .frame(width: 5, height: 5)
                            .padding(.top, 8)
                        Text(text)
                            .font(.system(size: 17, weight: .regular))
                            .foregroundStyle(Theme.textPrimary)
                            .lineSpacing(5)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - Streaming indicator

private struct StreamingIndicator: View {
    @State private var phase = 0

    private let timer = Timer.publish(every: 0.42, on: .main, in: .common).autoconnect()

    var body: some View {
        HStack(spacing: 8) {
            HStack(spacing: 4) {
                ForEach(0..<3) { index in
                    Circle()
                        .fill(Theme.textTertiary)
                        .frame(width: 6, height: 6)
                        .opacity(phase == index ? 1 : 0.25)
                }
            }
            Text("thinking")
                .font(Theme.mono(13))
                .foregroundStyle(Theme.textTertiary)
        }
        .onReceive(timer) { _ in
            phase = (phase + 1) % 3
        }
    }
}
