// Device-side tool implementations for the client executor. Each handler
// receives the invocation arguments plus { signal } and returns
// { status, payload, errorCode? } — the bounded ToolResult projection the
// server rebuilds on its side.
const fs = require("node:fs/promises");
const path = require("node:path");

const MAX_CONTENT_CHARS = 262_144;

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
}

module.exports = { registerDeviceTools, readDeviceFile };
