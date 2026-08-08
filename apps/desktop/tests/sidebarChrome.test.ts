import { expect, test } from "bun:test";

test("sidebar interactive rows opt out of the Electron drag region", async () => {
  const css = await Bun.file(new URL("../src/design/shell.css", import.meta.url)).text();

  expect(css).toContain(".workspace-rail {");
  expect(css).toContain("-webkit-app-region: drag;");
  expect(css).toContain(".workspace-rail :is(button, input, [role=\"button\"], [role=\"separator\"])");
  expect(css).toContain("-webkit-app-region: no-drag;");
});

test("sidebar uses compact, grouped row rhythm", async () => {
  const css = await Bun.file(new URL("../src/design/shell.css", import.meta.url)).text();
  const sessionList = await Bun.file(
    new URL("../src/features/sessions/components/SessionList.tsx", import.meta.url),
  ).text();

  expect(css).toContain("--workspace-rail-nav-row-height: 1.875rem;");
  expect(css).toContain("--workspace-rail-group-row-height: 1.625rem;");
  expect(css).toContain("--workspace-rail-session-row-height: 1.75rem;");
  expect(css).toContain("--workspace-rail-sibling-gap: var(--space-0-5);");
  // Uniform rhythm (2026-08-09): one small consistent step between groups,
  // and the row radius rides the central settings-driven token.
  expect(css).toContain("--workspace-rail-group-gap: var(--space-2);");
  expect(css).toContain("--workspace-rail-row-radius: var(--r-row);");
  expect(css).toContain("--workspace-rail-icon-lane: 1.25rem;");
  expect(css).toContain("--workspace-rail-section-size: var(--text-label);");
  expect(css).toContain("--workspace-rail-label-size: var(--text-label);");
  expect(css).toContain("--workspace-rail-meta-size: var(--text-2xs);");
  expect(css).toContain("--workspace-rail-label-leading: var(--leading-label);");
  // Selection is a pronounced pill (7%); the section header floats its rule
  // with measured, even baseline pitch (2026-08-09 rail redesign).
  expect(css).toContain("--workspace-rail-selected-bg: color-mix(in oklab, var(--ink) 7%, transparent);");
  expect(css).toContain("height: 1.75rem;");
  expect(css).toContain("margin: var(--space-2) 0 var(--space-3);");
  expect(css).toContain("font-weight: var(--weight-medium);");
  expect(css).toContain(".workspace-rail__session-body {");
  expect(css).toContain(".workspace-rail__session-rest {");
  expect(sessionList).toContain('className="workspace-rail__session-group"');
  expect(sessionList).toContain('className="workspace-rail__session-body"');
  expect(sessionList).toContain('className="workspace-rail__session-rest"');
  expect(sessionList).not.toContain("workspace-rail__session-group px-2.5");

  const navRow = await Bun.file(
    new URL("../src/features/sessions/components/NavRow.tsx", import.meta.url),
  ).text();
  const sessionRow = await Bun.file(
    new URL("../src/features/sessions/components/SessionRow.tsx", import.meta.url),
  ).text();
  expect(navRow).not.toContain("tracking-[-0.005em]");
  expect(sessionRow).not.toContain("tracking-[-0.005em]");
  expect(sessionRow).toContain("var(--workspace-rail-icon-lane)");
  expect(css).not.toContain('[data-active="true"] .workspace-rail__session-title');
});
