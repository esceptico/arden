import { expect, test } from "bun:test";
import { latestInspectableTurnId, turnProofSummary } from "@/features/context/lib/turnProof";
import type { ActivityItem, UiMessage } from "@/stores";

function activity(id: string, items: ActivityItem[]): UiMessage {
  return {
    id,
    role: "activity",
    content: "",
    activity: { label: "Called", done: true, items },
  };
}

function transcript(messages: UiMessage[]) {
  return {
    messages: new Map(messages.map((message) => [message.id, message])),
    order: messages.map((message) => message.id),
  };
}

test("suppresses proof for turns without structured outcomes or sources", () => {
  const state = transcript([
    { id: "user-1", role: "user", content: "hello" },
    { id: "assistant-1", role: "assistant", content: "hi" },
  ]);

  expect(turnProofSummary(state.messages, state.order, "user-1")).toBeNull();
});

test("suppresses proof when sources are the only evidence", () => {
  const state = transcript([
    { id: "user-1", role: "user", content: "check email" },
    activity("activity-1", [{
      id: "call-1",
      kind: "emails",
      target: "inbox",
      sourceRefs: [{ provider: "gmail", kind: "message", ref: "42", title: "Email" }],
    }]),
    { id: "assistant-1", role: "assistant", content: "Summary" },
  ]);

  expect(turnProofSummary(state.messages, state.order, "user-1")).toBeNull();
});

test("summarizes durable actions checks and receipts", () => {
  const state = transcript([
    { id: "user-1", role: "user", content: "send it" },
    activity("activity-1", [
      {
        id: "call-1",
        kind: "send_email",
        displayName: "Send email",
        target: "alice@example.com",
        outcome: {
          status: "succeeded",
          effect: { operation: "send", target: "message:42" },
          verification: { postcondition: "message exists", observed: "message:42", confidence: 1 },
          receipt: "provider:42",
        },
        sourceRefs: [
          { provider: "gmail", kind: "message", ref: "42", title: "Sent email" },
          { provider: "gmail", kind: "message", ref: "42", title: "Sent email duplicate" },
        ],
      },
    ]),
    { id: "assistant-1", role: "assistant", content: "Sent" },
  ]);

  expect(turnProofSummary(state.messages, state.order, "user-1")).toEqual({
    tone: "recorded",
    actionCount: 1,
    checkCount: 1,
    receiptCount: 1,
    limitationCount: 0,
    actions: [{ toolCallId: "call-1", toolLabel: "Send email", operation: "send", target: "message:42" }],
    checks: [{
      toolCallId: "call-1",
      toolLabel: "Send email",
      postcondition: "message exists",
      observed: "message:42",
      confidence: 1,
    }],
    receipts: [{ toolCallId: "call-1", toolLabel: "Send email", receipt: "provider:42" }],
    limitations: [],
  });
});

test("failed denied and uncertain outcomes take attention precedence", () => {
  const state = transcript([
    { id: "user-1", role: "user", content: "change it" },
    activity("activity-1", [
      {
        id: "call-1",
        kind: "calendar_edit",
        target: "event:1",
        outcome: {
          status: "uncertain",
          error: {
            code: "execution_state_uncertain",
            retryable: false,
            recovery_action: "Read the event before retrying.",
          },
        },
      },
    ]),
    { id: "assistant-1", role: "assistant", content: "I could not confirm it" },
  ]);

  expect(turnProofSummary(state.messages, state.order, "user-1")).toMatchObject({
    tone: "attention",
    limitationCount: 1,
    limitations: [{
      toolCallId: "call-1",
      toolLabel: "calendar_edit",
      status: "uncertain",
      code: "execution_state_uncertain",
      recoveryAction: "Read the event before retrying.",
    }],
  });
});

test("caps expanded proof rows without changing total counts", () => {
  const state = transcript([
    { id: "user-1", role: "user", content: "write files" },
    activity(
      "activity-1",
      Array.from({ length: 7 }, (_, index) => ({
        id: `call-${index}`,
        kind: "write_file",
        target: `file-${index}.txt`,
        outcome: {
          status: "succeeded" as const,
          effect: { operation: "write", target: `file-${index}.txt` },
        },
      })),
    ),
    { id: "assistant-1", role: "assistant", content: "Done" },
  ]);

  const summary = turnProofSummary(state.messages, state.order, "user-1");

  expect(summary?.actionCount).toBe(7);
  expect(summary?.actions).toHaveLength(5);
});

test("selects the latest visible or hidden-meta output turn", () => {
  const ordinary = transcript([
    { id: "user-1", role: "user", content: "first" },
    { id: "assistant-1", role: "assistant", content: "first answer" },
    { id: "user-2", role: "user", content: "second" },
    { id: "assistant-2", role: "assistant", content: "second answer" },
  ]);
  const meta = transcript([
    ...ordinary.messages.values(),
    { id: "meta-user-run-3", role: "user", content: "hidden", isMeta: true } as UiMessage,
    { id: "assistant-3", role: "assistant", content: "automated answer" } as UiMessage,
  ]);

  expect(latestInspectableTurnId(ordinary.messages, ordinary.order)).toBe("user-2");
  expect(latestInspectableTurnId(meta.messages, meta.order)).toBe("meta-user-run-3");
});
