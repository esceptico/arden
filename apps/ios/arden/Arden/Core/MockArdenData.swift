import Foundation

enum MockArdenData {
    static var sessions: [SessionListItem] {
        [
            SessionListItem(
                sessionID: "mock-slayer-exciter",
                startedAt: "2026-06-13T19:40:00Z",
                lastActivity: "2026-06-14T16:31:00Z",
                name: "IRF540N slayer excitor circuit",
                messageCount: 34,
                sessionType: "chat",
                activeRunID: "mock-run-approval",
                runStatus: "waiting_for_approval",
                pendingApprovalsCount: 1
            ),
            SessionListItem(
                sessionID: "mock-project-overview",
                startedAt: "2026-06-14T08:10:00Z",
                lastActivity: "2026-06-14T16:18:00Z",
                name: "ARDEN project overview",
                messageCount: 12,
                sessionType: "chat",
                activeRunID: nil,
                runStatus: nil,
                pendingApprovalsCount: nil
            ),
            SessionListItem(
                sessionID: "mock-workflow-demo",
                startedAt: "2026-06-14T15:50:00Z",
                lastActivity: "2026-06-14T16:29:00Z",
                name: "Multi-agent research run",
                messageCount: 9,
                sessionType: "chat",
                activeRunID: "mock-run-workflow",
                runStatus: "running",
                pendingApprovalsCount: nil
            ),
            SessionListItem(
                sessionID: "mock-xcode-incompat",
                startedAt: "2026-06-14T13:55:00Z",
                lastActivity: "2026-06-14T14:30:00Z",
                name: "Xcode incompatibility after macOS update",
                messageCount: 8,
                sessionType: "chat",
                activeRunID: nil,
                runStatus: nil,
                pendingApprovalsCount: nil
            ),
            SessionListItem(
                sessionID: "mock-cold-outreach",
                startedAt: "2026-06-14T09:30:00Z",
                lastActivity: "2026-06-14T11:20:00Z",
                name: "Cold outreach message feedback",
                messageCount: 21,
                sessionType: "chat",
                activeRunID: nil,
                runStatus: nil,
                pendingApprovalsCount: nil
            ),
            SessionListItem(
                sessionID: "mock-startup-tweet",
                startedAt: "2026-06-13T15:00:00Z",
                lastActivity: "2026-06-13T15:40:00Z",
                name: "Rephrasing a startup tweet",
                messageCount: 6,
                sessionType: "chat",
                activeRunID: nil,
                runStatus: nil,
                pendingApprovalsCount: nil
            ),
            SessionListItem(
                sessionID: "mock-job-security",
                startedAt: "2026-06-13T11:00:00Z",
                lastActivity: "2026-06-13T12:05:00Z",
                name: "Job security comparison: US vs EU",
                messageCount: 15,
                sessionType: "chat",
                activeRunID: nil,
                runStatus: nil,
                pendingApprovalsCount: nil
            )
        ]
    }

    static func transcript(for sessionID: String?) -> MobileTranscript {
        let id = sessionID ?? sessions.first?.sessionID
        let approvals = id == "mock-slayer-exciter" ? [
            PendingApproval(
                toolID: "mock-approval-1",
                toolName: "bash",
                preview: "python3 scripts/mosfet_ramp.py --start 5 --stop 20 --step 0.5",
                diff: nil,
                runID: "mock-run-approval"
            )
        ] : []

        let activeRun: ActiveRunSnapshot?
        switch id {
        case "mock-slayer-exciter":
            activeRun = ActiveRunSnapshot(runID: "mock-run-approval", status: "waiting_for_approval")
        case "mock-workflow-demo":
            activeRun = ActiveRunSnapshot(runID: "mock-run-workflow", status: "running")
        default:
            activeRun = nil
        }

        let runtime = SessionRuntimeSnapshot(
            sessionID: id ?? "mock-slayer-exciter",
            latestEventSeq: 42,
            checkpointSeq: 42,
            activeRun: activeRun,
            pendingApprovals: approvals
        )

        var transcript = MobileTranscript()
        transcript.load(history: HistoryResponse(
            messages: messages(for: id),
            activeRunID: runtime.activeRun?.runID,
            runtime: runtime,
            page: nil
        ))

        if id == "mock-workflow-demo" {
            transcript.appendToolChain(id: "demo-tc", steps: demoToolChain())
            transcript.appendWorkflow(id: "demo-wf", demoWorkflow())
            transcript.appendSubagents(id: "demo-sa", demoSubagents())
            transcript.appendArtifact(id: "demo-art", demoArtifact())
        }

        return transcript
    }

    static func reply(for prompt: String) -> String {
        let trimmed = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.localizedCaseInsensitiveContains("status") || trimmed.localizedCaseInsensitiveContains("server") {
            return """
            The server is healthy. Here's the current snapshot:

            - **SSE stream** — stable, 3 active sessions reconnecting cleanly on drop.
            - **Tool approvals** — one pending: a bash call awaiting your review.
            - **Memory** — consolidation ran 11m ago, 4 new facts written.

            Want me to surface the pending approval, or keep monitoring the run?
            """
        }

        if trimmed.localizedCaseInsensitiveContains("mosfet") || trimmed.localizedCaseInsensitiveContains("voltage") || trimmed.localizedCaseInsensitiveContains("20v") {
            return """
            A few options, easiest first:

            - **Bench supply** — what most people use. Turn it up slowly while watching the MOSFET temp; ~$2 of confidence.
            - **PWM the input** — your 555 interrupter already does this. Lower duty = less average power.
            - **Resistor in series** — wastes power as heat but fine for a quick test bench.

            Best approach for your build is the bench supply — slowly ramp to 20V and stop the moment the case gets warm.
            """
        }

        return """
        Got it. Slowly ramp the input and keep an eye on the MOSFET case temperature — if it stays cool you have headroom, if it warms up back off the voltage.
        """
    }

    static func chunks(for reply: String) -> [String] {
        let words = reply.split(separator: " ").map(String.init)
        guard !words.isEmpty else { return [] }
        // Emit a few words per chunk: fewer store republishes → far less
        // main-thread churn while streaming (was one publish per word at ~18Hz).
        let batchSize = 5
        var chunks: [String] = []
        var index = 0
        while index < words.count {
            let end = min(index + batchSize, words.count)
            let isLast = end == words.count
            chunks.append(words[index..<end].joined(separator: " ") + (isLast ? "" : " "))
            index = end
        }
        return chunks
    }

    // MARK: - Rich feature elements (mock)

    enum RichTrigger {
        case toolChain
        case workflow
        case subagents
        case artifact
    }

    static func richTrigger(for prompt: String) -> RichTrigger? {
        let p = prompt.lowercased()
        if p.contains("workflow") || p.contains("orchestrate") || p.contains("orchestration") {
            return .workflow
        }
        if p.contains("subagent") || p.contains("sub-agent") || p.contains("spawn") || p.contains("agents") {
            return .subagents
        }
        if p.contains("tool chain") || p.contains("tool-chain") || p.contains("toolchain") || p.contains("chain of tool") {
            return .toolChain
        }
        if p.contains("artifact") || p.contains("widget") || p.contains("chart") || p.contains("html") {
            return .artifact
        }
        return nil
    }

    static let models = ["Opus 4.8", "Sonnet 4.6", "Haiku 4.5"]
    static let efforts = ["Low", "Medium", "High", "Extra High"]

    // Canned tool output for tapping a single transcript tool row.
    static func toolOutput(forName name: String, command: String) -> (output: String?, diff: String?) {
        switch name {
        case "read_file":
            return ("# Slayer exciter notes\n\n- Drive: 555 @ ~120 kHz into gate\n- MOSFET: IRF540N (Vgs(th) 2–4V, Vds 100V)\n- Caution: keep gate drive ≤ 12–15V; ramp Vdd from a bench supply.", nil)
        case "bash":
            return ("3 active sessions\nlatest_event_seq=42\nrun: idle\nmemory: consolidated 11m ago", nil)
        case "web_search":
            return ("Top results:\n1. IRF540N datasheet (Vishay) — gate charge, SOA curves\n2. Slayer exciter gate drive — 12V regulated rail recommended\n3. 555 interrupter duty-cycle vs heat", nil)
        case "edit_file":
            return (nil, "@@ notes/slayer-exciter.md @@\n-  Drive gate directly from Vdd\n+  Drive gate from a 12V regulated rail; ramp Vdd on a bench supply")
        default:
            return (command.isEmpty ? nil : command, nil)
        }
    }

    static func demoRunTrace() -> [MockRunEvent] {
        [
            MockRunEvent(id: "rt-1", kind: .message, title: "User", detail: "How do I dial the input from 5V up to 20V safely?", at: "0.0s", duration: nil, state: .completed),
            MockRunEvent(id: "rt-2", kind: .thinking, title: "Thinking", detail: "Bench supply ramp is safest; verify gate drive limits first.", at: "0.4s", duration: "4s", state: .completed),
            MockRunEvent(id: "rt-3", kind: .tool, title: "read_file", detail: "notes/slayer-exciter.md", at: "4.6s", duration: "0.1s", state: .completed),
            MockRunEvent(id: "rt-4", kind: .tool, title: "web_search", detail: "IRF540N safe gate drive voltage", at: "4.8s", duration: "1.4s", state: .completed),
            MockRunEvent(id: "rt-5", kind: .thinking, title: "Composing answer", detail: "Three options, bench supply recommended.", at: "6.3s", duration: "2s", state: .completed),
            MockRunEvent(id: "rt-6", kind: .result, title: "Answer", detail: "Bench supply — ramp slowly, watch the case temp.", at: "8.4s", duration: nil, state: .completed)
        ]
    }

    static func demoArtifact() -> MockArtifact {
        MockArtifact(
            id: "art-mosfet-ramp",
            title: "MOSFET ramp plan",
            kind: "HTML",
            updated: "just now",
            html: """
            <!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
              :root { color-scheme: light dark; }
              body { font: -apple-system-body, -apple-system, system-ui, sans-serif; margin: 0; padding: 20px;
                     background: Canvas; color: CanvasText; -webkit-text-size-adjust: 100%; }
              h1 { font-size: 20px; margin: 0 0 4px; letter-spacing: -0.3px; }
              p.sub { margin: 0 0 18px; color: color-mix(in srgb, CanvasText 55%, transparent); font-size: 13px; }
              .step { display: flex; gap: 12px; align-items: baseline; padding: 12px 0; border-top: 0.5px solid color-mix(in srgb, CanvasText 18%, transparent); }
              .v { font-variant-numeric: tabular-nums; font-weight: 600; width: 56px; color: #2B6CF0; }
              .bar { height: 8px; border-radius: 4px; background: #2B6CF0; margin-top: 16px; }
              code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }
            </style></head><body>
              <h1>IRF540N ramp plan</h1>
              <p class="sub">Bench-supply ramp, watch the case temp</p>
              <div class="step"><span class="v">5 V</span><span>idle bias — confirm 555 oscillation on the gate</span></div>
              <div class="step"><span class="v">10 V</span><span>first power — listen for the arc, check Vgs ≤ 12V</span></div>
              <div class="step"><span class="v">15 V</span><span>watch MOSFET case; back off if it warms</span></div>
              <div class="step"><span class="v">20 V</span><span>target — stop the moment the case gets hot</span></div>
              <div class="bar" style="width: 64%"></div>
              <p class="sub" style="margin-top:8px">Est. safe headroom at 20V: <code>~64%</code></p>
            </body></html>
            """
        )
    }

    static func demoToolChain() -> [MockToolStep] {
        [
            MockToolStep(id: "tc-1", name: "bash", detail: "rg -n \"slayer\" notes/", state: .completed, duration: "0.2s",
                         output: "notes/slayer-exciter.md:1:# Slayer exciter notes\nnotes/slayer-exciter.md:14:gate drive from Vdd"),
            MockToolStep(id: "tc-2", name: "read_file", detail: "notes/slayer-exciter.md", state: .completed, duration: "0.1s",
                         output: "# Slayer exciter notes\n\n- Drive: 555 @ ~120 kHz into gate\n- MOSFET: IRF540N (Vds 100V)\n- Caution: keep gate drive ≤ 12–15V"),
            MockToolStep(id: "tc-3", name: "web_search", detail: "IRF540N safe gate drive voltage", state: .completed, duration: "1.4s",
                         output: "1. IRF540N datasheet — Vgs(th) 2–4V, abs max Vgs ±20V\n2. Slayer exciter — use a 12V regulated gate rail\n3. Ramp Vdd slowly on a bench supply"),
            MockToolStep(id: "tc-4", name: "edit_file", detail: "notes/slayer-exciter.md", state: .running, duration: nil,
                         diff: "@@ notes/slayer-exciter.md @@\n-  Drive gate directly from Vdd\n+  Drive gate from a 12V regulated rail; ramp Vdd on a bench supply")
        ]
    }

    static func demoWorkflow() -> MockWorkflow {
        MockWorkflow(
            id: "wf-research",
            name: "Research & synthesize",
            state: .running,
            elapsed: "1m 12s",
            tokens: "18.4k",
            phases: [
                MockWorkflowPhase(id: "p-research", name: "research", state: .running, agents: [
                    MockWorkflowAgent(id: "a1", name: "Datasheet specs", state: .completed, tokens: "3.3k", elapsed: "38s"),
                    MockWorkflowAgent(id: "a2", name: "Driver topologies", state: .completed, tokens: "2.3k", elapsed: "41s"),
                    MockWorkflowAgent(id: "a3", name: "Thermal limits", state: .running, tokens: "1.1k", elapsed: "22s")
                ]),
                MockWorkflowPhase(id: "p-synth", name: "synthesis", state: .pending, agents: [
                    MockWorkflowAgent(id: "a4", name: "Compose recommendation", state: .pending, tokens: nil, elapsed: nil)
                ])
            ]
        )
    }

    static func demoSubagents() -> [MockSubagent] {
        [
            MockSubagent(
                id: "sa-1", type: "Research", name: "Investigate gate drive options",
                state: .running, detail: "Comparing bench supply vs PWM dimming…", elapsed: "45s", detached: false,
                model: "Sonnet 4.6 · Medium",
                result: nil,
                trace: [
                    MockRunEvent(id: "sa1-e1", kind: .thinking, title: "Planning", detail: "Two paths: bench supply ramp vs PWM duty on the 555.", at: "0.0s", duration: "3s", state: .completed),
                    MockRunEvent(id: "sa1-e2", kind: .tool, title: "web_search", detail: "IRF540N gate drive voltage limits", at: "3.2s", duration: "1.4s", state: .completed),
                    MockRunEvent(id: "sa1-e3", kind: .tool, title: "read_file", detail: "notes/slayer-exciter.md", at: "4.8s", duration: "0.1s", state: .completed),
                    MockRunEvent(id: "sa1-e4", kind: .thinking, title: "Comparing options", detail: "Weighing safety vs effort…", at: "5.0s", duration: nil, state: .running)
                ]
            ),
            MockSubagent(
                id: "sa-2", type: "Code review", name: "Review mosfet_ramp.py",
                state: .completed, detail: "Found 2 issues: missing current limit, no temp cutoff.", elapsed: "2m 10s", detached: false,
                model: "Opus 4.8 · High",
                result: "**2 issues found:**\n\n- No current limit on the ramp — add a hard cap before stepping Vdd.\n- No temperature cutoff — read the MOSFET case temp and abort above 60°C.\n\nOtherwise the step logic is sound.",
                trace: [
                    MockRunEvent(id: "sa2-e1", kind: .tool, title: "read_file", detail: "scripts/mosfet_ramp.py", at: "0.0s", duration: "0.1s", state: .completed),
                    MockRunEvent(id: "sa2-e2", kind: .thinking, title: "Analyzing ramp loop", detail: "Checking bounds + safety guards.", at: "0.3s", duration: "1m 40s", state: .completed),
                    MockRunEvent(id: "sa2-e3", kind: .result, title: "Final answer", detail: "2 issues: current limit, temp cutoff.", at: "2m 08s", duration: nil, state: .completed)
                ]
            ),
            MockSubagent(
                id: "sa-3", type: "Agent", name: "Draft a parts BOM",
                state: .failed, detail: "Rate limit hit on the parts API.", elapsed: "1m 02s", detached: true,
                model: "Haiku 4.5 · Low",
                result: nil,
                trace: [
                    MockRunEvent(id: "sa3-e1", kind: .tool, title: "web_search", detail: "IRF540N suppliers + pricing", at: "0.0s", duration: "0.9s", state: .completed),
                    MockRunEvent(id: "sa3-e2", kind: .tool, title: "http_get", detail: "api.parts.example/quote", at: "0.9s", duration: "60s", state: .failed),
                    MockRunEvent(id: "sa3-e3", kind: .error, title: "Run failed", detail: "429 Too Many Requests — rate limit on the parts API.", at: "1m 01s", duration: nil, state: .failed)
                ]
            )
        ]
    }

    static var automations: [MockAutomation] {
        [
            MockAutomation(
                id: "auto-standup",
                name: "Daily standup",
                description: "Summarize today's progress and blockers for the team.",
                enabled: true,
                schedule: "Weekdays · 9:00",
                trigger: "time",
                lastState: .completed,
                lastRun: "5h ago",
                nextRun: "Tomorrow, 9:00",
                builtin: false,
                runs: [
                    MockAutomationRun(id: "r-1", state: .completed, started: "Today, 09:00", duration: "2m 34s", summary: "Shipped IR dropout fix; rate limiter in progress; blocked on design review."),
                    MockAutomationRun(id: "r-2", state: .completed, started: "Yesterday, 09:00", duration: "1m 52s", summary: "Rate limiter refactor started."),
                    MockAutomationRun(id: "r-3", state: .failed, started: "Jun 12, 09:00", duration: "0m 12s", summary: "Failed to reach team calendar API — check credentials.")
                ]
            ),
            MockAutomation(
                id: "auto-slack",
                name: "Slack monitor",
                description: "Watch #eng-bugs for errors and open tickets.",
                enabled: true,
                schedule: "#eng-bugs · keywords",
                trigger: "message",
                lastState: .completed,
                lastRun: "1h ago",
                nextRun: nil,
                builtin: false,
                runs: [
                    MockAutomationRun(id: "r-4", state: .completed, started: "Today, 16:23", duration: "0m 48s", summary: "Created ticket ARDEN-2847 from error report.")
                ]
            ),
            MockAutomation(
                id: "auto-memory",
                name: "Memory consolidation",
                description: "Periodically consolidate and reflect on knowledge.",
                enabled: true,
                schedule: "Daily · 23:00",
                trigger: "time",
                lastState: .completed,
                lastRun: "1h ago",
                nextRun: "Today, 23:00",
                builtin: true,
                runs: [
                    MockAutomationRun(id: "r-5", state: .completed, started: "Today, 15:15", duration: "3m 40s", summary: "Consolidated 6 observations into 4 facts.")
                ]
            )
        ]
    }

    private static func messages(for sessionID: String?) -> [HistoryMessage] {
        switch sessionID {
        case "mock-project-overview":
            return [
                history("user", "Give me a quick status of the ARDEN server — what's running and any blockers?"),
                history("assistant", """
                The server is healthy. Here's the current snapshot:

                - **SSE stream** — stable, 3 active sessions reconnecting cleanly on drop.
                - **Tool approvals** — one pending: a `bash` call awaiting your review.
                - **Memory** — consolidation ran 11m ago, 4 new facts written.
                """),
                tool("bash", "uv run arden-server status --json"),
                history("assistant", "Want me to surface the pending approval, or keep monitoring the run?")
            ]
        case "mock-xcode-incompat":
            return [
                history("user", "After updating macOS, Xcode refuses to open my project — says the toolchain is incompatible. Fix?"),
                history("assistant", """
                That's the SDK mismatch after a major macOS bump. Update Command Line Tools and point Xcode at them:

                Run `xcode-select --install`, then accept the new license with `sudo xcodebuild -license accept`.
                """),
                tool("bash", "xcode-select -p"),
                history("assistant", "Tools path looks stale. Reset it and the project should open cleanly.")
            ]
        case "mock-cold-outreach":
            return [
                history("user", "Can you review this cold outreach message? It feels too long and salesy."),
                history("assistant", """
                It is a bit long. The opening line does the work — lead with the specific reason you're reaching out, then one sentence of value, then a low-friction ask.

                Cut the second paragraph entirely; it repeats the pitch.
                """),
                history("user", "Good call. What about the subject line?"),
                history("assistant", "Make it concrete and personal: reference their recent launch instead of \"quick question\". Specific subjects get opened.")
            ]
        case "mock-startup-tweet":
            return [
                history("user", "Help me rephrase this startup tweet so it sounds less corporate."),
                history("assistant", """
                Drop the buzzwords and say the real thing. \"Leveraging AI to optimize workflows\" → \"We built a tool that does the boring half of your job.\"

                Shorter, concrete, and it actually sounds like a person wrote it.
                """),
                history("user", "Love it. Keep that energy."),
                history("assistant", "Done — that version will read far better in the timeline.")
            ]
        case "mock-job-security":
            return [
                history("user", "How does job security for software engineers compare between the US and EU?"),
                history("assistant", """
                Broad strokes: the US trades security for upside, the EU trades upside for stability.

                - **US** — at-will employment, faster layoffs, but higher comp and more mobility.
                - **EU** — strong protections, notice periods, and severance baked into law; harder to fire, slower to hire.
                """),
                history("user", "So EU is strictly safer?"),
                history("assistant", "Safer day-to-day, yes — but in a downturn the rigidity can mean slower rehiring. Different shape of risk, not strictly less of it.")
            ]
        case "mock-workflow-demo":
            return [
                history("user", "Spin up a multi-agent run to research safe gate-drive options for the IRF540N and synthesize a recommendation."),
                history("assistant", "On it — orchestrating a research workflow with parallel agents, plus a couple of focused subagents. Live status below.")
            ]
        default:
            return [
                history("user", "How do I dial the input from 5V up to 20V safely without frying the MOSFET?"),
                history("assistant", """
                A few options, easiest first:

                - **Bench supply** — what most people use. Turn it up slowly while watching the MOSFET temp; ~$2 of confidence.
                - **PWM the input** — your 555 interrupter already does this. Lower duty = less average power.
                - **Resistor in series** — wastes power as heat but fine for a quick test bench.
                """),
                tool("read_file", "notes/slayer-exciter.md"),
                history("assistant", "Best approach for your build is the bench supply — slowly ramp to 20V and stop the moment the case gets warm.")
            ]
        }
    }

    private static func history(_ role: String, _ content: String) -> HistoryMessage {
        HistoryMessage(
            role: role,
            content: content,
            reasoningContent: nil,
            toolCalls: nil,
            toolCallID: nil,
            id: UUID().uuidString,
            messageID: nil,
            seq: nil,
            createdAt: nil,
            isMeta: nil
        )
    }

    private static func tool(_ name: String, _ content: String) -> HistoryMessage {
        HistoryMessage(
            role: "tool",
            content: content,
            reasoningContent: nil,
            toolCalls: [HistoryToolCall(id: UUID().uuidString, name: name, arguments: "{}", displayName: name, kind: "local")],
            toolCallID: nil,
            id: UUID().uuidString,
            messageID: nil,
            seq: nil,
            createdAt: nil,
            isMeta: nil
        )
    }
}
