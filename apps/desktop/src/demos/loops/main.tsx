import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/geist";
import { LoopInstrumentDemo } from "./LoopInstrumentDemo";

const root = document.querySelector<HTMLDivElement>("#app");
if (!root) throw new Error("Missing #app");

createRoot(root).render(
  <StrictMode>
    <LoopInstrumentDemo />
  </StrictMode>,
);
