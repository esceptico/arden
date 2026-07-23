import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { CommandPeek } from "@/features/command-sidecar/CommandPeek";
import { createCommandSidecarState } from "@/stores/command-sidecar-domain";
import { setState } from "@/stores";

let root: Root | null = null;

afterEach(async () => {
  delete (window as unknown as { ardenDesktop?: unknown }).ardenDesktop;
  await act(async () => {
    setState({ commandSidecar: createCommandSidecarState() });
    root?.unmount();
  });
  root = null;
  document.body.replaceChildren();
});

test("shows command activity and submits canonical approval results", async () => {
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  (window as unknown as { ardenDesktop: unknown }).ardenDesktop = {
    api: {
      request: async (_config: unknown, request: { path: string; body?: string }) => {
        calls.push({ path: request.path, body: request.body ? JSON.parse(request.body) : {} });
        return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data: {}, text: "" };
      },
    },
    events: {
      connect: async () => "command-stream",
      disconnect: async () => {},
      onData: () => () => {},
    },
  };
  setState({
    commandSidecar: {
      ...createCommandSidecarState(),
      open: true,
      clientId: "command:1",
      query: "pause email digest",
      runId: "run-1",
      sessionId: "command-1",
      status: "running",
      activities: [{ id: "t1", name: "Update automation", status: "running" }],
      approval: {
        toolId: "t1",
        name: "update_automation",
        preview: "Pause Email digest",
        diff: null,
      },
    },
  });
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);

  await act(async () => root?.render(<CommandPeek />));

  expect(host.textContent).toContain("pause email digest");
  expect(host.textContent).toContain("Update automation");
  const approve = [...host.querySelectorAll("button")].find((button) => button.textContent === "Approve");
  await act(async () => approve?.click());
  expect(calls).toEqual([
    {
      path: "/tools/result",
      body: { run_id: "run-1", tool_id: "t1", result: "", approved: true },
    },
  ]);
});

test("Close is passive while Stop cancels the run", async () => {
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  (window as unknown as { ardenDesktop: unknown }).ardenDesktop = {
    api: {
      request: async (_config: unknown, request: { path: string; body?: string }) => {
        calls.push({ path: request.path, body: request.body ? JSON.parse(request.body) : {} });
        return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data: {}, text: "" };
      },
    },
    events: {
      connect: async () => "command-stream",
      disconnect: async () => {},
      onData: () => () => {},
    },
  };
  setState({
    commandSidecar: {
      ...createCommandSidecarState(),
      open: true,
      clientId: "command:1",
      query: "run automation",
      runId: "run-1",
      sessionId: "command-1",
      status: "running",
    },
  });
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root?.render(<CommandPeek />));

  const stop = [...host.querySelectorAll("button")].find((button) => button.textContent === "Stop");
  await act(async () => stop?.click());

  expect(calls[0]).toEqual({ path: "/cancel", body: { run_id: "run-1" } });
});
