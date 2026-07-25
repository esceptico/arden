import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const source = readFileSync(
  new URL("../src/features/settings/components/ProviderRow.tsx", import.meta.url),
  "utf8",
);

test("env-managed providers reuse the canonical mockup button geometry", () => {
  expect(source).toContain('<span className="btn arden-button" data-variant="secondary">');
  expect(source).not.toContain("rounded-md border border-line-soft bg-surface-soft");
});
