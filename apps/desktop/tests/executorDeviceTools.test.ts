import { describe, expect, test } from "bun:test";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { readDeviceFile } = require("../electron/executor-tools.cjs");

describe("read_device_file handler", () => {
  test("reads a file with offset and limit", async () => {
    const dir = mkdtempSync(join(tmpdir(), "arden-executor-test-"));
    const file = join(dir, "sample.txt");
    writeFileSync(file, ["one", "two", "three", "four", "five"].join("\n"));

    const result = await readDeviceFile({ path: file, offset: 2, limit: 2 });
    expect(result.status).toBe("succeeded");
    expect(result.payload.content.startsWith("two\nthree")).toBe(true);
    expect(result.payload.content).toContain("2 more lines");
    expect(result.payload.data.total_lines).toBe(5);
    expect(result.payload.data.returned_lines).toBe(2);
  });

  test("missing file maps to not_found", async () => {
    const result = await readDeviceFile({ path: "/definitely/not/here.txt" });
    expect(result.status).toBe("failed");
    expect(result.errorCode).toBe("not_found");
  });

  test("relative path is rejected", async () => {
    const result = await readDeviceFile({ path: "relative/path.txt" });
    expect(result.status).toBe("failed");
    expect(result.errorCode).toBe("invalid_path");
  });
});

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { runBash } = require("../electron/executor-tools.cjs");

describe("bash handler", () => {
  test("runs a command and captures stdout", async () => {
    const result = await runBash({ command: "echo hello" });
    expect(result.status).toBe("succeeded");
    expect(result.payload.content).toContain("hello");
  });

  test("nonzero exit is a typed failure with stderr", async () => {
    const result = await runBash({ command: "echo err >&2; exit 7" });
    expect(result.status).toBe("failed");
    expect(result.errorCode).toBe("command_failed");
    expect(result.payload.content).toContain("[stderr]");
    expect(result.payload.content).toContain("[exit code: 7]");
    expect(result.payload.preview).toBe("Exit 7");
  });

  test("blocked command is refused without executing", async () => {
    const result = await runBash({ command: "rm -rf /" });
    expect(result.status).toBe("failed");
    expect(result.errorCode).toBe("permission_denied");
  });

  test("working_dir wins over context default_cwd", async () => {
    const result = await runBash({ command: "pwd", working_dir: "/tmp" }, { context: { default_cwd: "/" } });
    expect(result.status).toBe("succeeded");
    expect(result.payload.content).toContain("/tmp");
  });

  test("context default_cwd applies when no working_dir given", async () => {
    const result = await runBash({ command: "pwd" }, { context: { default_cwd: "/tmp" } });
    expect(result.payload.content).toContain("/tmp");
  });

  test("abort mid-command reports uncertain", async () => {
    const controller = new AbortController();
    const pending = runBash({ command: "sleep 5" }, { signal: controller.signal });
    setTimeout(() => controller.abort(), 50);
    const result = await pending;
    expect(result.status).toBe("uncertain");
    expect(result.errorCode).toBe("cancelled");
  });
});
