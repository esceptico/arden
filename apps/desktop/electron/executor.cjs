// Client executor: the desktop side of the server's executor protocol.
//
// Owns one outbound SSE control stream (lease + command events with cursor
// replay), a heartbeat loop that renews the lease and acks the command
// cursor, and reconnect with exponential backoff. Tool execution itself is
// registered by the caller via handlers; a command with no handler is
// answered immediately with a failed result so the server never waits on a
// capability this executor does not have.
const sseFrameParserModule = import("./sse-frame-parser.js");
const { scanDeviceSkills, snapshotFingerprint } = require("./executor-skills.cjs");
const { parseExecutorCommandEvent } = require("./executor-command-envelope.cjs");

const HEARTBEAT_INTERVAL_MS = 20_000;
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;

class StaleLease extends Error {}

function createExecutorClient({
  getConfig,
  stateStore,
  fetchImpl,
  log = () => {},
  heartbeatIntervalMs = HEARTBEAT_INTERVAL_MS,
  capabilities = [],
  skillsDirs = [],
}) {
  let disposed = false;
  let abortController = null;
  let heartbeatTimer = null;
  let reconnectTimer = null;
  let reconnectDelay = RECONNECT_BASE_MS;
  let leaseId = null;
  let cursor = 0;
  let executorId = null;
  let token = null;
  const handlers = new Map();
  const running = new Map();
  let advertisedSkillsFingerprint = null;
  const status = { state: "idle", executorId: null, connected: false, lastError: null };
  const statusListeners = new Set();

  function setStatus(patch) {
    Object.assign(status, patch);
    for (const listener of statusListeners) listener({ ...status });
  }

  function onStatusChange(listener) {
    statusListeners.add(listener);
    return () => statusListeners.delete(listener);
  }

  function registerHandler(toolName, handler) {
    handlers.set(toolName, handler);
  }

  function headers(config, withJson) {
    const result = { Authorization: `Bearer ${token}` };
    if (withJson) result["Content-Type"] = "application/json";
    return result;
  }

  async function post(config, path, body) {
    const response = await fetchImpl(new URL(path, config.serverUrl), {
      method: "POST",
      headers: headers(config, true),
      body: JSON.stringify(body),
    });
    return response;
  }

  async function ensureEnrolled(config) {
    const state = await stateStore.load();
    if (state?.executorId && state?.token) {
      executorId = state.executorId;
      token = state.token;
      cursor = Number.isFinite(state.cursor) ? state.cursor : 0;
      return true;
    }
    if (!config.apiKey) return false;
    const response = await fetchImpl(new URL("/executor/enroll", config.serverUrl), {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${config.apiKey}` },
      body: JSON.stringify({ name: stateStore.deviceName, capabilities }),
    });
    if (!response.ok) throw new Error(`enroll failed: ${response.status}`);
    const body = await response.json();
    executorId = body.executor_id;
    token = body.token;
    cursor = 0;
    await stateStore.save({ executorId, token, cursor });
    log(`enrolled as ${executorId}`);
    return true;
  }

  async function persistCursor() {
    await stateStore.save({ executorId, token, cursor });
  }

  const RESULT_RETRY_DELAYS_MS = [1_000, 2_000, 4_000, 8_000, 16_000];

  async function submitResult(config, invocationId, statusValue, payload, errorCode) {
    // The tool already ran: losing the result would leave the server-side
    // waiter hanging on work that finished. Results are idempotent by
    // invocation_id, so retry network failures; only a stale lease (403,
    // we were fenced) or a recorded conflict (409) ends the attempt.
    for (let attempt = 0; ; attempt += 1) {
      try {
        const response = await post(config, "/executor/results", {
          lease_id: leaseId,
          invocation_id: invocationId,
          status: statusValue,
          result: payload,
          error_code: errorCode ?? null,
        });
        if (response.status === 403) throw new StaleLease();
        if (response.status < 500) return response;
        throw new Error(`results POST failed: ${response.status}`);
      } catch (error) {
        if (error instanceof StaleLease || disposed || attempt >= RESULT_RETRY_DELAYS_MS.length) throw error;
        log(`result delivery failed (attempt ${attempt + 1}) — retrying: ${error?.message ?? error}`);
        await new Promise(resolve => setTimeout(resolve, RESULT_RETRY_DELAYS_MS[attempt]));
      }
    }
  }

  async function handleExecute(config, command) {
    const { invocation_id: invocationId, tool_name: toolName, arguments: args, context } = command.payload;
    const handler = handlers.get(toolName);
    if (!handler) {
      await submitResult(
        config,
        invocationId,
        "failed",
        { content: `This executor has no implementation for tool ${toolName}.`, preview: "Unsupported tool" },
        "unsupported_tool",
      );
      return;
    }
    await post(config, "/executor/started", { lease_id: leaseId, invocation_id: invocationId });
    const controller = new AbortController();
    running.set(invocationId, controller);
    try {
      const result = await handler(args, { signal: controller.signal, context });
      await submitResult(config, invocationId, result.status ?? "succeeded", result.payload, result.errorCode);
    } catch (error) {
      const cancelled = controller.signal.aborted;
      await submitResult(
        config,
        invocationId,
        cancelled ? "cancelled" : "failed",
        { content: String(error?.message ?? error), preview: cancelled ? "Cancelled" : "Tool failed" },
        cancelled ? "cancelled" : "tool_error",
      );
    } finally {
      running.delete(invocationId);
    }
  }

  async function handleCommand(config, event) {
    const command = parseExecutorCommandEvent(event);
    if (command.type === "execute_tool") {
      // Fire-and-forget so the stream loop keeps consuming — cancel_tool
      // must be able to arrive while a tool is still running. The durable
      // invocation server-side owns the outcome if we crash mid-run.
      void handleExecute(config, command).catch(error => {
        log(`tool execution failed to report: ${error?.message ?? error}`);
      });
    } else if (command.type === "cancel_tool") {
      running.get(command.payload.invocation_id)?.abort();
    }
    cursor = command.seq;
    await persistCursor();
  }

  async function advertiseSkills(config) {
    if (!skillsDirs.length) return;
    const skills = await scanDeviceSkills(skillsDirs);
    const fingerprint = snapshotFingerprint(skills);
    if (fingerprint === advertisedSkillsFingerprint) return;
    const response = await post(config, "/executor/skills", {
      lease_id: leaseId,
      skills: skills.map(({ name, description, sha256 }) => ({ name, description, sha256 })),
    });
    if (!response.ok) {
      log(`skill advertisement failed: ${response.status}`);
      return;
    }
    advertisedSkillsFingerprint = fingerprint;
    const { need = [] } = await response.json();
    if (!need.length) return;
    const bodies = skills
      .filter(skill => need.includes(skill.name))
      .map(({ name, sha256, content }) => ({ name, sha256, body: content }));
    await post(config, "/executor/skills/bodies", { lease_id: leaseId, skills: bodies });
    log(`synced ${bodies.length} device skill(s)`);
  }

  function startHeartbeat(config) {
    stopHeartbeat();
    heartbeatTimer = setInterval(async () => {
      try {
        const response = await post(config, "/executor/heartbeat", { lease_id: leaseId, acked_seq: cursor });
        if (response.ok) {
          // Cheap rescan each beat: a changed fingerprint re-advertises, so
          // edits to local skill files reach the server within one interval.
          await advertiseSkills(config).catch(() => {});
        }
        if (response.status === 401) {
          // Device was revoked mid-session: drop the credential so the
          // reconnect loop re-enrolls instead of retrying a dead token.
          log("heartbeat rejected: device revoked — re-enrolling");
          await stateStore.save(null);
          executorId = null;
          token = null;
          restart();
        } else if (response.status === 403) {
          log("heartbeat rejected: stale lease — reconnecting");
          restart();
        }
      } catch {
        /* stream error handling owns reconnects */
      }
    }, heartbeatIntervalMs);
  }

  function stopHeartbeat() {
    if (heartbeatTimer) clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }

  async function consumeStream(config, response) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const { createSseFrameParser } = await sseFrameParserModule;
    const parser = createSseFrameParser();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const data of parser.push(decoder.decode(value, { stream: true }))) {
        if (data.kind === "lease") {
          leaseId = data.lease_id;
          reconnectDelay = RECONNECT_BASE_MS;
          setStatus({ state: "connected", executorId, connected: true, lastError: null });
          startHeartbeat(config);
          // New lease — re-advertise even if the fingerprint is unchanged,
          // so a fresh server (or wiped cache) always gets the snapshot.
          advertisedSkillsFingerprint = null;
          void advertiseSkills(config).catch(error => {
            log(`skill advertisement failed: ${error?.message ?? error}`);
          });
        } else if (data.kind === "command") {
          await handleCommand(config, data);
        }
      }
    }
  }

  async function runOnce() {
    const config = getConfig();
    if (!config.serverUrl) throw new Error("no server configured");
    if (!(await ensureEnrolled(config))) throw new Error("no API key configured; cannot enroll");

    abortController = new AbortController();
    const response = await fetchImpl(new URL(`/executor/stream?cursor=${cursor}`, config.serverUrl), {
      headers: headers(config, false),
      signal: abortController.signal,
    });
    if (response.status === 401) {
      // Enrollment was revoked server-side; drop the credential and re-enroll.
      await stateStore.save(null);
      executorId = null;
      token = null;
      throw new Error("executor token rejected — re-enrolling");
    }
    if (!response.ok || !response.body) throw new Error(`stream failed: ${response.status}`);
    await consumeStream(config, response);
  }

  async function loop() {
    while (!disposed) {
      try {
        setStatus({ state: "connecting", connected: false });
        await runOnce();
        if (!disposed) log("stream ended — reconnecting");
      } catch (error) {
        if (disposed) return;
        setStatus({ state: "disconnected", connected: false, lastError: String(error?.message ?? error) });
        log(`executor error: ${error?.message ?? error}`);
      }
      stopHeartbeat();
      leaseId = null;
      if (disposed) return;
      await new Promise(resolve => {
        reconnectTimer = setTimeout(resolve, reconnectDelay);
      });
      reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
    }
  }

  function restart() {
    abortController?.abort();
  }

  function start() {
    void loop();
  }

  function dispose() {
    disposed = true;
    stopHeartbeat();
    if (reconnectTimer) clearTimeout(reconnectTimer);
    abortController?.abort();
    for (const controller of running.values()) controller.abort();
  }

  function getStatus() {
    return { ...status };
  }

  return { start, restart, dispose, getStatus, onStatusChange, registerHandler };
}

module.exports = { createExecutorClient };
