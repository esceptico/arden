import { beforeEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { AssistantMessage } from "@/features/chat/components/AssistantMessage";
import { TurnGroup } from "@/features/chat/components/TurnGroup";
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

test("rendered component tests run in standards mode", () => {
  expect(document.compatMode).toBe("CSS1Compat");
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

test("browser-invalid stored URLs are non-clickable and retain Show call", () => {
  setState({
    messages: new Map([
      ["user-1", { id: "user-1", role: "user", content: "question" }],
      [
        "activity-1",
        {
          id: "activity-1",
          role: "activity",
          content: "",
          activity: {
            label: "Called",
            done: true,
            items: [{
              id: "tool-1",
              kind: "web_fetch",
              target: "Fetch",
              sourceRefs: [{
                provider: "web",
                kind: "page",
                ref: "opaque-1",
                title: "Invalid URL",
                url: "HTTPS://example.com:opaque-port/path",
              }],
            }],
          },
        },
      ],
      ["assistant-1", { id: "assistant-1", role: "assistant", content: "answer" }],
    ]),
    order: ["user-1", "activity-1", "assistant-1"],
    sourceTurnId: "user-1",
  });

  const markup = renderToStaticMarkup(<SourcesPanel />);

  expect(markup).not.toContain("<a");
  expect(markup).toContain("Show call");
});

test("hidden meta turns render through TurnGroup without rendering the hidden user row", async () => {
  setState({
    sourceRefsRevision: 1,
    messages: new Map([
      [
        "meta-user-1",
        {
          id: "meta-user-1",
          role: "user",
          content: "hidden wakeup",
          isMeta: true,
          turn: { startedAt: 1, endedAt: 2, durationMs: 1 },
        },
      ],
      [
        "activity-meta",
        {
          id: "activity-meta",
          role: "activity",
          content: "",
          activity: {
            label: "Called",
            done: true,
            items: [{
              id: "tool-meta",
              kind: "web_fetch",
              target: "Fetch",
              sourceRefs: [{ provider: "web", kind: "page", ref: "meta", title: "Meta" }],
            }],
          },
        },
      ],
      ["assistant-meta", { id: "assistant-meta", role: "assistant", content: "visible answer" }],
    ]),
    order: ["meta-user-1", "activity-meta", "assistant-meta"],
  });
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  try {
    await act(async () => {
      root.render(
        <TurnGroup
          turnId="meta-user-1"
          userId={null}
          childIds={["activity-meta", "assistant-meta"]}
        />,
      );
    });

    expect(host.textContent).not.toContain("hidden wakeup");
    expect(host.textContent).toContain("visible answer");
    expect(host.querySelector('[data-source-footer="true"]')).not.toBeNull();
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
});
