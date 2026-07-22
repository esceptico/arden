import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/geist";
import "@/lib/brandMigration";
import "@/styles.css";
import { App } from "@/app/App";
import { QuickCapture } from "@/app/QuickCapture";
import { IconContext } from "@/components/icons";

const root = document.querySelector<HTMLDivElement>("#app");
if (!root) throw new Error("Missing #app");

// The Electron main process opens a second, frameless BrowserWindow
// with `#quick-capture` in the URL hash to host the floating composer.
// Both windows load the same bundle; we switch which tree to mount.
const isQuickCapture = window.location.hash === "#quick-capture";

// Quick-capture's host BrowserWindow is frameless + transparent. Tag
// the body so CSS can clear the chrome (background, min-size) that
// the main window expects.
if (isQuickCapture) document.body.classList.add("quick-capture-mode");

createRoot(root).render(
  <StrictMode>
    <IconContext.Provider value={{ color: "currentColor", strokeWidth: 1.5 }}>
      {isQuickCapture ? <QuickCapture /> : <App />}
    </IconContext.Provider>
  </StrictMode>,
);
