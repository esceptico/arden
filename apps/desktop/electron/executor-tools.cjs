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
const FILE_DISCOVERY_MISS_LIMIT = 2;
const FILE_DISCOVERY_CANDIDATE_LIMIT = 8;
const SEARCH_SNIPPET_MAX_BYTES = 4_096;
const SEARCH_RESULT_MAX_BYTES = 256_000;
const SEARCH_RECORD_MAX_BYTES = 16_384;
const SEARCH_PATH_MAX_BYTES = 32_768;
const SEARCH_SCAN_MAX_BYTES = 8 * 1024 * 1024;
const SEARCH_QUERY_MAX_CHARS = 4_096;
const SEARCH_QUERY_MAX_BYTES = 16_384;
const SEARCH_INPUT_PATH_MAX_CHARS = 4_096;
const SEARCH_INPUT_PATH_MAX_BYTES = 16_384;
const SEARCH_GLOB_MAX_CHARS = 1_024;
const SEARCH_GLOB_MAX_BYTES = 4_096;
const SEARCH_CURSOR_VERSION = 1;

// --- shared helpers ---

function failure(code, content, preview, payload = {}) {
  return { status: "failed", errorCode: code, payload: { content, preview, ...payload } };
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

function fileDiscoveryUpdate({ observed = [], discoveredRoots = [], missedRoots = [] } = {}) {
  return {
    version: 1,
    observed,
    discovered_roots: discoveredRoots,
    missed_roots: missedRoots,
  };
}

function observedFilePath(target, kind) {
  return { path: target, kind };
}

function isPermissionError(error) {
  return error?.code === "EACCES" || error?.code === "EPERM";
}

function isMissingError(error) {
  return error?.code === "ENOENT" || error?.code === "ENOTDIR";
}

function pathInspectionFailure(error, target, cwd) {
  if (isPermissionError(error)) {
    return failure(
      "permission_denied",
      `Permission denied while inspecting ${displayPath(target, cwd)}.`,
      "Permission denied",
      { data: { resolved_path: target } },
    );
  }
  return failure(
    "path_inspection_failed",
    `Could not inspect ${displayPath(target, cwd)}.`,
    "Path inspection failed",
    { data: { resolved_path: target } },
  );
}

function pathKind(stat) {
  if (stat.isFile()) return "file";
  if (stat.isDirectory()) return "directory";
  return "other";
}

function isWithinPath(target, root) {
  const relative = path.relative(root, target);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function discoveryRequiredRoot(target, context, { discoveryOperation = false } = {}) {
  const state = context?.file_discovery;
  if (!state) return null;
  if (state.observed_paths?.[target]) return null;
  const roots = Object.entries(state.miss_counts ?? {})
    .filter(([root, misses]) => Number(misses) >= FILE_DISCOVERY_MISS_LIMIT && isWithinPath(target, root))
    .map(([root]) => root)
    .sort((a, b) => b.length - a.length);
  const root = roots[0] ?? null;
  if (discoveryOperation && root === target) return null;
  return root;
}

function discoveryRequiredFailure(target, root, cwd) {
  const visibleTarget = displayPath(target, cwd);
  const visibleRoot = displayPath(root, cwd);
  return failure(
    "discovery_required",
    `Path discovery is required before trying another unobserved path under ${visibleRoot}. ` +
      `Call file_list or file_find on ${visibleRoot}, then use an exact returned path. Requested: ${visibleTarget}.`,
    "Discover paths first",
    {
      data: {
        requested_path: visibleTarget,
        resolved_path: target,
        discovery_root: visibleRoot,
        absolute_discovery_root: root,
      },
    },
  );
}

async function missingPathFailure({ target, requestedPath, cwd, resource, cause }) {
  if (!isMissingError(cause)) return pathInspectionFailure(cause, target, cwd);
  let ancestor = path.dirname(target);
  let ancestorStat;
  while (true) {
    try {
      ancestorStat = await fs.stat(ancestor);
      break;
    } catch (error) {
      if (!isMissingError(error)) return pathInspectionFailure(error, ancestor, cwd);
      const parent = path.dirname(ancestor);
      if (parent === ancestor) {
        return failure(
          "not_found",
          `${resource} not found: ${displayPath(target, cwd)}. No existing ancestor could be inspected.`,
          "Not found",
          { data: { requested_path: String(requestedPath ?? ""), resolved_path: target, candidates: [] } },
        );
      }
      ancestor = parent;
    }
  }

  let entries = [];
  if (ancestorStat.isDirectory()) {
    let dirents;
    try {
      dirents = await fs.readdir(ancestor, { withFileTypes: true });
    } catch (error) {
      return pathInspectionFailure(error, ancestor, cwd);
    }
    entries = dirents
      .sort((a, b) => a.name.localeCompare(b.name))
      .slice(0, FILE_DISCOVERY_CANDIDATE_LIMIT)
      .map(entry => ({
        name: entry.name,
        path: path.join(ancestor, entry.name),
        kind: entry.isFile() ? "file" : entry.isDirectory() ? "directory" : "other",
      }));
  }

  const visibleTarget = displayPath(target, cwd);
  const visibleAncestor = displayPath(ancestor, cwd);
  const candidateLines = entries.map(entry => `- ${displayPath(entry.path, cwd)} (${entry.kind})`);
  const candidates = candidateLines.length ? `\nAvailable children:\n${candidateLines.join("\n")}` : "";
  return failure(
    "not_found",
    `${resource} not found: ${visibleTarget}. Nearest existing ancestor: ${visibleAncestor}.${candidates}\n` +
      `List or find under ${visibleAncestor}, then use an exact returned path.`,
    "Not found",
    {
      data: {
        requested_path: String(requestedPath ?? ""),
        resolved_path: target,
        nearest_existing_ancestor: ancestor,
        candidates: entries,
      },
      file_discovery: fileDiscoveryUpdate({
        observed: [observedFilePath(ancestor, pathKind(ancestorStat)), ...entries.map(e => observedFilePath(e.path, e.kind))],
        missedRoots: [ancestor],
      }),
    },
  );
}

function sha256(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

function truncateUtf8(value, maxBytes) {
  const encoded = Buffer.from(value, "utf8");
  if (encoded.length <= maxBytes) return { text: value, truncated: false };
  let end = maxBytes;
  while (end > 0 && (encoded[end] & 0xc0) === 0x80) end -= 1;
  return { text: encoded.subarray(0, end).toString("utf8"), truncated: true };
}

function searchFingerprint({ root, query, fileGlob, outputMode }) {
  return sha256(Buffer.from(JSON.stringify({ root, query, file_glob: fileGlob ?? null, output_mode: outputMode })));
}

function encodeSearchCursor(offset, fingerprint) {
  return Buffer.from(JSON.stringify({ v: SEARCH_CURSOR_VERSION, offset, fingerprint }), "utf8").toString("base64url");
}

function decodeSearchCursor(cursor, fingerprint) {
  if (!cursor) return 0;
  const parsed = JSON.parse(Buffer.from(cursor, "base64url").toString("utf8"));
  if (
    parsed?.v !== SEARCH_CURSOR_VERSION ||
    !Number.isInteger(parsed.offset) ||
    parsed.offset < 0 ||
    parsed.fingerprint !== fingerprint
  ) {
    throw new Error("Invalid search cursor");
  }
  return parsed.offset;
}

function boundedSearchResult(payload) {
  if (Buffer.byteLength(JSON.stringify(payload), "utf8") <= SEARCH_RESULT_MAX_BYTES) {
    return { status: "succeeded", payload };
  }
  return failure(
    "result_too_large",
    "The bounded search projection exceeded its response budget. Narrow the path, query, or file glob and retry.",
    "Search result too large",
    { data: { matches: [], has_more: true, next_cursor: null, limit_reason: "byte_limit" } },
  );
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
  } catch (error) {
    if (isMissingError(error)) return "absent";
    throw error;
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
  const offloadRoot = typeof offloadDir === "string" && offloadDir ? resolvePath(offloadDir, cwd) : null;
  const isOffloaded = offloadRoot !== null && isWithinPath(target, offloadRoot);
  if (isOffloaded && offset === DEFAULT_OFFSET && limit === DEFAULT_LINE_LIMIT) {
    limit = OFFLOAD_READ_LIMIT;
  }

  const requiredRoot = discoveryRequiredRoot(target, context);
  if (requiredRoot) return discoveryRequiredFailure(target, requiredRoot, cwd);

  let stat;
  try {
    stat = await fs.stat(target);
  } catch (error) {
    return missingPathFailure({ target, requestedPath: args?.path, cwd, resource: "File", cause: error });
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
    file_discovery: fileDiscoveryUpdate({ observed: [observedFilePath(target, "file")] }),
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

  const requiredRoot = discoveryRequiredRoot(root, context, { discoveryOperation: true });
  if (requiredRoot) return discoveryRequiredFailure(root, requiredRoot, cwd);

  let rootStat;
  try {
    rootStat = await fs.stat(root);
  } catch (error) {
    return missingPathFailure({ target: root, requestedPath: args?.path, cwd, resource: "Directory", cause: error });
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
      file_discovery: fileDiscoveryUpdate({
        observed: [
          observedFilePath(root, "directory"),
          ...page.items.map(item => observedFilePath(item.absolute_path ?? path.resolve(root, item.path), item.kind)),
        ],
        discoveredRoots: [root],
      }),
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

function runSearchRecords(rgArgs, cwd, { mode, signal, onRecord }) {
  return new Promise((resolve, reject) => {
    const child = spawn("rg", rgArgs, { cwd, stdio: ["ignore", "pipe", "ignore"] });
    let settled = false;
    let intentionallyStopped = false;
    let limitReason = null;
    let scanBytes = 0;
    let phase = "path";
    let pathParts = [];
    let pathBytes = 0;
    let detailParts = [];
    let detailBytes = 0;
    let currentPath = null;

    const finish = error => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      if (error) reject(error);
      else resolve({ limitReason });
    };
    const stop = reason => {
      if (settled) return;
      intentionallyStopped = true;
      limitReason ??= reason;
      child.kill("SIGKILL");
      finish();
    };
    const onAbort = () => {
      child.kill("SIGKILL");
      finish(new Error("aborted"));
    };
    const timer = setTimeout(() => stop("timeout"), RG_TIMEOUT_MS);
    signal?.addEventListener("abort", onAbort);

    const appendPath = value => {
      pathBytes += value.length;
      if (pathBytes > SEARCH_PATH_MAX_BYTES) return false;
      pathParts.push(value);
      return true;
    };
    const appendDetail = value => {
      detailBytes += value.length;
      if (detailBytes > SEARCH_RECORD_MAX_BYTES) return false;
      detailParts.push(value);
      return true;
    };
    const resetPath = () => {
      pathParts = [];
      pathBytes = 0;
    };
    const resetDetail = () => {
      detailParts = [];
      detailBytes = 0;
    };

    child.stdout.on("data", chunk => {
      if (settled) return;
      scanBytes += chunk.length;
      if (scanBytes > SEARCH_SCAN_MAX_BYTES) {
        stop("scan_byte_limit");
        return;
      }

      let cursor = 0;
      while (cursor < chunk.length && !settled) {
        if (phase === "path") {
          const delimiter = chunk.indexOf(0, cursor);
          const end = delimiter === -1 ? chunk.length : delimiter;
          if (!appendPath(chunk.subarray(cursor, end))) {
            stop("record_byte_limit");
            return;
          }
          if (delimiter === -1) return;
          currentPath = Buffer.concat(pathParts, pathBytes).toString("utf8");
          resetPath();
          cursor = delimiter + 1;
          if (mode === "files_only") {
            const reason = onRecord({ path: currentPath });
            currentPath = null;
            if (reason) stop(reason);
          } else {
            phase = "detail";
          }
          continue;
        }

        const delimiter = chunk.indexOf(10, cursor);
        const end = delimiter === -1 ? chunk.length : delimiter;
        if (!appendDetail(chunk.subarray(cursor, end))) {
          stop("record_byte_limit");
          return;
        }
        if (delimiter === -1) return;
        const detail = Buffer.concat(detailParts, detailBytes).toString("utf8").replace(/\r$/, "");
        resetDetail();
        phase = "path";
        cursor = delimiter + 1;
        const reason = onRecord({ path: currentPath, detail });
        currentPath = null;
        if (reason) stop(reason);
      }
    });
    child.on("error", error => finish(error));
    child.on("close", code => {
      if (settled) return;
      if (code !== 0 && code !== 1 && !intentionallyStopped) {
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

  const requiredRoot = discoveryRequiredRoot(root, context, { discoveryOperation: true });
  if (requiredRoot) return discoveryRequiredFailure(root, requiredRoot, cwd);

  let rootStat;
  try {
    rootStat = await fs.stat(root);
  } catch (error) {
    return missingPathFailure({ target: root, requestedPath: args?.path, cwd, resource: "Directory", cause: error });
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
      file_discovery: fileDiscoveryUpdate({
        observed: [
          observedFilePath(root, "directory"),
          ...visible.map(item => observedFilePath(item.absolute_path ?? item.path, "file")),
        ],
        discoveredRoots: [root],
      }),
    },
  };
}

async function searchText(args, { signal, context } = {}) {
  const cwd = context?.default_cwd || undefined;
  const root = resolvePath(String(args?.path ?? "."), cwd);
  const query = String(args?.query ?? "");
  const requestedPath = String(args?.path ?? ".");
  const fileGlob = args?.file_glob ? String(args.file_glob) : null;
  const outputMode = String(args?.output_mode ?? "content");
  const limit = Number.isFinite(args?.limit) ? Math.trunc(args.limit) : 100;
  if (Array.from(query).length > SEARCH_QUERY_MAX_CHARS || Buffer.byteLength(query, "utf8") > SEARCH_QUERY_MAX_BYTES) {
    return failure("invalid_ref", "Search query exceeds the 4,096-character input limit.", "Query too large");
  }
  if (
    Array.from(requestedPath).length > SEARCH_INPUT_PATH_MAX_CHARS ||
    Buffer.byteLength(requestedPath, "utf8") > SEARCH_INPUT_PATH_MAX_BYTES
  ) {
    return failure("invalid_ref", "Search path exceeds the 4,096-character input limit.", "Path too large");
  }
  if (
    fileGlob &&
    (Array.from(fileGlob).length > SEARCH_GLOB_MAX_CHARS || Buffer.byteLength(fileGlob, "utf8") > SEARCH_GLOB_MAX_BYTES)
  ) {
    return failure("invalid_ref", "Search file_glob exceeds the 1,024-character input limit.", "Glob too large");
  }
  if (!["content", "files_only", "count"].includes(outputMode)) {
    return failure("invalid_ref", `Unknown output_mode: ${outputMode}`, "Invalid mode");
  }

  const requiredRoot = discoveryRequiredRoot(root, context, { discoveryOperation: true });
  if (requiredRoot) return discoveryRequiredFailure(root, requiredRoot, cwd);

  let rootStat;
  try {
    rootStat = await fs.stat(root);
  } catch (error) {
    return missingPathFailure({ target: root, requestedPath: args?.path, cwd, resource: "Path", cause: error });
  }

  const searchCwd = rootStat.isDirectory() ? root : path.dirname(root);
  const target = rootStat.isDirectory() ? "." : path.basename(root);
  const fingerprint = searchFingerprint({ root, query, fileGlob: args?.file_glob, outputMode });
  let offset;
  try {
    offset = decodeSearchCursor(args?.cursor, fingerprint);
  } catch {
    return failure(
      "invalid_ref",
      "Invalid or stale file_search_text cursor. Re-run the first page with the same query, path, glob, and mode.",
      "Invalid cursor",
    );
  }

  const rgArgs = ["--fixed-strings", "--sort", "path", "--color", "never", "--no-heading"];
  for (const glob of RG_EXCLUDE_GLOBS) rgArgs.push("--glob", glob);
  if (args?.file_glob) rgArgs.push("--glob", args.file_glob);
  if (outputMode === "content") {
    rgArgs.push("--vimgrep", "--null", "--max-columns", String(SEARCH_SNIPPET_MAX_BYTES), "--max-columns-preview");
  } else if (outputMode === "files_only") {
    rgArgs.push("--files-with-matches", "--null");
  } else {
    rgArgs.push("--count-matches", "--null", "--with-filename");
  }
  rgArgs.push("--", query, target);

  const matches = [];
  const contentLines = [];
  let resultBytes = 0;
  let recordIndex = 0;
  let totalMatches = 0;
  let hasMore = false;
  let collectionLimitReason = null;

  const relativeResultPath = filePath =>
    rootStat.isDirectory() ? relativePath(filePath, root) : path.basename(filePath);
  const addItem = (item, line) => {
    const lineBytes = Buffer.byteLength(line, "utf8") + 1;
    if (resultBytes + lineBytes > SEARCH_RESULT_MAX_BYTES) {
      hasMore = true;
      collectionLimitReason = "byte_limit";
      return false;
    }
    resultBytes += lineBytes;
    matches.push(item);
    contentLines.push(line);
    return true;
  };

  const onRecord = record => {
    const index = recordIndex;
    recordIndex += 1;

    let filePath = path.resolve(searchCwd, record.path);
    let item;
    let line;
    if (outputMode === "content") {
      const firstColon = record.detail.indexOf(":");
      const secondColon = record.detail.indexOf(":", firstColon + 1);
      if (firstColon <= 0 || secondColon <= firstColon) return null;
      const lineNumber = Number(record.detail.slice(0, firstColon));
      const column = Number(record.detail.slice(firstColon + 1, secondColon));
      const snippet = truncateUtf8(record.detail.slice(secondColon + 1), SEARCH_SNIPPET_MAX_BYTES);
      const relative = relativeResultPath(filePath);
      item = {
        path: filePath,
        relative_path: relative,
        line: lineNumber,
        column,
        snippet_truncated: snippet.truncated,
      };
      line = `${relative}:${lineNumber}:${column}: ${snippet.text}${snippet.truncated ? "…" : ""}`;
    } else if (outputMode === "files_only") {
      const relative = relativeResultPath(filePath);
      item = { path: filePath, relative_path: relative };
      line = relative;
    } else {
      const count = Number(record.detail);
      if (!Number.isFinite(count)) return null;
      totalMatches += count;
      const relative = relativeResultPath(filePath);
      item = { path: filePath, relative_path: relative, count };
      line = `${relative}: ${count}`;
    }

    if (index < offset) return null;
    if (matches.length >= limit) {
      hasMore = true;
      return outputMode === "count" ? null : "result_limit";
    }
    if (!addItem(item, line)) return outputMode === "count" ? null : "byte_limit";
    return null;
  };

  let output;
  try {
    output = await runSearchRecords(rgArgs, searchCwd, { mode: outputMode, signal, onRecord });
  } catch (error) {
    if (signal?.aborted) throw error;
    return {
      status: "failed",
      errorCode: "search_failed",
      payload: {
        content: "Text search with ripgrep failed.",
        preview: "Search failed",
        data: { path: root, query, output_mode: outputMode, matches: [] },
      },
    };
  }

  if (output.limitReason) hasMore = true;
  let limitReason = collectionLimitReason ?? output.limitReason ?? (hasMore ? "result_limit" : null);
  const discoveryRoot = rootStat.isDirectory() ? root : searchCwd;
  const buildProjection = () => {
    const nextCursor = hasMore && matches.length ? encodeSearchCursor(offset + matches.length, fingerprint) : null;
    const data = {
      ...pathData(root, cwd),
      query,
      output_mode: outputMode,
      matches,
      has_more: hasMore,
      next_cursor: nextCursor,
      limit_reason: limitReason,
    };
    if (outputMode === "count") {
      data.total_matches = totalMatches;
      data.files_counted = recordIndex;
      data.count_complete = output.limitReason === null;
    }
    let content = contentLines.join("\n");
    if (hasMore && matches.length) {
      content += `\nShowing ${matches.length} result(s); more exist (${limitReason}). Continue with next_cursor or narrow scope.`;
    }
    return {
      content,
      preview: `${matches.length} ${outputMode === "files_only" ? "files" : outputMode === "count" ? "counts" : "matches"}${hasMore ? " (capped)" : ""}`,
      data,
      file_discovery: fileDiscoveryUpdate({
        observed: [
          observedFilePath(root, pathKind(rootStat)),
          ...matches.map(item => observedFilePath(item.path, "file")),
        ],
        discoveredRoots: [discoveryRoot],
      }),
    };
  };
  if (!matches.length) {
    const suffix = hasMore ? ` Search stopped at ${limitReason}; narrow the scope and retry.` : "";
    const payload = buildProjection();
    payload.content = `No matches for '${query}' under ${displayPath(root, cwd)}.${suffix}`;
    payload.preview = `0 matches${hasMore ? " (partial)" : ""}`;
    return boundedSearchResult(payload);
  }
  let payload = buildProjection();
  while (Buffer.byteLength(JSON.stringify(payload), "utf8") > SEARCH_RESULT_MAX_BYTES && matches.length > 1) {
    matches.pop();
    contentLines.pop();
    hasMore = true;
    limitReason = "byte_limit";
    payload = buildProjection();
  }
  return boundedSearchResult(payload);
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
            file_discovery: fileDiscoveryUpdate({ observed: [observedFilePath(target, "file")] }),
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
      file_discovery: fileDiscoveryUpdate({ observed: [observedFilePath(target, "file")] }),
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
      file_discovery: fileDiscoveryUpdate({ observed: [observedFilePath(target, "file")] }),
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
