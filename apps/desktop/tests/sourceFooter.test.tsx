import { beforeEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { AssistantMessage } from "@/features/chat/components/AssistantMessage";
import { SourcesPanel } from "@/features/sources/components/SourcesPanel";
import { setState } from "@/stores";

beforeEach(() => {
  setState({
    currentSessionId: "session-1",
    messages: new Map([
      ["assistant-1", { id: "assistant-1", role: "assistant", content: "Final answer" }],
    ]),
    order: ["assistant-1"],
    running: false,
    sourceFocus: null,
    sourceTurnId: null,
  });
});

test("final assistant source footer renders between Markdown and message actions", async () => {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  try {
    await act(async () => {
      root.render(
        <AssistantMessage
          id="assistant-1"
          isFinal
          sourceTurnId="user-1"
          sourceCount={2}
        />,
      );
    });

    const children = Array.from(host.querySelector("article")?.children ?? []);
    expect(children[0]?.classList.contains("md")).toBe(true);
    expect(children[1]?.getAttribute("data-source-footer")).toBe("true");
    expect(children[1]?.getAttribute("aria-label")).toBe("Open 2 sources for this turn");
    expect(children[2]?.querySelector('button[aria-label="Copy"]')).not.toBeNull();
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
});

test("zero-source and nonfinal assistant messages omit the source footer", async () => {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  try {
    await act(async () => {
      root.render(
        <AssistantMessage id="assistant-1" isFinal sourceTurnId="user-1" sourceCount={0} />,
      );
    });
    expect(host.querySelector("[data-source-footer]")).toBeNull();

    await act(async () => {
      root.render(
        <AssistantMessage id="assistant-1" isFinal={false} sourceTurnId="user-1" sourceCount={2} />,
      );
    });
    expect(host.querySelector("[data-source-footer]")).toBeNull();
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
});

test("empty Sources panel describes turn-level provenance", () => {
  expect(renderToStaticMarkup(<SourcesPanel />)).toContain("No sources for this turn.");
});
