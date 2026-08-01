// Remote-deployment simulation: the Arden server runs in a Docker container
// with a sealed filesystem while the executor (the real Electron executor
// client + device tool implementations) runs on the host. A scripted
// OpenAI-compatible LLM drives one chat that reads a host-only file — the
// content can only reach the transcript through the device executor — and a
// second chat with the executor gone, proving client tools disappear.
//
// Run:  bun apps/desktop/scripts/remote-sim.mjs
import { execSync, spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";

const require = createRequire(import.meta.url);
const { createExecutorClient } = require("../electron/executor.cjs");
const { registerDeviceTools } = require("../electron/executor-tools.cjs");

const SERVER_PORT = Number(process.env.SIM_SERVER_PORT ?? 16877);
const LLM_PORT = Number(process.env.SIM_LLM_PORT ?? 17811);
const SERVER_URL = `http://127.0.0.1:${SERVER_PORT}`;
const CONTAINER = "arden-remote-sim";
const IMAGE = "arden-remote-sim";
const MODEL_ID = "scripted-sim";
const TRIGGER = "READ-THE-SECRET";
const SECRET = `sim-secret-${crypto.randomUUID()}`;
const SECRET_PATH = path.join(os.tmpdir(), "arden-remote-sim-secret.txt");

const repoRoot = path.resolve(import.meta.dir, "../../..");
const results = [];
let llmServer = null;
let executor = null;
let stateDir = null;

function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
}

function sh(command, options = {}) {
  return execSync(command, { cwd: repoRoot, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], ...options });
}

async function sleep(ms) {
  await new Promise(resolve => setTimeout(resolve, ms));
}

async function until(label, fn, timeoutMs = 60_000, intervalMs = 500) {
  const start = Date.now();
  for (;;) {
    const value = await fn().catch(() => null);
    if (value) return value;
    if (Date.now() - start > timeoutMs) throw new Error(`timeout waiting for ${label}`);
    await sleep(intervalMs);
  }
}

async function api(method, apiPath, body) {
  const response = await fetch(`${SERVER_URL}${apiPath}`, {
    method,
    headers: { Authorization: `Bearer ${API_KEY}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  let data = null;
  try {
    data = JSON.parse(text);
  } catch {
    /* non-JSON */
  }
  return { status: response.status, data, text };
}

// --- scripted LLM ---

function scriptedCompletion(request) {
  const messages = request.messages ?? [];
  const toolNames = (request.tools ?? []).map(t => t?.function?.name).filter(Boolean);
  const toolResult = [...messages].reverse().find(m => m.role === "tool");
  const lastUser = [...messages].reverse().find(m => m.role === "user");
  const userText = typeof lastUser?.content === "string"
    ? lastUser.content
    : (lastUser?.content ?? []).map(part => part?.text ?? "").join(" ");

  if (toolResult) {
    const content = typeof toolResult.content === "string" ? toolResult.content : JSON.stringify(toolResult.content);
    return { content: `The device file says: ${content}`, toolCall: null };
  }
  if (userText.includes(TRIGGER)) {
    if (!toolNames.includes("read_file")) {
      return { content: "TOOL-ABSENT: read_file is not available in this run.", toolCall: null };
    }
    return {
      content: null,
      toolCall: { name: "read_file", arguments: JSON.stringify({ path: SECRET_PATH }) },
    };
  }
  return { content: "ok", toolCall: null };
}

function completionChunks(scripted) {
  const base = { id: "cmpl-sim", object: "chat.completion.chunk", created: 0, model: MODEL_ID };
  const chunks = [];
  if (scripted.toolCall) {
    chunks.push({
      ...base,
      choices: [
        {
          index: 0,
          delta: {
            role: "assistant",
            tool_calls: [
              {
                index: 0,
                id: `call_${crypto.randomUUID().slice(0, 8)}`,
                type: "function",
                function: { name: scripted.toolCall.name, arguments: scripted.toolCall.arguments },
              },
            ],
          },
          finish_reason: null,
        },
      ],
    });
    chunks.push({ ...base, choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }] });
  } else {
    chunks.push({
      ...base,
      choices: [{ index: 0, delta: { role: "assistant", content: scripted.content }, finish_reason: null }],
    });
    chunks.push({ ...base, choices: [{ index: 0, delta: {}, finish_reason: "stop" }] });
  }
  return chunks;
}

function completionJson(scripted) {
  const message = scripted.toolCall
    ? {
        role: "assistant",
        content: null,
        tool_calls: [
          {
            id: `call_${crypto.randomUUID().slice(0, 8)}`,
            type: "function",
            function: { name: scripted.toolCall.name, arguments: scripted.toolCall.arguments },
          },
        ],
      }
    : { role: "assistant", content: scripted.content };
  return {
    id: "cmpl-sim",
    object: "chat.completion",
    created: 0,
    model: MODEL_ID,
    choices: [{ index: 0, message, finish_reason: scripted.toolCall ? "tool_calls" : "stop" }],
    usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
  };
}

function startFakeLlm() {
  llmServer = Bun.serve({
    hostname: "0.0.0.0",
    port: LLM_PORT,
    async fetch(request) {
      const url = new URL(request.url);
      if (!url.pathname.endsWith("/chat/completions")) {
        return new Response("not found", { status: 404 });
      }
      const body = await request.json();
      const scripted = scriptedCompletion(body);
      if (body.stream) {
        const chunks = completionChunks(scripted);
        const payload = `${chunks.map(chunk => `data: ${JSON.stringify(chunk)}`).join("\n\n")}\n\ndata: [DONE]\n\n`;
        return new Response(payload, { headers: { "Content-Type": "text/event-stream" } });
      }
      return Response.json(completionJson(scripted));
    },
  });
}

// --- executor on the host ---

function startExecutor() {
  const state = { current: null };
  executor = createExecutorClient({
    getConfig: () => ({ serverUrl: SERVER_URL, apiKey: API_KEY }),
    stateStore: {
      deviceName: "remote-sim-host",
      async load() {
        return state.current;
      },
      async save(next) {
        state.current = next;
      },
    },
    fetchImpl: (url, init) => fetch(url, init),
    log: message => console.log(`[executor] ${message}`),
    capabilities: ["filesystem", "shell"],
  });
  registerDeviceTools(executor);
  executor.start();
}

// --- orchestration ---

let API_KEY = "";

async function main() {
  console.log("== remote-deployment simulation ==");

  API_KEY = crypto.randomBytes(24).toString("base64url");
  const apiKeyHash = sh(
    `uv run --project apps/server python -c "from arden.settings import hash_api_key; print(hash_api_key('${API_KEY}'))"`,
  ).trim();

  stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "arden-remote-sim-state-"));
  fs.writeFileSync(
    path.join(stateDir, "settings.json"),
    JSON.stringify({ api_key_hash: apiKeyHash, chat_model: MODEL_ID }, null, 2),
  );
  fs.writeFileSync(
    path.join(stateDir, "models.json"),
    JSON.stringify(
      { [MODEL_ID]: { base_url: `http://host.docker.internal:${LLM_PORT}/v1`, context_window: 200_000 } },
      null,
      2,
    ),
  );
  fs.chmodSync(stateDir, 0o777);
  fs.writeFileSync(SECRET_PATH, `${SECRET}\n`);

  console.log("building server image (first build takes a few minutes)…");
  sh(`docker build -q -f apps/server/Dockerfile -t ${IMAGE} .`, { stdio: ["ignore", "pipe", "inherit"] });

  spawnSync("docker", ["rm", "-f", CONTAINER], { stdio: "ignore" });
  sh(
    `docker run -d --rm --name ${CONTAINER} -p 127.0.0.1:${SERVER_PORT}:6877 ` +
      `-v ${stateDir}:/home/arden/.arden ${IMAGE}`,
  );

  await until("server /health", async () => {
    const response = await fetch(`${SERVER_URL}/health`);
    return response.ok;
  }, 90_000);
  console.log("server is up");

  const sealed = spawnSync("docker", ["exec", CONTAINER, "cat", SECRET_PATH]);
  check("container filesystem is sealed (cannot read the host secret)", sealed.status !== 0);

  startFakeLlm();
  startExecutor();
  await until("executor connected", async () => executor.getStatus().connected, 30_000);
  const devices = await api("GET", "/executor/devices");
  check(
    "executor enrolled and connected",
    devices.data?.devices?.some(d => d.connected),
    JSON.stringify(devices.data?.devices?.map(d => ({ name: d.name, connected: d.connected }))),
  );

  // Positive path: the chat can only learn the secret through the device.
  const session = await api("POST", "/sessions", { name: "remote-sim" });
  const sessionId = session.data.session_id;
  const submitted = await api("POST", "/chat/message", {
    session_id: sessionId,
    message: `${TRIGGER} — read the file and tell me what it says.`,
  });
  check("chat message accepted", submitted.status === 200, `status ${submitted.status}`);

  const transcript = await until(
    "secret to arrive in the transcript",
    async () => {
      const history = await api("GET", `/session/history?session_id=${sessionId}`);
      const raw = JSON.stringify(history.data);
      return raw.includes(SECRET) ? raw : null;
    },
    90_000,
    1_000,
  );
  check("host-only file content reached the chat through the executor", transcript.includes(SECRET));

  const invocations = sh(
    `docker exec ${CONTAINER} python -c "import sqlite3; c = sqlite3.connect('/home/arden/.arden/sessions.db'); print(list(c.execute('select tool_name, placement, status from tool_invocations')))"`,
  ).trim();
  check(
    "durable invocation recorded as client-placed and succeeded",
    invocations.includes("read_file") && invocations.includes("client") && invocations.includes("succeeded"),
    invocations,
  );

  // Negative path: no executor → client tools disappear from the run.
  executor.dispose();
  await until("executor disconnect observed by server", async () => {
    const state = await api("GET", "/executor/devices");
    return state.data?.devices?.every(d => !d.connected);
  }, 60_000);

  const offlineSession = await api("POST", "/sessions", { name: "remote-sim-offline" });
  await api("POST", "/chat/message", {
    session_id: offlineSession.data.session_id,
    message: `${TRIGGER} — read the file and tell me what it says.`,
  });
  const offlineTranscript = await until(
    "offline run to answer",
    async () => {
      const history = await api("GET", `/session/history?session_id=${offlineSession.data.session_id}`);
      const raw = JSON.stringify(history.data);
      return raw.includes("TOOL-ABSENT") || raw.includes(SECRET) ? raw : null;
    },
    90_000,
    1_000,
  );
  check(
    "with the executor gone, read_file is not advertised (and the secret cannot leak)",
    offlineTranscript.includes("TOOL-ABSENT") && !offlineTranscript.includes(SECRET),
  );
}

async function cleanup() {
  try {
    executor?.dispose();
  } catch {
    /* already down */
  }
  llmServer?.stop(true);
  spawnSync("docker", ["rm", "-f", CONTAINER], { stdio: "ignore" });
  fs.rmSync(SECRET_PATH, { force: true });
  if (stateDir) fs.rmSync(stateDir, { recursive: true, force: true });
}

try {
  await main();
} catch (error) {
  check("simulation completed", false, String(error?.message ?? error));
} finally {
  await cleanup();
}

const failed = results.filter(result => !result.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
