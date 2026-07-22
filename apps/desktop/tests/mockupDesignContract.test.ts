import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

const design = read("../../../DESIGN.md");
const product = read("../../../PRODUCT.md");
const legacy = read("../../../docs/design-language.md");
const language = read("../../../docs/mockups/board-language.html");

test("DESIGN.md is the sole normative human-readable design contract", () => {
  expect(design).toContain("# NTRP desktop design system");
  expect(design).toContain("## Authority map");
  expect(design).toContain("board-surfaces.css");
  expect(design).toContain("board-system.css");
  expect(design).toContain("board-motion.js");
  expect(legacy).toContain("Historical design-language note");
  expect(legacy).toContain("../DESIGN.md");
  expect(legacy).not.toContain("This doc is the contract for new UI");
  expect(language).toContain("../../DESIGN.md");
  expect(language).not.toContain("Open decisions");
  expect(language).not.toContain("DARK MAPPING (proposal)");
  expect(language).not.toContain("specced, not yet plated");
});

test("product and design contracts agree on status and motion", () => {
  expect(product).not.toContain("status via the small dot/pip");
  expect(design).toContain("Status is expressed with a word first");
  expect(design).toContain("No bounce or elastic easing");
  expect(design).toContain("Do not animate width, height, padding, or margin");
  expect(design).toContain("default, hover, focus-visible, active, disabled, loading, and error");
});
