import { beforeEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { AssistantMessage } from "@/features/chat/components/AssistantMessage";
import { TurnGroup } from "@/features/chat/components/TurnGroup";
import { SourcesPanel } from "@/features/sources/components/SourcesPanel";
import { getState, setState, type SourceRef } from "@/stores";

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

test("proof summary is placed between final answer content and message actions", async () => {
  setState({
    sourceRefsRevision: 1,
    messages: new Map([
      ["user-1", { id: "user-1", role: "user", content: "write" }],
      ["activity-1", {
        id: "activity-1",
        role: "activity",
        content: "",
        activity: {
          label: "Called",
          done: true,
          items: [{
            id: "call-1",
            kind: "write_file",
            target: "a.txt",
            outcome: { status: "succeeded", effect: { operation: "write", target: "a.txt" } },
          }],
        },
      }],
      ["assistant-1", { id: "assistant-1", role: "assistant", content: "Done" }],
    ]),
    order: ["user-1", "activity-1", "assistant-1"],
  });
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(
      <AssistantMessage id="assistant-1" isFinal sourceTurnId="user-1" sourceCount={0} />,
    ));
    const children = Array.from(host.querySelector("article")?.children ?? []);
    expect(children[0]?.classList.contains("md")).toBe(true);
    expect(children[1]?.getAttribute("data-proof-summary")).toBe("true");
    expect(children[2]?.querySelector('button[aria-label="Copy"]')).not.toBeNull();
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
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

test("groups unknown MCP calls and progressively reveals returned sources", async () => {
  const teamSources: SourceRef[] = Array.from({ length: 7 }, (_, index) => ({
    provider: "mcp.crm",
    kind: "contact",
    ref: `team-${index + 1}`,
    title: `Team ${index + 1}`,
    toolCallId: "tool-team",
  }));
  setState({
    sourceRefsRevision: 1,
    messages: new Map([
      ["user-1", { id: "user-1", role: "user", content: "Find contacts" }],
      [
        "activity-1",
        {
          id: "activity-1",
          role: "activity",
          content: "",
          activity: {
            label: "Called",
            done: true,
            items: [
              {
                id: "tool-alice",
                kind: "lookup_contact",
                displayName: "Lookup contact",
                target: "query=alice",
                sourceRefs: [{
                  provider: "mcp.crm",
                  kind: "contact",
                  ref: "alice",
                  title: "Alice",
                  toolCallId: "tool-alice",
                }],
              },
              {
                id: "tool-team",
                kind: "lookup_contact",
                displayName: "Lookup contact",
                target: "query=team",
                sourceRefs: teamSources,
              },
            ],
          },
        },
      ],
      ["assistant-1", { id: "assistant-1", role: "assistant", content: "Found them" }],
    ]),
    order: ["user-1", "activity-1", "assistant-1"],
    sourceTurnId: "user-1",
    viewingTool: null,
  });

  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(<SourcesPanel />));

    expect(host.textContent).toContain("mcp.crm");
    expect(host.textContent).toContain("Lookup contact");
    expect(host.textContent).toContain("2 calls");
    expect(host.textContent).toContain("8 sources");
    expect(host.textContent).toContain("Alice");
    expect(host.textContent).not.toContain("Team 1");

    const expandTeam = host.querySelector(
      'button[aria-label="Expand source call query=team"]',
    ) as HTMLButtonElement;
    expect(expandTeam?.getAttribute("aria-expanded")).toBe("false");
    await act(async () => expandTeam.click());

    expect(host.textContent).toContain("Team 5");
    expect(host.textContent).not.toContain("Team 6");
    const showMore = Array.from(host.querySelectorAll("button")).find(
      (button) => button.textContent?.trim() === "Show 2 more",
    );
    expect(showMore).toBeDefined();
    await act(async () => showMore?.click());
    expect(host.textContent).toContain("Team 7");

    const showAliceCall = host.querySelector(
      'button[aria-label="Show tool call for Alice"]',
    ) as HTMLButtonElement;
    await act(async () => showAliceCall.click());
    expect(getState().viewingTool?.id).toBe("tool-alice");
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
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
