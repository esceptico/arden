import { describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { runBash, readFile, listFiles, findFiles, searchText, writeFile, editFile } = require("../electron/executor-tools.cjs");

function tempDir(): string {
  return mkdtempSync(join(tmpdir(), "arden-executor-test-"));
}

describe("read_file handler", () => {
  test("reads with pagination header and numbered lines", async () => {
    const dir = tempDir();
    const file = join(dir, "sample.txt");
    writeFileSync(file, ["one", "two", "three", "four", "five"].join("\n"));

    const result = await readFile({ path: file, offset: 2, limit: 2 });
    expect(result.status).toBe("succeeded");
    expect(result.payload.content).toBe("[5 lines, showing 2-3]\n     2|two\n     3|three");
    expect(result.payload.data.size).toBe(23);
    expect(result.payload.source_refs[0]).toEqual({ provider: "filesystem", kind: "file", ref: file, title: "sample.txt" });
  });

  test("full read reports a content_read observation with the file sha", async () => {
    const dir = tempDir();
    const file = join(dir, "sample.txt");
    writeFileSync(file, "hello\n");

    const result = await readFile({ path: file });
    const observation = result.payload.observations[0];
    expect(observation.id).toBe(`file:${file}`);
    expect(observation.version).toHaveLength(64);
    expect(observation.content_read).toBe(true);
  });

  test("partial read is not content_read", async () => {
    const dir = tempDir();
    const file = join(dir, "sample.txt");
    writeFileSync(file, ["a", "b", "c"].join("\n"));

    const result = await readFile({ path: file, offset: 1, limit: 2 });
    expect(result.payload.observations[0].content_read).toBe(false);
  });

  test("offloaded results are capped and carry no source refs", async () => {
    const dir = tempDir();
    const offloadDir = `${join(dir, "results")}/`;
    mkdirSync(offloadDir, { recursive: true });
    const file = join(offloadDir, "result.txt");
    writeFileSync(file, Array.from({ length: 300 }, (_, i) => `line ${i + 1}`).join("\n"));

    const result = await readFile({ path: file }, { context: { offload_dir: offloadDir } });
    expect(result.payload.content).toContain("[300 lines, showing 1-100]");
    expect(result.payload.source_refs).toBeUndefined();
  });

  test("missing file and directory are typed failures", async () => {
    const dir = tempDir();
    expect((await readFile({ path: join(dir, "nope.txt") })).errorCode).toBe("not_found");
    expect((await readFile({ path: dir })).errorCode).toBe("invalid_ref");
  });

  test("relative path resolves against context default_cwd", async () => {
    const dir = tempDir();
    writeFileSync(join(dir, "notes.txt"), "hi\n");

    const result = await readFile({ path: "notes.txt" }, { context: { default_cwd: dir } });
    expect(result.status).toBe("succeeded");
    expect(result.payload.data.path).toBe("notes.txt");
    expect(result.payload.data.absolute_path).toContain(dir);
  });
});

describe("list_files handler", () => {
  test("lists entries dirs-first with metadata", async () => {
    const dir = tempDir();
    writeFileSync(join(dir, "b.txt"), "x");
    mkdirSync(join(dir, "a-dir"));
    writeFileSync(join(dir, ".hidden"), "x");

    const result = await listFiles({ path: dir });
    expect(result.status).toBe("succeeded");
    expect(result.payload.preview).toBe("2 entries");
    expect(result.payload.data.entries[0].name).toBe("a-dir/");
    expect(result.payload.data.entries[0].kind).toBe("directory");
    expect(result.payload.data.entries[1].name).toBe("b.txt");
    expect(result.payload.content).toContain(`${dir} (2 entries)`);
  });

  test("cursor pages are deterministic", async () => {
    const dir = tempDir();
    for (const name of ["a.txt", "b.txt", "c.txt"]) writeFileSync(join(dir, name), "x");

    const first = await listFiles({ path: dir, limit: 2 });
    expect(first.payload.data.has_more).toBe(true);
    const second = await listFiles({ path: dir, limit: 2, cursor: first.payload.data.next_cursor });
    expect(second.payload.data.entries.map((e: { name: string }) => e.name)).toEqual(["c.txt"]);
    expect(second.payload.data.has_more).toBe(false);
  });

  test("invalid cursor is a typed failure", async () => {
    const dir = tempDir();
    const result = await listFiles({ path: dir, cursor: "garbage!" });
    expect(result.errorCode).toBe("invalid_ref");
  });
});

describe("find_files handler", () => {
  test("matches a glob recursively", async () => {
    const dir = tempDir();
    mkdirSync(join(dir, "sub"));
    writeFileSync(join(dir, "a.py"), "x");
    writeFileSync(join(dir, "sub", "b.py"), "x");
    writeFileSync(join(dir, "c.txt"), "x");

    const result = await findFiles({ path: dir, pattern: "**/*.py" });
    expect(result.status).toBe("succeeded");
    const relative = result.payload.data.matches.map((m: { relative_path: string }) => m.relative_path);
    expect(relative.sort()).toEqual(["a.py", "sub/b.py"]);
  });

  test("caps results and discloses more", async () => {
    const dir = tempDir();
    for (let i = 0; i < 5; i += 1) writeFileSync(join(dir, `f${i}.txt`), "x");

    const result = await findFiles({ path: dir, pattern: "*.txt", limit: 2 });
    expect(result.payload.data.matches).toHaveLength(2);
    expect(result.payload.data.has_more).toBe(true);
    expect(result.payload.preview).toContain("(capped)");
  });
});

describe("search_text handler", () => {
  test("returns line and column matches", async () => {
    const dir = tempDir();
    writeFileSync(join(dir, "code.py"), "def alpha():\n    return alpha\n");

    const result = await searchText({ query: "alpha", path: dir });
    expect(result.status).toBe("succeeded");
    expect(result.payload.data.matches).toHaveLength(2);
    expect(result.payload.data.matches[0]).toMatchObject({ relative_path: "code.py", line: 1, column: 5 });
  });

  test("no matches is a clean success", async () => {
    const dir = tempDir();
    writeFileSync(join(dir, "code.py"), "nothing here\n");

    const result = await searchText({ query: "absent-token", path: dir });
    expect(result.status).toBe("succeeded");
    expect(result.payload.preview).toBe("0 matches");
  });
});

describe("write_file handler", () => {
  test("creates a new file without a prior read", async () => {
    const dir = tempDir();
    const file = join(dir, "new.txt");

    const result = await writeFile({ path: file, content: "hello\nworld\n" });
    expect(result.status).toBe("succeeded");
    expect(readFileSync(file, "utf8")).toBe("hello\nworld\n");
    expect(result.payload.effect).toEqual({ operation: "create", target: file });
    expect(result.payload.observations[0].content_read).toBe(true);
  });

  test("replacing requires a fresh complete read", async () => {
    const dir = tempDir();
    const file = join(dir, "note.txt");
    writeFileSync(file, "old\n");

    const result = await writeFile({ path: file, content: "new\n" });
    expect(result.errorCode).toBe("fresh_read_required");
    expect(readFileSync(file, "utf8")).toBe("old\n");
  });

  test("replaces after an observed read; conflicts when the file moved on", async () => {
    const dir = tempDir();
    const file = join(dir, "note.txt");
    writeFileSync(file, "version A\n");
    const read = await readFile({ path: file });
    const observation = read.payload.observations[0];
    const context = { resource_observations: { [observation.id]: observation } };

    const replaced = await writeFile({ path: file, content: "version B\n" }, { context });
    expect(replaced.status).toBe("succeeded");
    expect(replaced.payload.effect.operation).toBe("replace");
    expect(readFileSync(file, "utf8")).toBe("version B\n");

    // Same (now stale) observation: the disk changed since that read.
    const conflicted = await writeFile({ path: file, content: "version C\n" }, { context });
    expect(conflicted.errorCode).toBe("write_conflict");
    expect(readFileSync(file, "utf8")).toBe("version B\n");
  });

  test("identical content short-circuits as unchanged", async () => {
    const dir = tempDir();
    const file = join(dir, "note.txt");
    writeFileSync(file, "same\n");

    const result = await writeFile({ path: file, content: "same\n" });
    expect(result.status).toBe("succeeded");
    expect(result.payload.preview).toBe("File unchanged");
  });

  test("missing parent is a typed failure", async () => {
    const dir = tempDir();
    const result = await writeFile({ path: join(dir, "missing", "x.txt"), content: "x" });
    expect(result.errorCode).toBe("not_found");
  });
});

describe("edit_file handler", () => {
  test("replaces one exact block and reports the new revision", async () => {
    const dir = tempDir();
    const file = join(dir, "note.txt");
    writeFileSync(file, "hello old world\n");

    const result = await editFile({ path: file, old_text: "old", new_text: "new" });
    expect(result.status).toBe("succeeded");
    expect(readFileSync(file, "utf8")).toBe("hello new world\n");
    expect(result.payload.effect).toEqual({ operation: "edit", target: file });
    expect(result.payload.observations[0].version).toHaveLength(64);
  });

  test("dollar patterns in new_text are inserted literally", async () => {
    const dir = tempDir();
    const file = join(dir, "note.txt");
    writeFileSync(file, "price: OLD\n");

    const result = await editFile({ path: file, old_text: "OLD", new_text: "$& = $100" });
    expect(result.status).toBe("succeeded");
    expect(readFileSync(file, "utf8")).toBe("price: $& = $100\n");
  });

  test("ambiguous and missing blocks are typed failures", async () => {
    const dir = tempDir();
    const file = join(dir, "note.txt");
    writeFileSync(file, "same\nsame\n");

    const ambiguous = await editFile({ path: file, old_text: "same", new_text: "x" });
    expect(ambiguous.errorCode).toBe("invalid_ref");
    expect(ambiguous.payload.content).toContain("matched 2 times");

    const missing = await editFile({ path: file, old_text: "absent", new_text: "x" });
    expect(missing.errorCode).toBe("not_found");
    expect(readFileSync(file, "utf8")).toBe("same\nsame\n");
  });
});

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
