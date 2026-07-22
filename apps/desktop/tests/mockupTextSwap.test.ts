import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { Window } from "happy-dom";

const motionSource = readFileSync(
  new URL("../../../docs/mockups/board-motion.js", import.meta.url),
  "utf8",
);
const settingsSource = readFileSync(
  new URL("../../../docs/mockups/board-settings.html", import.meta.url),
  "utf8",
);
const settingsCompact = settingsSource.replace(/\s+/g, "");
const motionDemoSource = readFileSync(
  new URL("../../../docs/mockups/board-motion.html", import.meta.url),
  "utf8",
);
const memorySource = readFileSync(
  new URL("../../../docs/mockups/board-memory.html", import.meta.url),
  "utf8",
);
const chatHtmlSource = readFileSync(
  new URL("../../../docs/mockups/board-chat.html", import.meta.url),
  "utf8",
);
const chatScriptSource = readFileSync(
  new URL("../../../docs/mockups/board-chat.js", import.meta.url),
  "utf8",
);

function loadMotion(reduced = false) {
  const window = new Window();
  const context = {
    clearTimeout,
    document: window.document,
    getComputedStyle: window.getComputedStyle.bind(window),
    matchMedia: () => ({ matches: reduced }),
    requestAnimationFrame: (callback: FrameRequestCallback) => setTimeout(callback, 0),
    setTimeout,
  } as Record<string, unknown> & { window?: unknown; BOARD_MOTION?: any };
  context.window = context;
  runInNewContext(motionSource, context);
  return { document: window.document, motion: context.BOARD_MOTION };
}

test("shared mockup motion exposes the canonical text-swap tokens and CSS", () => {
  const { document, motion } = loadMotion();
  const styles = document.querySelector("#board-motion-primitives")?.textContent ?? "";

  expect(motion.duration.textSwap).toBe(150);
  expect(document.documentElement.style.getPropertyValue("--text-swap-dur")).toBe("150ms");
  expect(document.documentElement.style.getPropertyValue("--text-swap-translate-y")).toBe("4px");
  expect(document.documentElement.style.getPropertyValue("--text-swap-blur")).toBe("2px");
  expect(document.documentElement.style.getPropertyValue("--text-swap-ease")).toBe("ease-in-out");
  expect(styles).toContain(".t-text-swap.is-exit");
  expect(styles).toContain(".t-text-swap.is-enter-start");
  expect(styles).toContain(".t-text-swap { transition: none !important; }");
});

test("text swap exits the old label and the latest pending destination wins", async () => {
  const { document, motion } = loadMotion();
  const label = document.createElement("span");
  label.textContent = "Refresh";
  document.body.append(label);

  expect(motion.textSwap.swap(label, "Done")).toBe(true);
  expect(label.classList.contains("t-text-swap")).toBe(true);
  expect(label.classList.contains("is-exit")).toBe(true);
  expect(label.textContent).toBe("Refresh");

  expect(motion.textSwap.swap(label, "Ready")).toBe(true);
  await Bun.sleep(175);

  expect(label.textContent).toBe("Ready");
  expect(label.classList.contains("is-exit")).toBe(false);
  expect(label.classList.contains("is-enter-start")).toBe(false);
});

test("text swap updates immediately for reduced motion and explicit instant changes", () => {
  const reduced = loadMotion(true);
  const reducedLabel = reduced.document.createElement("span");
  reducedLabel.textContent = "Working";
  expect(reduced.motion.textSwap.swap(reducedLabel, "Worked")).toBe(true);
  expect(reducedLabel.textContent).toBe("Worked");
  expect(reducedLabel.classList.contains("is-exit")).toBe(false);

  const regular = loadMotion(false);
  const instantLabel = regular.document.createElement("span");
  instantLabel.textContent = "Copy";
  expect(regular.motion.textSwap.swap(instantLabel, "Copied", { animate: false })).toBe(true);
  expect(instantLabel.textContent).toBe("Copied");
  expect(instantLabel.classList.contains("is-exit")).toBe(false);
});

test("Settings and Motion share text swap for refresh acknowledgement", () => {
  expect(settingsSource).toContain('class="refresh-label t-text-swap">Refresh</span>');
  expect(settingsCompact).toContain('sharedMotion.textSwap.swap(text,"")');
  expect(settingsCompact).toContain('sharedMotion.textSwap.swap(text,"Done")');
  expect(settingsCompact).toContain('sharedMotion.textSwap.swap(text,"Refresh")');
  expect(settingsSource).not.toContain("text.textContent='Done'");
  expect(settingsSource).not.toContain("text.textContent='Refresh'");

  expect(motionDemoSource).toContain('class="refresh-demo-label t-text-swap">Refresh</span>');
  expect(motionDemoSource).toContain('M.textSwap.swap(refreshLabel,"")');
  expect(motionDemoSource).toContain('M.textSwap.swap(refreshLabel,"Done")');
  expect(motionDemoSource).toContain('M.textSwap.swap(refreshLabel,"Refresh")');
  expect(motionDemoSource).not.toContain('refreshLabel.textContent="Done"');
  expect(motionDemoSource).not.toContain('refreshLabel.textContent="Refresh"');
});

test("Memory edit and saved signatures use one shared text destination", () => {
  expect(memorySource).toContain("function setEditing(on,{statusText}={})");
  expect(memorySource).toContain("MOTION.textSwap.swap(sigState,nextStatus)");
  expect(memorySource).toContain('setEditing(false,{statusText:"edited just now"})');
  expect(memorySource).not.toContain('sigState.textContent = "edited just now"');
});

test("Chat swaps live run status only when the tool tail completes", () => {
  expect(chatHtmlSource).toMatch(/class="[^"]*\bdp-running-text\b[^"]*\brun-status\b[^"]*\bt-text-swap\b[^"]*"/);
  expect(chatScriptSource).toContain("motion.textSwap.swap(status, 'Working', { animate: false })");
  expect(chatScriptSource).toContain("status.classList.remove('shimmer')");
  expect(chatScriptSource).toContain("motion.textSwap.swap(status, 'Worked')");
  expect(chatScriptSource).toContain("completeLiveRunStatus();");
});

test("Chat tool tail keeps three rows and uses the shared smooth list motion", () => {
  const { motion } = loadMotion();

  expect(motion.limits.traceTail).toBe(3);
  expect(motion.distance.traceRow).toBe(4);
  expect(motion.blur.traceRow).toBe(2);
  expect(motionSource).toContain('filter: `blur(${blurRadius}px)`');
  expect(chatScriptSource).toContain("motion.textSwap.swap(suffix, active.dataset.finishedSuffix || suffix.textContent)");
  expect(chatScriptSource).not.toContain("suffix.textContent = active.dataset.finishedSuffix");
});

test("Chat approval actions expose pending and result labels before closing", () => {
  expect(chatHtmlSource).toContain('data-approval-action="deny"');
  expect(chatHtmlSource).toContain('data-approval-action="allow"');
  expect(chatHtmlSource).toContain('class="approval-action-label t-text-swap"');
  expect(chatScriptSource).toContain("const pending=allow?'Approving':'Denying'");
  expect(chatScriptSource).toContain("const result=allow?'Approved':'Denied'");
  expect(chatScriptSource).toContain("motion.textSwap.swap(label,pending)");
  expect(chatScriptSource).toContain("motion.textSwap.swap(label,result)");
  expect(chatScriptSource).not.toContain("button.addEventListener('click', () => setScene(state.previousScene))");
});

test("every Chat queue item exposes edit and cancel controls", () => {
  expect(chatScriptSource).toContain('class="queue-edit dp-icon-button"');
  expect(chatScriptSource).toContain('class="queue-cancel dp-icon-button"');
  expect(chatScriptSource).toContain("$('.queue-edit', card).addEventListener('click'");
  expect(chatScriptSource).toContain("$('.queue-cancel', card).addEventListener('click'");
  expect(chatScriptSource).toContain("editQueuedMessage(item.id)");
  expect(chatScriptSource).toContain("removeQueuedMessage(item.id)");
});
