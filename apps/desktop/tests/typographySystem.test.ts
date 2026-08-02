import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const src = fileURLToPath(new URL("../src", import.meta.url));
const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

test("authored UI typography stays on the semantic ladder", () => {
  const forbidden = [
    /\bfont-\[\d/,
    /\btext-\[\d+(?:\.\d+)?px\]/,
    /\btracking-\[[-.\d]+(?:em)?\]/,
    /\bleading-\[\d+(?:\.\d+)?\]/,
  ];

  for (const path of new Bun.Glob("**/*.tsx").scanSync({ cwd: src })) {
    const source = readFileSync(`${src}/${path}`, "utf8");
    for (const pattern of forbidden) expect(source, path).not.toMatch(pattern);
  }

  for (const path of new Bun.Glob("**/*.css").scanSync({ cwd: src })) {
    const source = readFileSync(`${src}/${path}`, "utf8");
    expect(source, path).not.toMatch(/letter-spacing:\s*[-.\d]/);
  }
});

test("primary workspaces share title, rail, and prose roles", () => {
  const base = read("../src/design/base.css");
  const settings = read("../src/design/settings.css");
  const automations = read("../src/features/automations/components/automations-workspace.css");
  const memory = read("../src/design/memory.css");
  const area = read("../src/design/area.css");
  const home = read("../src/features/home/components/Home.css");

  for (const role of [
    "--type-page-title:",
    "--type-editorial-title:",
    "--type-section-label:",
    "--type-rail-group:",
    "--type-rail-item:",
    "--type-data:",
  ]) expect(base).toContain(role);

  for (const surface of [settings, automations, memory]) {
    expect(surface).toContain("font: var(--type-page-title)");
    expect(surface).toContain("font: var(--type-rail-group)");
    expect(surface).toContain("font: var(--type-rail-item)");
  }
  for (const surface of [home, area]) expect(surface).toContain("font: var(--type-editorial-title)");
  expect(memory).toContain("font-size: var(--reading-h1-size)");
  expect(memory).toContain("line-height: var(--leading-reading)");
});

test("the chat composer matches conversation body typography", () => {
  const composer = read("../src/features/chat/components/Composer.tsx");
  const chat = read("../src/design/chat.css");
  expect(composer).toContain("text-[var(--text-body)] leading-[var(--leading-body)] text-ink outline-none");
  expect(composer).not.toContain("text-md leading-[var(--leading-reading)]");
  expect(chat).toMatch(/\.board-composer__input\s*\{[^}]*font-size:\s*var\(--text-body\);[^}]*line-height:\s*var\(--leading-body\);/s);
});
