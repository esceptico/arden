import { afterEach, beforeEach, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { sendMessage } from "@/actions/messages";
import { archiveArea, archiveSession, goToNewSessionHome } from "@/actions/sessions";
import { getState, setState } from "@/stores";

const requests: Array<{ path: string; method: string; body?: string }> = [];
const CONTEXT_BUDGET = {
  model: "openai-codex/gpt-5.6-sol",
  hard_limit: 372_000,
  compaction_trigger: 282_720,
  message_limit: 120,
  input_tokens: 0,
  message_count: 0,
};
let failCreation = false;
let delayedAreaId: string | null | undefined;
let releaseDelayedCreation: (() => void) | null = null;
const originalDesktop = window.ardenDesktop;

function response(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", contentType: "application/json", data, text: "" };
}

beforeEach(() => {
  requests.length = 0;
  failCreation = false;
  delayedAreaId = undefined;
  releaseDelayedCreation = null;
  setState({
    currentSessionId: null,
    pendingNewChatAreaId: null,
    pendingNewChatDraftId: 0,
    areas: { ...getState().areas, openAreaKey: null },
    memoryOpen: false,
    automationsOpen: false,
    settingsOpen: false,
    sessions: [],
    error: null,
    skipApprovals: false,
  });
  window.ardenDesktop = {
    api: {
      request: async (_config, request) => {
        requests.push({
          path: request.path,
          method: request.method ?? "GET",
          body: request.body,
        });
        if (request.path === "/sessions" && request.method === "POST") {
          if (failCreation) throw new Error("creation failed");
          const areaId = JSON.parse(request.body ?? "{}").area_id as string | null;
          if (delayedAreaId !== undefined && areaId === delayedAreaId) {
            await new Promise<void>((resolve) => {
              releaseDelayedCreation = resolve;
            });
          }
          return response({
            session_id: `created-${areaId ?? "inbox"}`,
            started_at: "",
            last_activity: "",
            name: null,
            message_count: 0,
          });
        }
        if (request.path.startsWith("/session/history")) {
          return response({ messages: [], active_run_id: null, context_budget: CONTEXT_BUDGET });
        }
        if (request.path === "/chat/message") return response({ run_id: "run-1" });
        return response(null);
      },
    },
  } as Window["ardenDesktop"];
});

async function waitForDelayedCreation(): Promise<void> {
  for (let attempt = 0; attempt < 20 && !releaseDelayedCreation; attempt += 1) {
    await Promise.resolve();
  }
  if (!releaseDelayedCreation) throw new Error("creation did not reach delay gate");
}

afterEach(() => {
  window.ardenDesktop = originalDesktop;
});

test("Area New Chat creates nothing until first non-empty send", async () => {
  goToNewSessionHome("area-1");

  expect(requests).toEqual([]);
  expect(await sendMessage("   ")).toBe(false);
  expect(requests).toEqual([]);

  expect(await sendMessage("hello")).toBe(true);

  const creation = requests.find((request) => request.path === "/sessions");
  expect(JSON.parse(creation?.body ?? "{}")).toEqual({ area_id: "area-1" });
  expect(requests.some((request) => request.path === "/chat/message")).toBe(true);
  expect(getState().pendingNewChatAreaId).toBeNull();
});

test("Inbox New Chat sends an explicit null Area only after send", async () => {
  goToNewSessionHome();

  expect(requests).toEqual([]);
  expect(await sendMessage("hello")).toBe(true);

  const creation = requests.find((request) => request.path === "/sessions");
  expect(JSON.parse(creation?.body ?? "{}")).toEqual({ area_id: null });
});

test("failed creation keeps the Area intent for retry", async () => {
  failCreation = true;
  goToNewSessionHome("area-1");

  expect(await sendMessage("hello")).toBe(false);

  expect(getState().currentSessionId).toBeNull();
  expect(getState().pendingNewChatAreaId).toBe("area-1");
  expect(getState().error).toBe("creation failed");
});

test("concurrent first sends share one session creation", async () => {
  goToNewSessionHome("area-1");

  await Promise.all([sendMessage("one"), sendMessage("two")]);

  expect(
    requests.filter((request) => request.path === "/sessions" && request.method === "POST"),
  ).toHaveLength(1);
});

test("a newer Area draft does not reuse or get overwritten by an older creation", async () => {
  delayedAreaId = "area-a";
  goToNewSessionHome("area-a");
  const sendA = sendMessage("message A");
  await waitForDelayedCreation();

  goToNewSessionHome("area-b");
  const sendB = sendMessage("message B");
  await sendB;
  releaseDelayedCreation?.();
  await sendA;

  const sent = requests
    .filter((request) => request.path === "/chat/message")
    .map((request) => JSON.parse(request.body ?? "{}"));
  expect(sent).toContainEqual({
    message: "message A",
    session_id: "created-area-a",
    skip_approvals: false,
    client_id: expect.any(String),
  });
  expect(sent).toContainEqual({
    message: "message B",
    session_id: "created-area-b",
    skip_approvals: false,
    client_id: expect.any(String),
  });
  expect(getState().currentSessionId).toBe("created-area-b");
});

test("an in-flight draft cannot navigate over a selected existing session", async () => {
  delayedAreaId = "area-a";
  goToNewSessionHome("area-a");
  const sending = sendMessage("message A");
  await waitForDelayedCreation();

  getState().setCurrentSession("existing-session");
  releaseDelayedCreation?.();
  expect(await sending).toBe(true);

  expect(getState().currentSessionId).toBe("existing-session");
  const sent = requests.find((request) => request.path === "/chat/message");
  expect(JSON.parse(sent?.body ?? "{}").session_id).toBe("created-area-a");
});

test.each([
  ["Area", () => getState().openArea("area-b")],
  ["Memory", () => getState().openMemory("page.md")],
  ["Automations", () => getState().openAutomations(null)],
  ["Settings", () => getState().openSettings()],
] as const)("an in-flight draft cannot navigate over %s", async (_surface, navigate) => {
  delayedAreaId = "area-a";
  goToNewSessionHome("area-a");
  const sending = sendMessage("message A");
  await waitForDelayedCreation();

  navigate();
  releaseDelayedCreation?.();
  expect(await sending).toBe(true);

  expect(getState().currentSessionId).toBeNull();
  const sent = requests.find((request) => request.path === "/chat/message");
  expect(JSON.parse(sent?.body ?? "{}").session_id).toBe("created-area-a");
});

test("first send marks the new empty history ready without fetching it", async () => {
  goToNewSessionHome("area-1");

  expect(await sendMessage("hello")).toBe(true);

  expect(requests.some((request) => request.path.startsWith("/session/history"))).toBe(false);
  expect(requests.some((request) => request.path === "/chat/message")).toBe(true);
  expect(getState().sessionView.historyLoadedFor).toBe("created-area-1");
});

test("archiving the pending Area moves the draft to Inbox", async () => {
  goToNewSessionHome("area-1");
  const draftId = getState().pendingNewChatDraftId;

  await archiveArea("area-1");

  expect(getState().pendingNewChatAreaId).toBeNull();
  expect(getState().pendingNewChatDraftId).toBe(draftId + 1);
});

test("archiving the final chat opens an unpersisted draft", async () => {
  setState({
    currentSessionId: "session-1",
    sessions: [
      {
        session_id: "session-1",
        started_at: "",
        last_activity: "",
        name: "Only chat",
        message_count: 1,
      },
    ],
  });

  await archiveSession("session-1");

  expect(requests.some((request) => request.path === "/sessions" && request.method === "POST")).toBe(false);
  expect(getState().currentSessionId).toBeNull();
  expect(getState().pendingNewChatAreaId).toBeNull();
});

test("every explicit New Chat entry point routes to the lazy draft", () => {
  const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
  const entries = readFileSync(new URL("../src/hooks/useEntries.ts", import.meta.url), "utf8");
  const sessionList = readFileSync(
    new URL("../src/features/sessions/components/SessionList.tsx", import.meta.url),
    "utf8",
  );

  expect(app).toMatch(/k === "n"[\s\S]*?goToNewSessionHome\(\)/);
  expect(entries).toContain("run: () => goToNewSessionHome()");
  expect(sessionList).toContain("goToNewSessionHome(group.area?.area_id ?? null)");
  expect(sessionList).toContain("goToNewSessionHome(areaMenu.areaId)");
  expect(sessionList).not.toContain("createSession");
});
