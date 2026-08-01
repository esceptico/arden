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
