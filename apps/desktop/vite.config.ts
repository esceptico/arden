import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import preload from "vite-plugin-preload";

export default defineConfig({
  root: "src",
  // Vite 8's native (rolldown/notify) file watcher is silently dead on this
  // macOS (Darwin 27): edits were never re-transformed under node OR bun, so
  // HMR and even fresh page loads served stale modules until a restart —
  // the same rot behind the "@import'd CSS needs a restart" quirk. Polling
  // is the reliable fallback; scope is just src/, so the cost is negligible.
  // Revisit when a vite/rolldown release fixes fsevents on this OS.
  server: { watch: { usePolling: true, interval: 300 } },
  resolve: {
    // `@/` → apps/desktop/src (bulletproof-react absolute-import convention).
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  // Relative asset paths. Electron loads the packaged index.html via the
  // `file://` protocol, where the default absolute base (`/assets/...`)
  // resolves to the filesystem root instead of the app bundle —
  // resulting in a fully blank window on launch. `./` makes the script
  // and stylesheet refs relative to the html file location so they
  // resolve correctly inside the asar.
  base: "./",
  // `preload()` emits <link rel="modulepreload"> tags for every async
  // chunk produced by React.lazy + Vite's code-split. Without it, the
  // first time the user opens (say) the Settings modal the renderer has
  // to round-trip-load the chunk before mount; with it, the chunks are
  // already warmed in browser/Electron's module cache when the import()
  // resolves. Negligible cold-start cost vs. instant modal-open.
  plugins: [
    // React Compiler 1.0 — auto-memoizes components + hooks so we don't
    // have to scatter useMemo / useCallback / React.memo. Meta reports
    // significant load + interaction wins from enabling it. Compiler is
    // safe-by-default: it skips compilation for anything it can't prove
    // pure (e.g. components that mutate Maps/Sets in render), the rest
    // of the tree still benefits.
    react({ babel: { plugins: [["babel-plugin-react-compiler"]] } }),
    tailwindcss(),
    preload(),
  ],
  build: {
    outDir: "../dist/renderer",
    emptyOutDir: true,
  },
});
