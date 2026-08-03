// Device-side tool implementations for the client executor. Each handler
// receives the invocation arguments plus { signal, context } and returns
// { status, payload, errorCode? } — the bounded ToolResult projection the
// server rebuilds on its side. Formats and failure codes mirror the
// canonical definitions in apps/server/arden/tools/{files,bash}.py.
const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fsSync = require("node:fs");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");

const BASH_TIMEOUT_MS = 120_000;
const BASH_MAX_OUTPUT_CHARS = 1_000_000;
const DEFAULT_OFFSET = 1;
const DEFAULT_LINE_LIMIT = 500;
const OFFLOAD_READ_LIMIT = 100;
const DEFAULT_OFFLOAD_THRESHOLD = 50_000;
const RG_TIMEOUT_MS = 20_000;
const RG_EXCLUDE_GLOBS = ["!**/.git/**", "!**/node_modules/**", "!**/.venv/**", "!**/__pycache__/**"];

// --- shared helpers ---

function failure(code, content, preview) {
  return { status: "failed", errorCode: code, payload: { content, preview } };
}

function resolvePath(rawPath, cwd) {
  let expanded = rawPath;
  if (expanded === "~" || expanded.startsWith("~/")) {
    expanded = path.join(os.homedir(), expanded.slice(1));
  }
  if (path.isAbsolute(expanded)) return path.resolve(expanded);
  let root = cwd || process.cwd();
  if (root === "~" || root.startsWith("~/")) root = path.join(os.homedir(), root.slice(1));
  return path.resolve(root, expanded);
}

function relativePath(target, root) {
  const relative = path.relative(root, target);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) return target;
  return relative.split(path.sep).join("/");
}

function displayPath(target, cwd) {
  if (!cwd) return target;
  return relativePath(target, resolvePath(cwd));
}

function pathData(target, cwd) {
  const data = { path: displayPath(target, cwd) };
  if (cwd) data.absolute_path = target;
  return data;
}

function observationId(target) {
  return `file:${target}`;
}

function sha256(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

function sizeLabel(stat) {
  if (stat.isDirectory()) return "dir";
  const size = stat.size;
  if (size < 1024) return `${size}B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)}KB`;
  return `${(size / (1024 * 1024)).toFixed(1)}MB`;
}

function formatTimestamp(ms) {
  return new Date(ms).toISOString().replace("Z", "+00:00");
}

function formatLinesWithPagination(content, offset, limit) {
  const lines = content.split("\n");
  const totalLines = lines.length;
  const clamped = Math.max(1, Math.min(offset, totalLines));
  const startIdx = clamped - 1;
  const endIdx = Math.min(startIdx + limit, totalLines);
  const selected = lines.slice(startIdx, endIdx);
  const outputLines = selected.map((line, i) => `${String(startIdx + i + 1).padStart(6)}|${line}`);
  let header = `[${totalLines} lines]`;
  if (startIdx > 0 || endIdx < totalLines) {
    header = `[${totalLines} lines, showing ${clamped}-${endIdx}]`;
  }
  return `${header}\n${outputLines.join("\n")}`;
}

function encodeCursor(offset) {
  return Buffer.from(`offset:${offset}`, "utf8").toString("base64url");
}

function decodeCursor(cursor) {
  if (!cursor) return 0;
  const raw = Buffer.from(cursor, "base64url").toString("utf8");
  const [prefix, value] = raw.split(":", 2);
  const offset = Number(value);
  if (prefix !== "offset" || !Number.isInteger(offset) || offset < 0) {
    throw new Error("Invalid pagination cursor");
  }
  return offset;
}

function paginate(items, limit, cursor) {
  const offset = decodeCursor(cursor);
  const selected = items.slice(offset, offset + limit);
  const nextOffset = offset + selected.length;
  const hasMore = nextOffset < items.length;
  return {
    items: selected,
    total: items.length,
    has_more: hasMore,
    next_cursor: hasMore ? encodeCursor(nextOffset) : null,
  };
}

function padEnd(value, width) {
  return value.length >= width ? value : value + " ".repeat(width - value.length);
}

function readSnapshot(target) {
  const raw = fsSync.readFileSync(target);
  return { raw, sha256: sha256(raw), size: raw.length };
}

function revisionOrAbsent(target) {
  try {
    return readSnapshot(target).sha256;
  } catch {
    return "absent";
  }
}

class RevisionConflict extends Error {}

function atomicCompareAndSwap(target, content, expectedSha256) {
  if (revisionOrAbsent(target) !== expectedSha256) throw new RevisionConflict();
  const encoded = Buffer.from(content, "utf8");
  const tempPath = path.join(path.dirname(target), `.${path.basename(target)}.arden-${crypto.randomUUID()}.tmp`);
  let fd = null;
  try {
    fd = fsSync.openSync(tempPath, fsSync.constants.O_WRONLY | fsSync.constants.O_CREAT | fsSync.constants.O_EXCL, 0o666);
    if (fsSync.existsSync(target)) {
      fsSync.fchmodSync(fd, fsSync.statSync(target).mode & 0o777);
    }
    fsSync.writeSync(fd, encoded);
    fsSync.fsyncSync(fd);
    fsSync.closeSync(fd);
    fd = null;
    if (revisionOrAbsent(target) !== expectedSha256) throw new RevisionConflict();
    fsSync.renameSync(tempPath, target);
  } finally {
    if (fd !== null) fsSync.closeSync(fd);
    fsSync.rmSync(tempPath, { force: true });
  }
  return { sha256: sha256(encoded), size: encoded.length };
}

// --- bash ---

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
    return failure("invalid_command", "bash requires a non-empty command.", "Invalid command");
  }
  if (isBlockedCommand(command)) {
    return failure("permission_denied", `Blocked command: ${command}`, "Blocked");
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
      finish(failure("command_failed", "Command could not be started", "Command failed"));
    });
    child.on("close", code => {
      let output = "";
      if (stdout) output += stdout;
      if (stderr) output += `${output ? "\n" : ""}[stderr]\n${stderr}`;
      if (code !== 0) output += `\n[exit code: ${code}]`;
      output = boundOutput(output) || "(no output)";
      if (code !== 0) {
        finish(failure("command_failed", output, `Exit ${code}`));
        return;
      }
      const lines = output.split("\n").length;
      finish({ status: "succeeded", payload: { content: output, preview: `${lines} lines` } });
    });
  });
}

// --- read_file ---

async function readFile(args, { context } = {}) {
  const cwd = context?.default_cwd || undefined;
  const target = resolvePath(String(args?.path ?? ""), cwd);
  let offset = Number.isFinite(args?.offset) ? Math.trunc(args.offset) : DEFAULT_OFFSET;
  let limit = Number.isFinite(args?.limit) ? Math.trunc(args.limit) : DEFAULT_LINE_LIMIT;

  // Offloaded tool-result files with default params get capped so the agent
  // does not read the entire offloaded result back into context.
  const offloadDir = context?.offload_dir;
  const isOffloaded = Boolean(offloadDir) && target.startsWith(offloadDir);
  if (isOffloaded && offset === DEFAULT_OFFSET && limit === DEFAULT_LINE_LIMIT) {
    limit = OFFLOAD_READ_LIMIT;
  }

  let stat;
  try {
    stat = await fs.stat(target);
  } catch {
    return failure(
      "not_found",
      `File not found: ${args.path}. Check the path or use list_files() to list the directory.`,
      "Not found",
    );
  }
  if (stat.isDirectory()) {
    return failure(
      "invalid_ref",
      `Path is a directory, not a file: ${args.path}. Use list_files(path=${JSON.stringify(args.path)}) to list contents.`,
      "Not a file",
    );
  }

  let snapshot;
  try {
    snapshot = readSnapshot(target);
  } catch (error) {
    if (error?.code === "EACCES" || error?.code === "EPERM") {
      return failure(
        "permission_denied",
        `Permission denied: ${args.path}. File may be protected or require elevated access.`,
        "Denied",
      );
    }
    return failure("read_failed", `Could not read file: ${args.path}`, "Read failed");
  }

  const content = snapshot.raw.toString("utf8");
  const formatted = formatLinesWithPagination(content, offset, limit);
  const totalLines = content.split("\n").length;
  const threshold = context?.offload_threshold ?? DEFAULT_OFFLOAD_THRESHOLD;
  const contentRead = offset === 1 && limit >= totalLines && formatted.length <= threshold;

  const payload = {
    content: formatted,
    preview: `Read ${totalLines} lines`,
    data: { ...pathData(target, cwd), size: snapshot.size },
    observations: [{ id: observationId(target), version: snapshot.sha256, content_read: contentRead }],
  };
  if (!isOffloaded) {
    payload.source_refs = [{ provider: "filesystem", kind: "file", ref: target, title: path.basename(target) }];
  }
  return { status: "succeeded", payload };
}

// --- list_files ---

async function listFiles(args, { context } = {}) {
  const cwd = context?.default_cwd || undefined;
  const root = resolvePath(String(args?.path ?? "."), cwd);
  const limit = Number.isFinite(args?.limit) ? Math.trunc(args.limit) : 200;
  const includeHidden = Boolean(args?.include_hidden);

  let rootStat;
  try {
    rootStat = await fs.stat(root);
  } catch {
    return failure("not_found", `Directory not found: ${args.path}`, "Not found");
  }
  if (!rootStat.isDirectory()) {
    return failure("invalid_ref", `Path is not a directory: ${args.path}`, "Not a directory");
  }

  let names;
  try {
    names = await fs.readdir(root);
  } catch (error) {
    if (error?.code === "EACCES" || error?.code === "EPERM") {
      return failure("permission_denied", `Permission denied: ${args.path}`, "Denied");
    }
    return failure("read_failed", `Could not list directory: ${args.path}`, "List failed");
  }

  const entries = [];
  for (const name of names) {
    if (!includeHidden && name.startsWith(".")) continue;
    const childPath = path.join(root, name);
    let stat;
    try {
      stat = await fs.stat(childPath);
    } catch {
      continue;
    }
    entries.push({
      _sort: [stat.isDirectory() ? 0 : 1, name.toLowerCase()],
      entry: {
        name: `${name}${stat.isDirectory() ? "/" : ""}`,
        ...pathData(childPath, cwd),
        kind: stat.isDirectory() ? "directory" : "file",
        size: sizeLabel(stat),
        modified_at: formatTimestamp(stat.mtimeMs),
      },
    });
  }
  entries.sort((a, b) => a._sort[0] - b._sort[0] || (a._sort[1] < b._sort[1] ? -1 : a._sort[1] > b._sort[1] ? 1 : 0));
  const sorted = entries.map(item => item.entry);

  let page;
  try {
    page = paginate(sorted, limit, args?.cursor);
  } catch {
    return failure("invalid_ref", "Invalid list_files pagination cursor.", "Invalid cursor");
  }
  const lines = page.items.map(item => `${padEnd(item.name, 48)} ${padEnd(item.size, 8)} ${item.modified_at}`);
  if (page.has_more) lines.push(`... more available; next_cursor: ${page.next_cursor}`);
  const header = `${displayPath(root, cwd)} (${sorted.length} entries)`;
  return {
    status: "succeeded",
    payload: {
      content: header + (lines.length ? `\n${lines.join("\n")}` : "\n(empty)"),
      preview: `${sorted.length} entries`,
      data: {
        ...pathData(root, cwd),
        entries: page.items,
        items: page.items,
        total: page.total,
        has_more: page.has_more,
        next_cursor: page.next_cursor,
      },
    },
  };
}

// --- ripgrep-backed find/search ---

function runRg(rgArgs, cwd, { limitLines, signal }) {
  return new Promise((resolve, reject) => {
    const child = spawn("rg", rgArgs, { cwd, stdio: ["ignore", "pipe", "ignore"] });
    let buffer = "";
    const lines = [];
    let settled = false;
    let truncated = false;

    const finish = (error = null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      if (error) reject(error);
      else resolve({ lines, truncated });
    };
    const onAbort = () => {
      child.kill("SIGKILL");
      finish(new Error("aborted"));
    };
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      finish(new Error(`ripgrep timed out after ${RG_TIMEOUT_MS / 1000}s`));
    }, RG_TIMEOUT_MS);
    signal?.addEventListener("abort", onAbort);

    child.stdout.on("data", chunk => {
      buffer += chunk;
      const parts = buffer.split("\n");
      buffer = parts.pop() ?? "";
      for (const line of parts) {
        if (line) lines.push(line);
        if (lines.length > limitLines) {
          truncated = true;
          child.kill("SIGKILL");
          finish();
          return;
        }
      }
    });
    child.on("error", error => finish(error));
    child.on("close", code => {
      if (buffer) lines.push(buffer);
      if (code !== 0 && code !== 1 && !truncated) {
        finish(new Error(`ripgrep failed with exit code ${code}`));
        return;
      }
      finish();
    });
  });
}

async function findFiles(args, { signal, context } = {}) {
  const cwd = context?.default_cwd || undefined;
  const root = resolvePath(String(args?.path ?? "."), cwd);
  const pattern = String(args?.pattern ?? "*");
  const limit = Number.isFinite(args?.limit) ? Math.trunc(args.limit) : 200;

  let rootStat;
  try {
    rootStat = await fs.stat(root);
  } catch {
    return failure("not_found", `Directory not found: ${args.path}`, "Not found");
  }
  if (!rootStat.isDirectory()) {
    return failure("invalid_ref", `Path is not a directory: ${args.path}`, "Not a directory");
  }

  const rgArgs = ["--files", "--color", "never", "--sort", "path", "--glob", pattern];
  for (const glob of RG_EXCLUDE_GLOBS) rgArgs.push("--glob", glob);
  if (args?.include_hidden) rgArgs.push("--hidden");

  let output;
  try {
    output = await runRg(rgArgs, root, { limitLines: limit, signal });
  } catch {
    return {
      status: "failed",
      errorCode: "search_failed",
      payload: {
        content: "File-name search with ripgrep failed.",
        preview: "Find failed",
        data: { path: root, pattern, matches: [] },
      },
    };
  }

  const matches = [];
  for (const relative of output.lines) {
    const filePath = path.resolve(root, relative);
    let stat;
    try {
      stat = await fs.stat(filePath);
    } catch {
      continue;
    }
    if (!stat.isFile()) continue;
    matches.push({ path: filePath, relative_path: relative, size: sizeLabel(stat) });
  }

  const hasMore = output.truncated || matches.length > limit;
  const visible = matches.slice(0, limit).map(item => ({ ...item, ...pathData(item.path, cwd) }));
  let lines = visible.map(item => `${padEnd(item.relative_path, 72)} ${item.size}`);
  if (!lines.length) lines = ["No files found."];
  else if (hasMore) lines.push(`Showing ${visible.length} files; more exist. Narrow path/pattern to continue.`);
  return {
    status: "succeeded",
    payload: {
      content: `${displayPath(root, cwd)} / ${pattern}\n${lines.join("\n")}`,
      preview: `${visible.length} files${hasMore ? " (capped)" : ""}`,
      data: { ...pathData(root, cwd), pattern, matches: visible, has_more: hasMore },
    },
  };
}

async function searchText(args, { signal, context } = {}) {
  const cwd = context?.default_cwd || undefined;
  const root = resolvePath(String(args?.path ?? "."), cwd);
  const query = String(args?.query ?? "");
  const limit = Number.isFinite(args?.limit) ? Math.trunc(args.limit) : 100;

  let rootStat;
  try {
    rootStat = await fs.stat(root);
  } catch {
    return failure("not_found", `Path not found: ${args.path}`, "Not found");
  }

  const searchCwd = rootStat.isDirectory() ? root : path.dirname(root);
  const target = rootStat.isDirectory() ? "." : path.basename(root);
  const rgArgs = [
    "--json",
    "--fixed-strings",
    "--sort",
    "path",
    "--line-number",
    "--column",
    "--color",
    "never",
    "--no-heading",
  ];
  for (const glob of RG_EXCLUDE_GLOBS) rgArgs.push("--glob", glob);
  if (args?.file_glob) rgArgs.push("--glob", args.file_glob);
  rgArgs.push("--", query, target);

  let output;
  try {
    // Every rg --json line is one event; matches are a subset, so scan a
    // generous multiple before capping match extraction below.
    output = await runRg(rgArgs, searchCwd, { limitLines: (limit + 1) * 8, signal });
  } catch {
    return {
      status: "failed",
      errorCode: "search_failed",
      payload: {
        content: "Text search with ripgrep failed.",
        preview: "Search failed",
        data: { path: root, query, matches: [] },
      },
    };
  }

  const matches = [];
  let overflow = output.truncated;
  for (const line of output.lines) {
    let event;
    try {
      event = JSON.parse(line);
    } catch {
      continue;
    }
    if (event?.type !== "match") continue;
    const data = event.data ?? {};
    const rawPath = data.path?.text ?? "";
    const filePath = path.resolve(searchCwd, rawPath);
    const lineText = String(data.lines?.text ?? "").replace(/[\r\n]+$/, "");
    let column = lineText.indexOf(query) + 1;
    if (column <= 0) column = Number(data.submatches?.[0]?.start ?? 0) + 1;
    matches.push({
      path: filePath,
      relative_path: relativePath(filePath, root),
      line: Number(data.line_number),
      column,
      text: lineText,
    });
    if (matches.length > limit) {
      overflow = true;
      break;
    }
  }

  if (!matches.length) {
    return {
      status: "succeeded",
      payload: {
        content: `No matches for '${query}' under ${root}.`,
        preview: "0 matches",
        data: { path: root, query, matches: [] },
      },
    };
  }
  const hasMore = overflow || matches.length > limit;
  const visible = matches.slice(0, limit);
  let content = visible.map(m => `${m.relative_path}:${m.line}:${m.column}: ${m.text}`).join("\n");
  if (hasMore) content += `\nShowing ${visible.length} matches; more exist. Narrow path/query to continue.`;
  return {
    status: "succeeded",
    payload: {
      content,
      preview: `${visible.length} matches${hasMore ? " (capped)" : ""}`,
      data: { path: root, query, matches: visible, has_more: hasMore },
    },
  };
}

// --- write_file / edit_file ---

function freshReadRequired(target, cwd) {
  return failure(
    "fresh_read_required",
    `Read ${displayPath(target, cwd)} completely before replacing it.`,
    "Read file first",
  );
}

function writeConflict(target, cwd) {
  return failure(
    "write_conflict",
    `File changed since it was read: ${displayPath(target, cwd)}.`,
    "Write conflict",
  );
}

async function writeFile(args, { context } = {}) {
  const cwd = context?.default_cwd || undefined;
  const target = resolvePath(String(args?.path ?? ""), cwd);
  const content = String(args?.content ?? "");

  if (fsSync.existsSync(target) && fsSync.statSync(target).isDirectory()) {
    return failure("invalid_ref", `Path is a directory: ${args.path}`, "Is directory");
  }
  if (!fsSync.existsSync(path.dirname(target))) {
    return failure(
      "not_found",
      `Parent directory does not exist: ${displayPath(path.dirname(target), cwd)}`,
      "No parent",
    );
  }

  const observation = context?.resource_observations?.[observationId(target)];
  let revision;
  let operation;
  try {
    if (fsSync.existsSync(target)) {
      const current = readSnapshot(target);
      if (current.raw.toString("utf8") === content) {
        return {
          status: "succeeded",
          payload: {
            content: `${displayPath(target, cwd)} already has the requested content.`,
            preview: "File unchanged",
            data: { ...pathData(target, cwd), lines: content ? content.split("\n").length : 0, size: current.size },
            observations: [
              { id: observationId(target), version: current.sha256, content_read: Boolean(observation?.content_read) },
            ],
          },
        };
      }
      if (!observation?.content_read || !observation?.version) {
        return freshReadRequired(target, cwd);
      }
      if (current.sha256 !== observation.version) {
        return writeConflict(target, cwd);
      }
      operation = "replace";
      revision = atomicCompareAndSwap(target, content, observation.version);
    } else {
      operation = "create";
      revision = atomicCompareAndSwap(target, content, "absent");
    }
  } catch (error) {
    if (error instanceof RevisionConflict) return writeConflict(target, cwd);
    if (error?.code === "EACCES" || error?.code === "EPERM") {
      return failure("permission_denied", `Permission denied: ${args.path}`, "Denied");
    }
    return failure("write_failed", `Could not write file: ${args.path}`, "Write failed");
  }

  const lines = content ? content.split("\n").length : 0;
  const display = displayPath(target, cwd);
  return {
    status: "succeeded",
    payload: {
      content: `Wrote ${display} (${lines} lines).`,
      preview: `Wrote ${lines} lines`,
      data: { ...pathData(target, cwd), lines, size: revision.size },
      effect: { operation, target },
      observations: [{ id: observationId(target), version: revision.sha256, content_read: true }],
    },
  };
}

async function editFile(args, { context } = {}) {
  const cwd = context?.default_cwd || undefined;
  const target = resolvePath(String(args?.path ?? ""), cwd);
  const oldText = String(args?.old_text ?? "");
  const newText = String(args?.new_text ?? "");

  if (!fsSync.existsSync(target)) {
    return failure("not_found", `File not found: ${args.path}`, "Not found");
  }
  if (!fsSync.statSync(target).isFile()) {
    return failure("invalid_ref", `Path is not a file: ${args.path}`, "Not a file");
  }

  let current;
  try {
    current = readSnapshot(target);
  } catch (error) {
    if (error?.code === "EACCES" || error?.code === "EPERM") {
      return failure("permission_denied", `Permission denied: ${args.path}`, "Denied");
    }
    return failure("read_failed", `Could not edit file: ${args.path}`, "Edit failed");
  }
  const before = current.raw.toString("utf8");
  const count = before.split(oldText).length - 1;
  if (count === 0) {
    return failure("not_found", "Text block not found. Read the file and include more exact context.", "No match");
  }
  if (count > 1) {
    return failure(
      "invalid_ref",
      `Text block matched ${count} times. Include a larger exact block so the edit is unique.`,
      "Ambiguous",
    );
  }

  // Function replacement so `$&`-style patterns in new_text are inserted
  // literally instead of being expanded by String.replace.
  const after = before.replace(oldText, () => newText);
  let revision;
  try {
    revision = atomicCompareAndSwap(target, after, current.sha256);
  } catch (error) {
    if (error instanceof RevisionConflict) return writeConflict(target, cwd);
    if (error?.code === "EACCES" || error?.code === "EPERM") {
      return failure("permission_denied", `Permission denied: ${args.path}`, "Denied");
    }
    return failure("write_failed", `Could not edit file: ${args.path}`, "Edit failed");
  }

  const observation = context?.resource_observations?.[observationId(target)];
  return {
    status: "succeeded",
    payload: {
      content: `Edited ${displayPath(target, cwd)}.`,
      preview: "Edited",
      data: { ...pathData(target, cwd), size: revision.size },
      effect: { operation: "edit", target },
      observations: [
        { id: observationId(target), version: revision.sha256, content_read: Boolean(observation?.content_read) },
      ],
    },
  };
}

function registerDeviceTools(executorClient) {
  executorClient.registerHandler("bash", runBash);
  executorClient.registerHandler("file_read", readFile);
  executorClient.registerHandler("file_list", listFiles);
  executorClient.registerHandler("file_find", findFiles);
  executorClient.registerHandler("file_search_text", searchText);
  executorClient.registerHandler("file_write", writeFile);
  executorClient.registerHandler("file_edit", editFile);
}

module.exports = {
  registerDeviceTools,
  runBash,
  readFile,
  listFiles,
  findFiles,
  searchText,
  writeFile,
  editFile,
};
