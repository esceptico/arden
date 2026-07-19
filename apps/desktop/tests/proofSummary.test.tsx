import { afterEach, beforeEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ProofSummary } from "@/features/context/components/ProofSummary";
import { setState } from "@/stores";

const roots = new Set<Root>();

function setup() {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  roots.add(root);
  return { host, root };
}

beforeEach(() => {
  setState({
    messages: new Map([
      ["user-1", { id: "user-1", role: "user", content: "send it" }],
      ["activity-1", {
        id: "activity-1",
        role: "activity",
        content: "",
        activity: {
          label: "Called",
          done: true,
          items: [{
            id: "call-1",
            kind: "send_email",
            displayName: "Send email",
            target: "alice@example.com",
            outcome: {
              status: "succeeded",
              effect: { operation: "send", target: "message:42" },
              verification: { postcondition: "message exists", observed: "message:42" },
              receipt: "provider:42",
            },
            sourceRefs: [{ provider: "gmail", kind: "message", ref: "42", title: "Sent email" }],
          }],
        },
      }],
      ["assistant-1", { id: "assistant-1", role: "assistant", content: "Sent" }],
    ]),
    order: ["user-1", "activity-1", "assistant-1"],
    sourceRefsRevision: 1,
  });
});

afterEach(async () => {
  for (const root of roots) await act(async () => root.unmount());
  roots.clear();
  document.body.replaceChildren();
});

test("proof summary is collapsed by default and expands outcome details", async () => {
  const { host, root } = setup();
  await act(async () => root.render(<ProofSummary turnId="user-1" />));

  const toggle = host.querySelector('button[aria-label="Expand outcome evidence"]') as HTMLButtonElement;
  expect(toggle?.getAttribute("aria-expanded")).toBe("false");
  expect(host.textContent).toContain("Send email completed");
  expect(host.textContent).not.toContain("1 action");
  expect(host.textContent).not.toContain("1 source");
  expect(host.textContent).not.toContain("message exists");

  await act(async () => toggle.click());
  expect(host.textContent).toContain("message exists");
  expect(host.textContent).toContain("provider:42");
  expect(host.textContent).not.toContain("View turn details");
});

test("proof summary renders nothing for a turn without evidence", async () => {
  setState({
    messages: new Map([
      ["user-2", { id: "user-2", role: "user", content: "hello" }],
      ["assistant-2", { id: "assistant-2", role: "assistant", content: "hi" }],
    ]),
    order: ["user-2", "assistant-2"],
  });
  const { host, root } = setup();
  await act(async () => root.render(<ProofSummary turnId="user-2" />));
  expect(host.innerHTML).toBe("");
});

test("proof summary renders nothing when a turn only has sources", async () => {
  setState({
    messages: new Map([
      ["user-2", { id: "user-2", role: "user", content: "check email" }],
      ["activity-2", {
        id: "activity-2",
        role: "activity",
        content: "",
        activity: {
          label: "Called",
          done: true,
          items: [{
            id: "call-2",
            kind: "emails",
            target: "inbox",
            sourceRefs: [{ provider: "gmail", kind: "message", ref: "42", title: "Email" }],
          }],
        },
      }],
      ["assistant-2", { id: "assistant-2", role: "assistant", content: "Summary" }],
    ]),
    order: ["user-2", "activity-2", "assistant-2"],
  });
  const { host, root } = setup();
  await act(async () => root.render(<ProofSummary turnId="user-2" />));
  expect(host.innerHTML).toBe("");
});
