// Device-side tool implementations for the client executor. Each handler
// receives the invocation arguments plus { signal } and returns
// { status, payload, errorCode? } — the bounded ToolResult projection the
// server rebuilds on its side.
const { spawn } = require("node:child_process");
const fs = require("node:fs/promises");
const path = require("node:path");

const MAX_CONTENT_CHARS = 262_144;
const BASH_TIMEOUT_MS = 120_000;
const BASH_MAX_OUTPUT_CHARS = 1_000_000;

// Mirrors the server-side denylist: the executor enforces its own policy
// and never trusts the server's checks alone.
const BLOCKED_PATTERNS = [
  "rm -rf /",
  "rm -rf ~",
  "rm -rf *",
  "dd if=",
  "mkfs",
  "fdisk",
  ":(){:|:&};:",
  "> /dev/sd",
  "chmod -R 777 /",
];

function isBlockedCommand(command) {
  const lower = command.toLowerCase().trim();
  return BLOCKED_PATTERNS.some(blocked => lower.includes(blocked));
}

function boundOutput(output) {
  if (output.length <= BASH_MAX_OUTPUT_CHARS) return output;
  const half = Math.floor(BASH_MAX_OUTPUT_CHARS / 2);
  const omitted = output.length - BASH_MAX_OUTPUT_CHARS;
  return `${output.slice(0, half)}\n\n[... ${omitted} chars elided ...]\n\n${output.slice(-half)}`;
}

async function runBash(args, { signal, context } = {}) {
  const command = typeof args?.command === "string" ? args.command : "";
  const workingDir = args?.working_dir || context?.default_cwd || undefined;
  if (!command.trim()) {
    return {
      status: "failed",
      errorCode: "invalid_command",
      payload: { content: "bash requires a non-empty command.", preview: "Invalid command" },
    };
  }
  if (isBlockedCommand(command)) {
    return {
      status: "failed",
      errorCode: "permission_denied",
      payload: { content: `Blocked command: ${command}`, preview: "Blocked" },
    };
  }

  return new Promise(resolve => {
    let stdout = "";
    let stderr = "";
    let settled = false;
    const child = spawn("/bin/bash", ["-c", command], { cwd: workingDir, stdio: ["ignore", "pipe", "pipe"] });

    const finish = result => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      resolve(result);
    };
    const onAbort = () => {
      // The command already started: side effects may have landed, so the
      // terminal status is uncertain, never a clean "cancelled".
      child.kill("SIGKILL");
      finish({
        status: "uncertain",
        errorCode: "cancelled",
        payload: { content: "Command was cancelled before completion; side effects are unknown.", preview: "Cancelled" },
      });
    };
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      finish({
        status: "uncertain",
        errorCode: "timed_out",
        payload: {
          content: `Command timed out after ${BASH_TIMEOUT_MS / 1000}s; it ran until the timeout, so side effects may have landed.`,
          preview: "Timed out",
        },
      });
    }, BASH_TIMEOUT_MS);
    signal?.addEventListener("abort", onAbort);

    child.stdout.on("data", chunk => {
      stdout += chunk;
    });
    child.stderr.on("data", chunk => {
      stderr += chunk;
    });
    child.on("error", () => {
      finish({
        status: "failed",
        errorCode: "command_failed",
        payload: { content: "Command could not be started", preview: "Command failed" },
      });
    });
    child.on("close", code => {
      let output = "";
      if (stdout) output += stdout;
      if (stderr) output += `${output ? "\n" : ""}[stderr]\n${stderr}`;
      if (code !== 0) output += `\n[exit code: ${code}]`;
      output = boundOutput(output) || "(no output)";
      if (code !== 0) {
        finish({
          status: "failed",
          errorCode: "command_failed",
          payload: { content: output, preview: `Exit ${code}` },
        });
        return;
      }
      const lines = output.split("\n").length;
      finish({ status: "succeeded", payload: { content: output, preview: `${lines} lines` } });
    });
  });
}

async function readDeviceFile(args) {
  const filePath = typeof args?.path === "string" ? args.path : "";
  const offset = Number.isFinite(args?.offset) ? Math.max(1, Math.trunc(args.offset)) : 1;
  const limit = Number.isFinite(args?.limit) ? Math.max(1, Math.trunc(args.limit)) : 2000;
  if (!filePath || !path.isAbsolute(filePath)) {
    return {
      status: "failed",
      errorCode: "invalid_path",
      payload: { content: `read_device_file requires an absolute path, got: ${filePath || "(empty)"}`, preview: "Invalid path" },
    };
  }
  try {
    const raw = await fs.readFile(filePath, "utf8");
    const lines = raw.split("\n");
    const slice = lines.slice(offset - 1, offset - 1 + limit);
    let content = slice.join("\n");
    let truncatedNote = "";
    if (content.length > MAX_CONTENT_CHARS) {
      content = content.slice(0, MAX_CONTENT_CHARS);
      truncatedNote = "\n... [truncated at 256 KiB]";
    }
    const hidden = lines.length - (offset - 1) - slice.length;
    const tail = hidden > 0 ? `\n... [${hidden} more lines — raise offset/limit to read on]` : "";
    return {
      status: "succeeded",
      payload: {
        content: `${content}${truncatedNote}${tail}`,
        preview: `Read ${path.basename(filePath)}`,
        data: { path: filePath, total_lines: lines.length, offset, returned_lines: slice.length },
      },
    };
  } catch (error) {
    const code = error?.code === "ENOENT" ? "not_found" : error?.code === "EACCES" ? "permission_denied" : "read_failed";
    return {
      status: "failed",
      errorCode: code,
      payload: { content: `Could not read ${filePath}: ${error?.code ?? error?.message ?? error}`, preview: "Read failed" },
    };
  }
}

function registerDeviceTools(executorClient) {
  executorClient.registerHandler("read_device_file", readDeviceFile);
  executorClient.registerHandler("bash", runBash);
}

module.exports = { registerDeviceTools, readDeviceFile, runBash };
