import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Composer } from "@/features/chat/components/Composer";
import { ComposerToolbar, type ComposerAction } from "@/features/chat/components/ComposerToolbar";
import { QueueCard } from "@/features/chat/components/QueueCard";
import { queueStackExtentPx } from "@/features/chat/lib/queue";
import { getState, setState, type QueuedMessage } from "@/stores";

function mount(): { el: HTMLElement; root: Root; restore: () => void } {
  const el = document.createElement("div");
  document.body.append(el);
  return { el, root: createRoot(el), restore: () => el.remove() };
}

function renderToolbar(action: ComposerAction, onStop: () => void) {
  // `action` is deliberately a public toolbar contract: the parent determines
  // it from run state + draft state, while this presentational control owns its
  // matching button semantics and retained-glyph transition.
  const props = {
    action,
    onAttach: () => {},
    skipApprovals: false,
    onToggleAuto: () => {},
    sendDisabled: false,
    working: false,
    thinkingAnimation: "comet" as const,
    thinkingIntensity: "normal" as const,
    onStop,
  } satisfies Parameters<typeof ComposerToolbar>[0];
  return <ComposerToolbar {...props} />;
}

const QUEUED: QueuedMessage[] = [
  { clientId: "queue-1", text: "First queued message", status: "pending", enqueuedAt: 1 },
  { clientId: "queue-2", text: "Second queued message", status: "pending", enqueuedAt: 2 },
  { clientId: "queue-3", text: "Third queued message", status: "pending", enqueuedAt: 3 },
  { clientId: "queue-4", text: "This must remain visually capped", status: "pending", enqueuedAt: 4 },
];

test("collapsed queue stack reserves one compact card above the composer", () => {
  expect([0, 1, 2, 3, 10].map(queueStackExtentPx)).toEqual([0, 52, 64, 76, 76]);
});

test("local queue is capped at ten messages", () => {
  const previousQueued = getState().queuedMessages;
  try {
    setState({ queuedMessages: [] });
    for (let index = 0; index < 11; index += 1) {
      getState().addQueuedMessage({
        clientId: `queue-${index}`,
        text: String(index),
        status: "pending",
        enqueuedAt: index,
      });
    }
    expect(getState().queuedMessages).toHaveLength(10);
  } finally {
    setState({ queuedMessages: previousQueued });
  }
});

test("composer send control preserves semantic send, queue, and stop modes", async () => {
  const previousSessionId = getState().currentSessionId;
  setState({ currentSessionId: null });
  try {
    for (const action of ["send", "queue", "stop"] as const) {
      const { el, root, restore } = mount();
      let stops = 0;
      try {
        await act(async () => {
          root.render(renderToolbar(action, () => { stops += 1; }));
        });

        const control = el.querySelector<HTMLButtonElement>(".board-composer__send")!;
        expect(control).not.toBeNull();
        expect(control.dataset.mode).toBe(action);
        expect(control.type).toBe(action === "stop" ? "button" : "submit");
        expect(control.getAttribute("aria-label")).toBe(
          action === "stop" ? "Stop response" : action === "queue" ? "Queue message" : "Send",
        );
        expect(control.querySelector(".t-icon-swap")?.getAttribute("data-state")).toBe(
          action === "stop" ? "b" : "a",
        );

        await act(async () => {
          control.click();
        });
        expect(stops).toBe(action === "stop" ? 1 : 0);
      } finally {
        await act(async () => root.unmount());
        restore();
      }
    }
  } finally {
    setState({ currentSessionId: previousSessionId });
  }
});

test("queue keeps the complete FIFO behind a three-card collapsed preview", async () => {
  const previousQueued = getState().queuedMessages;
  const { el, root, restore } = mount();
  try {
    setState({ queuedMessages: QUEUED });
    await act(async () => {
      root.render(<QueueCard onEdit={() => {}} />);
    });
    const list = el.querySelector<HTMLElement>('[role="list"][aria-label="Queued messages"]')!;
    const items = Array.from(list.querySelectorAll<HTMLElement>('[role="listitem"]'));
    expect(list).not.toBeNull();
    expect(items).toHaveLength(4);

    for (const item of items) {
      expect(item.classList.contains("board-queue-stack__item")).toBe(true);
      expect(item.querySelector(".board-queue-stack__actions")).not.toBeNull();
      expect(item.querySelector<HTMLButtonElement>('[aria-label="Remove queued message"]')).not.toBeNull();
      expect(item.querySelector<HTMLButtonElement>('[aria-label="Edit queued message"]')).not.toBeNull();
    }
  } finally {
    await act(async () => root.unmount());
    restore();
    setState({ queuedMessages: previousQueued });
  }
});

test("queue cancellation is immediate and local", async () => {
  const previousQueued = getState().queuedMessages;
  const previousSessionId = getState().currentSessionId;
  const previousConfig = getState().config;
  const { el, root, restore } = mount();
  try {
    setState({
      config: { serverUrl: "http://localhost:6877", apiKey: "" },
      currentSessionId: "session-queue",
      queuedMessages: QUEUED,
    });
    await act(async () => {
      root.render(<QueueCard onEdit={() => {}} />);
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    const cancel = el.querySelector<HTMLButtonElement>('[aria-label="Remove queued message"]')!;
    await act(async () => {
      cancel.click();
      await Promise.resolve();
    });

    expect(getState().queuedMessages.map((message) => message.clientId)).toEqual([
      "queue-2",
      "queue-3",
      "queue-4",
    ]);
  } finally {
    await act(async () => root.unmount());
    restore();
    setState({
      config: previousConfig,
      currentSessionId: previousSessionId,
      queuedMessages: previousQueued,
    });
  }
});

test("a nonempty running composer queues through the submit control instead of stopping", async () => {
  const previous = {
    config: getState().config,
    connected: getState().connected,
    currentSessionId: getState().currentSessionId,
    currentRunId: getState().currentRunId,
    running: getState().running,
    activeRunSessionIds: getState().activeRunSessionIds,
    draft: getState().draft,
    queuedMessages: getState().queuedMessages,
    pendingApprovals: getState().pendingApprovals,
    selectedSkill: getState().selectedSkill,
    pendingImages: getState().pendingImages,
    commandPickerOpen: getState().commandPickerOpen,
    commandPickerIndex: getState().commandPickerIndex,
    loops: getState().loops,
  };
  const previousDesktop = window.ardenDesktop;
  const requests: Array<{ path: string; method: string; body?: string }> = [];
  const { el, root, restore } = mount();
  try {
    window.ardenDesktop = {
      api: {
        request: async (_config, request) => {
          requests.push(request);
          const data = request.path.startsWith("/loops?")
            ? { loops: [] }
            : { run_id: "run-queue", session_id: "session-queue", status: "queued" };
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            contentType: "application/json",
            data,
            text: "",
          };
        },
      },
    };
    setState({
      config: { serverUrl: "http://localhost:6877", apiKey: "" },
      connected: true,
      currentSessionId: "session-queue",
      currentRunId: "run-queue",
      running: true,
      activeRunSessionIds: new Set(["session-queue"]),
      draft: "Keep this queued",
      queuedMessages: [],
      pendingApprovals: [],
      selectedSkill: null,
      pendingImages: [],
      commandPickerOpen: false,
      commandPickerIndex: 0,
      loops: [],
    });
    expect(getState().loops).toEqual([]);
    await act(async () => {
      root.render(<Composer />);
    });

    const input = el.querySelector<HTMLTextAreaElement>('#message-input')!;
    const queueControl = el.querySelector<HTMLButtonElement>('[aria-label="Queue message"]')!;
    expect(queueControl.type).toBe("submit");
    await act(async () => {
      queueControl.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(requests.filter((request) => request.path === "/chat/message")).toHaveLength(0);
    expect(requests.some((request) => request.path === "/cancel")).toBe(false);
    expect(getState().draft).toBe("");
    expect(input.value).toBe("");
    expect(document.activeElement).toBe(input);
    expect(getState().queuedMessages).toHaveLength(1);
    expect(getState().queuedMessages[0]?.text).toBe("Keep this queued");
    expect(el.querySelector<HTMLButtonElement>('[aria-label="Stop response"]')).not.toBeNull();

    await act(async () => {
      setState({ draft: "Steer this run" });
    });
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent("keydown", {
        key: "Enter",
        metaKey: true,
        bubbles: true,
        cancelable: true,
      }));
      await Promise.resolve();
      await Promise.resolve();
    });

    const steerRequests = requests.filter((request) => request.path === "/chat/message");
    expect(steerRequests).toHaveLength(1);
    expect(JSON.parse(steerRequests[0]?.body ?? "{}")).toMatchObject({
      message: "Steer this run",
      session_id: "session-queue",
    });
    expect(JSON.parse(steerRequests[0]?.body ?? "{}")).not.toHaveProperty("delivery");
  } finally {
    await act(async () => root.unmount());
    restore();
    window.ardenDesktop = previousDesktop;
    setState(previous);
  }
});

test("queue is staged above the composer as a compact Sonner-like stack", () => {
  const composer = readFileSync(
    new URL("../src/features/chat/components/Composer.tsx", import.meta.url),
    "utf8",
  );
  const queueComponent = readFileSync(
    new URL("../src/features/chat/components/QueueCard.tsx", import.meta.url),
    "utf8",
  );
  const css = readFileSync(new URL("../src/design/chat.css", import.meta.url), "utf8");
  const foundation = readFileSync(new URL("../src/design/foundation.css", import.meta.url), "utf8");

  const wrapStart = composer.indexOf('<div className="board-composer-wrap');
  const queue = composer.indexOf("<QueueCard onEdit={editQueuedMessage} />", wrapStart);
  const form = composer.indexOf("<form", wrapStart);
  expect(wrapStart).toBeGreaterThanOrEqual(0);
  expect(queue).toBeGreaterThan(wrapStart);
  expect(queue).toBeLessThan(form);
  expect(composer).not.toContain("board-composer-stack__queue");
  expect(composer).toMatch(
    /const composerAction: ComposerAction = running\s*\? hasContent\s*\? "queue"\s*:\s*"stop"\s*:\s*"send";/,
  );
  expect(composer).toContain("action={composerAction}");
  expect(queueComponent).toContain("QUEUE_STACK_MAX_PEEKS");
  expect(queueComponent).toContain("stackExpanded");
  expect(queueComponent).toContain("onMouseEnter={() => setStackHovered(true)}");
  expect(queueComponent).toContain("moveQueuedMessage(pointerDownId, slot)");

  const queueRule = css.match(/\.board-queue-stack\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";
  expect(queueRule).toContain("position: absolute;");
  expect(queueRule).toContain("right: 0;");
  expect(queueRule).toContain("left: 0;");
  expect(queueRule).toContain("bottom: calc(100% + 8px);");
  expect(queueRule).toContain("z-index: var(--z-raised);");

  const itemRule = css.match(/\.board-queue-stack__item\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";
  expect(itemRule).toContain("height: 44px;");
  expect(itemRule).toContain("right: 40px;");
  expect(itemRule).toContain("left: 40px;");
  expect(itemRule).toContain("border-radius: var(--r-panel);");

  const sendRule = css.match(/\.board-composer__send\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";
  expect(sendRule).toContain("color: var(--paper);");
  expect(sendRule).toContain("background: var(--ink);");
  expect(sendRule).toContain("border-radius: var(--r-icon);");

  expect(css).toContain(".board-queue-stack__item:hover .board-queue-stack__actions");
  expect(foundation).toContain('.arden-icon-button[data-size="sm"] { width: 1.5rem; height: 1.5rem; }');
});
