import { expect, test } from "bun:test";
import {
  buildSrcdoc,
  snapshotThemeVars,
  WIDGET_BASE_CSS,
  WIDGET_BRIDGE_SCRIPT,
  WIDGET_CSP_META,
  WIDGET_SANDBOX,
} from "@/features/chat/lib/srcdoc";

test("buildSrcdoc assembles CSP, theme :root, bridge, and body", () => {
  const doc = buildSrcdoc("<p>x</p>", "--color-ink:#111");

  expect(doc).toContain(WIDGET_CSP_META);
  expect(doc).toContain(
    `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; form-action 'none'">`,
  );
  expect(doc).toContain(":root{--color-ink:#111}");
  expect(doc).toContain("window.arden");
  expect(doc).toContain("ui/submit");
  expect(doc).toContain("ui/cancel");
  expect(doc).toContain("ui/size-changed");
  expect(doc).toContain("<body><p>x</p></body>");
  expect(doc).toContain(WIDGET_BRIDGE_SCRIPT);
});

test("base style pack ships in every srcdoc with the documented classes", () => {
  const doc = buildSrcdoc("<p>x</p>", "--color-ink:#111");

  expect(doc).toContain(WIDGET_BASE_CSS);
  for (const cls of [".field", ".grid-2", ".chip", ".actions", ".muted", "button.primary"]) {
    expect(WIDGET_BASE_CSS).toContain(cls);
  }
});

test("submitForm collects FormData and groups same-name fields into arrays", () => {
  expect(WIDGET_BRIDGE_SCRIPT).toContain("submitForm");
  expect(WIDGET_BRIDGE_SCRIPT).toContain("new FormData(form)");
  expect(WIDGET_BRIDGE_SCRIPT).toContain("Array.isArray");
});

test("sandbox is exactly allow-scripts allow-forms", () => {
  expect(WIDGET_SANDBOX).toBe("allow-scripts allow-forms");
  expect(WIDGET_SANDBOX).not.toContain("allow-same-origin");
});

test("CSP forbids form-action (allow-forms must not become an exfil channel)", () => {
  expect(WIDGET_CSP_META).toContain("form-action 'none'");
});

test("bridge swallows anchor navigation out of the sandboxed frame", () => {
  expect(WIDGET_BRIDGE_SCRIPT).toContain('closest("a[href]")');
  expect(WIDGET_BRIDGE_SCRIPT).toContain("event.preventDefault()");
});

test("snapshotThemeVars emits set custom properties and skips empties", () => {
  const root = document.createElement("div");
  document.body.append(root);
  try {
    root.style.setProperty("--color-ink", "#1a1a1a");
    root.style.setProperty("--font-sans", "Inter, sans-serif");

    const vars = snapshotThemeVars(root);

    expect(vars).toContain("--color-ink:#1a1a1a");
    expect(vars).toContain("--font-sans:Inter, sans-serif");
    expect(vars).not.toContain("--color-accent:");
    expect(vars.split(";").every((decl) => !decl.endsWith(":"))).toBe(true);
  } finally {
    root.remove();
  }
});
