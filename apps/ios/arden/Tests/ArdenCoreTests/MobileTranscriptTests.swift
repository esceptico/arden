import XCTest
@testable import ArdenCore

final class MobileTranscriptTests: XCTestCase {
    func testBuildsAssistantMessageFromStreamingDeltas() throws {
        var transcript = MobileTranscript()

        transcript.apply(try decodeEvent(#"{"type":"RUN_STARTED","run_id":"run-1","session_id":"sess-1","seq":1}"#))
        transcript.apply(try decodeEvent(#"{"type":"TEXT_MESSAGE_START","message_id":"msg-1","role":"assistant","seq":2}"#))
        transcript.apply(try decodeEvent(#"{"type":"TEXT_MESSAGE_CONTENT","message_id":"msg-1","delta":"hello","seq":3}"#))
        transcript.apply(try decodeEvent(#"{"type":"TEXT_MESSAGE_CONTENT","message_id":"msg-1","delta":" world","seq":4}"#))
        transcript.apply(try decodeEvent(#"{"type":"TEXT_MESSAGE_END","message_id":"msg-1","content":"hello world","seq":5}"#))
        transcript.apply(try decodeEvent(#"{"type":"RUN_FINISHED","run_id":"run-1","seq":6}"#))

        XCTAssertEqual(transcript.latestSeq, 6)
        XCTAssertNil(transcript.activeRunID)
        XCTAssertEqual(transcript.messages.count, 1)
        XCTAssertEqual(transcript.messages[0].id, "msg-1")
        XCTAssertEqual(transcript.messages[0].role, .assistant)
        XCTAssertEqual(transcript.messages[0].content, "hello world")
        XCTAssertFalse(transcript.messages[0].isStreaming)
    }

    func testTracksPendingApprovalFromStreamEvent() throws {
        var transcript = MobileTranscript()

        transcript.apply(try decodeEvent(#"{"type":"RUN_STARTED","run_id":"run-2","session_id":"sess-1","seq":1}"#))
        transcript.apply(try decodeEvent(#"{"type":"approval_needed","tool_id":"tool-1","name":"bash","content_preview":"echo hi","diff":"--- a","seq":2}"#))

        XCTAssertEqual(transcript.pendingApprovals.count, 1)
        XCTAssertEqual(transcript.pendingApprovals[0].toolID, "tool-1")
        XCTAssertEqual(transcript.pendingApprovals[0].toolName, "bash")
        XCTAssertEqual(transcript.pendingApprovals[0].preview, "echo hi")
        XCTAssertEqual(transcript.pendingApprovals[0].runID, "run-2")
    }

    // MARK: - Workflow rehydration + reset recovery (regression)

    // Persisted workflow-domain events, in the shape `/chat/{id}/workflows` returns.
    private let workflowEventsJSON = [
        #"{"type":"workflow_started","workflow_id":"wf-1","name":"Research","phases":["Plan","Build"],"seq":1}"#,
        #"{"type":"task_started","workflow_id":"wf-1","task_id":"t1","phase":"Plan","name":"Planner","agent_type":"plan","seq":2}"#,
        #"{"type":"token_usage","workflow_id":"wf-1","task_id":"t1","phase":"Plan","usage":{"prompt":100,"completion":50,"total":150},"seq":3}"#,
        #"{"type":"task_finished","workflow_id":"wf-1","task_id":"t1","phase":"Plan","status":"completed","seq":4}"#,
        #"{"type":"workflow_finished","workflow_id":"wf-1","status":"completed","seq":5}"#,
    ]

    private func applyWorkflowEvents(_ transcript: inout MobileTranscript) throws {
        for json in workflowEventsJSON {
            transcript.apply(try decodeEvent(json))
        }
    }

    private func assertResearchCard(_ transcript: MobileTranscript, file: StaticString = #filePath, line: UInt = #line) {
        let cards = transcript.messages.filter { $0.role == .workflow }
        XCTAssertEqual(cards.count, 1, "exactly one workflow card", file: file, line: line)
        guard let workflow = cards.first?.workflow else {
            return XCTFail("workflow card has no payload", file: file, line: line)
        }
        XCTAssertEqual(workflow.name, "Research", file: file, line: line)
        XCTAssertEqual(workflow.state, .completed, file: file, line: line)
        // "Build" had no agent, so workflow_finished prunes it; only "Plan" remains.
        XCTAssertEqual(workflow.phases.map(\.name), ["Plan"], file: file, line: line)
        let plan = workflow.phases[0]
        XCTAssertEqual(plan.state, .completed, file: file, line: line)
        XCTAssertEqual(plan.agents.count, 1, file: file, line: line)
        XCTAssertEqual(plan.agents[0].name, "Planner", file: file, line: line)
        XCTAssertEqual(plan.agents[0].state, .completed, file: file, line: line)
        XCTAssertEqual(plan.agents[0].tokens, "150", file: file, line: line)
    }

    func testBuildsWorkflowCardFromEvents() throws {
        var transcript = MobileTranscript()
        try applyWorkflowEvents(&transcript)
        assertResearchCard(transcript)
    }

    func testHistoryLoadClearsLiveWorkflowDomain() throws {
        var transcript = MobileTranscript()
        try applyWorkflowEvents(&transcript)
        XCTAssertEqual(transcript.messages.filter { $0.role == .workflow }.count, 1)

        transcript.load(history: try decodeHistory(#"{"messages":[]}"#))
        XCTAssertTrue(transcript.messages.isEmpty, "history rebuild drops the projected card")

        // The live domain must be gone too: a later task event for the same
        // workflow_id with no fresh workflow_started is dropped by the guard,
        // so no ghost card reappears (this is what bled across sessions before).
        transcript.apply(try decodeEvent(#"{"type":"task_progress","workflow_id":"wf-1","task_id":"t1","phase":"Plan","seq":10}"#))
        XCTAssertTrue(transcript.messages.filter { $0.role == .workflow }.isEmpty)
    }

    func testReconnectReplayRebuildsWorkflowCard() throws {
        var transcript = MobileTranscript()
        try applyWorkflowEvents(&transcript)            // live build
        transcript.load(history: try decodeHistory(#"{"messages":[]}"#))  // reconnect wipes
        XCTAssertTrue(transcript.messages.isEmpty)
        try applyWorkflowEvents(&transcript)            // rehydration replay
        assertResearchCard(transcript)                  // card is rebuilt, not double-counted
    }

    func testUntaggedTaskBecomesStandaloneSubagentNotWorkflow() throws {
        var transcript = MobileTranscript()
        transcript.apply(try decodeEvent(#"{"type":"task_started","task_id":"s1","name":"Explorer","agent_type":"explore","seq":1}"#))

        XCTAssertTrue(transcript.messages.filter { $0.role == .workflow }.isEmpty)
        let subagentCards = transcript.messages.filter { $0.role == .subagents }
        XCTAssertEqual(subagentCards.count, 1)
        XCTAssertEqual(subagentCards.first?.subagents?.first?.name, "Explorer")
    }

    private func decodeEvent(_ json: String) throws -> StreamEvent {
        try JSONDecoder().decode(StreamEvent.self, from: Data(json.utf8))
    }

    private func decodeHistory(_ json: String) throws -> HistoryResponse {
        try JSONDecoder().decode(HistoryResponse.self, from: Data(json.utf8))
    }
}
