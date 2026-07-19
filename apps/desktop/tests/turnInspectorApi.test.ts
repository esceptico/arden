import { expect, test } from "bun:test";
import { normalizeTurnInspector } from "@/api/turnInspector";

test("normalizes typed turn inspector data and drops malformed rows", () => {
  const normalized = normalizeTurnInspector({
    run_id: "run-1",
    session_id: "session-1",
    updated_at: "2026-07-20T12:00:00Z",
    context_manifest: [
      {
        context_id: "ctx-1",
        content_type: "memory",
        source: "memory",
        ref: "record:1",
        freshness: "current",
        selection_reason: "relevant",
        size_bytes: 42,
      },
      { context_id: 4 },
    ],
    evidence: {
      sources: [{ provider: "web", kind: "page", ref: "doc:1", title: "Docs" }, { provider: null }],
      approvals: [{ tool_call_id: "call-1", tool_name: "send_email", status: "approved" }],
      effects: [{ tool_call_id: "call-1", operation: "send", target: "message:42" }],
      receipts: [{ tool_call_id: "call-1", receipt: "provider:42" }],
      checks: [{ tool_call_id: "call-1", postcondition: "exists", observed: "message:42", confidence: 1 }],
      limitations: [{
        tool_call_id: "call-2",
        status: "uncertain",
        code: "execution_state_uncertain",
        recovery_action: "Read before retrying.",
      }],
    },
  });

  expect(normalized).toMatchObject({
    runId: "run-1",
    sessionId: "session-1",
    context: [{ id: "ctx-1", contentType: "memory", sizeBytes: 42 }],
    evidence: {
      sources: [{ provider: "web", ref: "doc:1" }],
      approvals: [{ toolCallId: "call-1", status: "approved" }],
      effects: [{ toolCallId: "call-1", target: "message:42" }],
      receipts: [{ toolCallId: "call-1", receipt: "provider:42" }],
      checks: [{ toolCallId: "call-1", confidence: 1 }],
      limitations: [{ toolCallId: "call-2", recoveryAction: "Read before retrying." }],
    },
  });
});

test("returns null for invalid envelopes and caps every rendered group", () => {
  expect(normalizeTurnInspector(null)).toBeNull();
  expect(normalizeTurnInspector({ run_id: "run-1" })).toBeNull();

  const rows = Array.from({ length: 60 }, (_, index) => ({
    tool_call_id: `call-${index}`,
    operation: "write",
    target: `file-${index}`,
  }));
  const normalized = normalizeTurnInspector({
    run_id: "run-1",
    session_id: "session-1",
    updated_at: "now",
    context_manifest: [],
    evidence: { effects: rows },
  });

  expect(normalized?.evidence.effects).toHaveLength(50);
});
