import { GlobalRegistrator } from "@happy-dom/global-registrator";

// Single global test DOM for the whole bun test suite. Replaces the per-file
// JSDOM realms + hand-rolled polyfills that the suite used to carry.
GlobalRegistrator.register({ url: "http://localhost" });

// Happy DOM creates a real <!doctype html> but does not currently expose the
// corresponding read-only `Document.compatMode` value. Browser libraries such
// as KaTeX read it during module initialization, so make the shared test DOM
// accurately report standards mode before any test modules are imported.
if (document.compatMode === undefined) {
  Object.defineProperty(document, "compatMode", {
    configurable: true,
    value: "CSS1Compat",
  });
}

// React's act() environment flag — every interaction test renders under act.
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT ??= true;

// Happy DOM's localStorage is one store shared by every test file in a bun
// invocation — leftover keys from one file can mask deterministic failures
// in another (a suite once passed only because an earlier file left a value
// behind). Reset per test so isolation matches a fresh browser profile.
import { beforeEach } from "bun:test";
beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});
