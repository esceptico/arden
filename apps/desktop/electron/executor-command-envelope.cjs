// Exact server-to-device executor command contract. Keep this independent of
// individual device tools: handlers receive only a validated command.
const COMMAND_VERSION = 1;
const MAX_COMMAND_BYTES = 2 * 1024 * 1024;
const MAX_ARGUMENT_BYTES = 1 * 1024 * 1024;
const MAX_JSON_DEPTH = 16;
const MAX_JSON_ITEMS = 50_000;
const MAX_CONTEXT_OBSERVATIONS = 10_000;
const MAX_TEXT_CHARS = 4_096;

class ExecutorCommandEnvelopeError extends Error {}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function requireExactKeys(value, keys, label) {
  if (!isRecord(value)) throw new ExecutorCommandEnvelopeError(`${label} must be an object`);
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new ExecutorCommandEnvelopeError(`${label} has an invalid shape`);
  }
}

function requireExactText(value, label, maxLength = MAX_TEXT_CHARS) {
  if (typeof value !== "string" || !value || value.trim() !== value || value.length > maxLength || /[\r\n]/.test(value)) {
    throw new ExecutorCommandEnvelopeError(`${label} must be exact single-line text`);
  }
  return value;
}

function requirePositiveInteger(value, label, maximum = Number.MAX_SAFE_INTEGER) {
  if (!Number.isInteger(value) || value < 1 || value > maximum) {
    throw new ExecutorCommandEnvelopeError(`${label} must be a positive integer`);
  }
  return value;
}

function encodedBytes(value) {
  return Buffer.byteLength(JSON.stringify(value), "utf8");
}

function validateJson(value, label) {
  const stack = [[value, 1]];
  let items = 0;
  while (stack.length) {
    const [item, depth] = stack.pop();
    if (depth > MAX_JSON_DEPTH) throw new ExecutorCommandEnvelopeError(`${label} exceed the nesting limit`);
    if (item === null || typeof item === "string" || typeof item === "boolean") continue;
    if (typeof item === "number") {
      if (!Number.isFinite(item)) throw new ExecutorCommandEnvelopeError(`${label} contain a non-finite number`);
      continue;
    }
    if (Array.isArray(item)) {
      items += item.length;
      stack.push(...item.map(child => [child, depth + 1]));
    } else if (isRecord(item)) {
      items += Object.keys(item).length;
      stack.push(...Object.values(item).map(child => [child, depth + 1]));
    } else {
      throw new ExecutorCommandEnvelopeError(`${label} must contain JSON values only`);
    }
    if (items > MAX_JSON_ITEMS) throw new ExecutorCommandEnvelopeError(`${label} exceed the item limit`);
  }
}

function parseContext(value) {
  requireExactKeys(
    value,
    ["default_cwd", "offload_dir", "offload_threshold", "resource_observations", "file_discovery"],
    "executor context",
  );
  if (value.default_cwd !== null) requireExactText(value.default_cwd, "executor context default_cwd");
  requireExactText(value.offload_dir, "executor context offload_dir");
  requirePositiveInteger(value.offload_threshold, "executor context offload_threshold", 16 * 1024 * 1024);

  if (!isRecord(value.resource_observations) || Object.keys(value.resource_observations).length > MAX_CONTEXT_OBSERVATIONS) {
    throw new ExecutorCommandEnvelopeError("executor context resource_observations is invalid");
  }
  for (const [id, observation] of Object.entries(value.resource_observations)) {
    requireExactText(id, "resource observation id");
    requireExactKeys(observation, ["version", "content_read"], "resource observation");
    if (observation.version !== null) requireExactText(observation.version, "resource observation version", 1024);
    if (typeof observation.content_read !== "boolean") {
      throw new ExecutorCommandEnvelopeError("resource observation content_read must be boolean");
    }
  }

  requireExactKeys(value.file_discovery, ["observed_paths", "miss_counts"], "file discovery");
  const { observed_paths: observedPaths, miss_counts: missCounts } = value.file_discovery;
  if (!isRecord(observedPaths) || !isRecord(missCounts)) {
    throw new ExecutorCommandEnvelopeError("file discovery state is invalid");
  }
  if (Object.keys(observedPaths).length > MAX_CONTEXT_OBSERVATIONS || Object.keys(missCounts).length > MAX_CONTEXT_OBSERVATIONS) {
    throw new ExecutorCommandEnvelopeError("file discovery state exceeds the entry limit");
  }
  for (const [path, kind] of Object.entries(observedPaths)) {
    requireExactText(path, "file discovery path");
    if (kind !== "file" && kind !== "directory" && kind !== "other") {
      throw new ExecutorCommandEnvelopeError("file discovery kind is invalid");
    }
  }
  for (const [path, count] of Object.entries(missCounts)) {
    requireExactText(path, "file discovery path");
    requirePositiveInteger(count, "file discovery miss count");
  }
  return value;
}

function parseExecuteCommand(payload, eventInvocationId) {
  requireExactKeys(
    payload,
    ["version", "type", "invocation_id", "tool_call_id", "tool_name", "arguments", "context", "run_id", "session_id", "deadline_at"],
    "execute command payload",
  );
  if (payload.version !== COMMAND_VERSION || payload.type !== "execute_tool") {
    throw new ExecutorCommandEnvelopeError("unsupported execute command version or type");
  }
  const invocationId = requireExactText(payload.invocation_id, "invocation id", 512);
  if (invocationId !== eventInvocationId) throw new ExecutorCommandEnvelopeError("command invocation id does not match its event");
  requireExactText(payload.tool_call_id, "tool call id", 512);
  requireExactText(payload.tool_name, "tool name", 512);
  requireExactText(payload.run_id, "run id", 512);
  requireExactText(payload.session_id, "session id", 512);
  if (payload.deadline_at !== null) {
    if (typeof payload.deadline_at !== "string" || Number.isNaN(Date.parse(payload.deadline_at))) {
      throw new ExecutorCommandEnvelopeError("command deadline is invalid");
    }
  }
  if (!isRecord(payload.arguments)) throw new ExecutorCommandEnvelopeError("tool arguments must be an object");
  for (const key of Object.keys(payload.arguments)) requireExactText(key, "tool argument key", 512);
  validateJson(payload.arguments, "tool arguments");
  if (encodedBytes(payload.arguments) > MAX_ARGUMENT_BYTES) {
    throw new ExecutorCommandEnvelopeError("tool arguments exceed the byte limit");
  }
  parseContext(payload.context);
  if (encodedBytes(payload) > MAX_COMMAND_BYTES) throw new ExecutorCommandEnvelopeError("executor command exceeds the byte limit");
  return payload;
}

function parseCancelCommand(payload, eventInvocationId) {
  requireExactKeys(payload, ["version", "type", "invocation_id"], "cancel command payload");
  if (payload.version !== COMMAND_VERSION || payload.type !== "cancel_tool") {
    throw new ExecutorCommandEnvelopeError("unsupported cancel command version or type");
  }
  const invocationId = requireExactText(payload.invocation_id, "invocation id", 512);
  if (invocationId !== eventInvocationId) throw new ExecutorCommandEnvelopeError("command invocation id does not match its event");
  return payload;
}

function parseExecutorCommandEvent(event) {
  requireExactKeys(event, ["kind", "seq", "type", "invocation_id", "payload"], "executor command event");
  if (event.kind !== "command") throw new ExecutorCommandEnvelopeError("invalid executor command event kind");
  if (!Number.isInteger(event.seq) || event.seq < 1) throw new ExecutorCommandEnvelopeError("executor command sequence is invalid");
  const invocationId = requireExactText(event.invocation_id, "event invocation id", 512);
  if (event.type === "execute_tool") return { ...event, payload: parseExecuteCommand(event.payload, invocationId) };
  if (event.type === "cancel_tool") return { ...event, payload: parseCancelCommand(event.payload, invocationId) };
  throw new ExecutorCommandEnvelopeError("unsupported executor command type");
}

module.exports = { ExecutorCommandEnvelopeError, parseExecutorCommandEvent };
