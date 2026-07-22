import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Window } from "happy-dom";
import { ScrollBlurTop } from "@/components/ui/ScrollBlur";

test("chat scroll blur renders the canonical eight masked layers", () => {
  const html = renderToStaticMarkup(
    <ScrollBlurTop scrollerRef={createRef<HTMLElement>()} />,
  );

  expect((html.match(/data-progressive-blur-layer=/g) ?? []).length).toBe(8);
  expect(html).toContain("backdrop-filter:blur(1.75px)");
  expect(html).toContain("linear-gradient(0deg");
});

test("static mockups mount the same eight-layer progressive blur field", () => {
  const window = new Window();
  const source = readFileSync(
    new URL("../../../docs/mockups/board-motion.js", import.meta.url),
    "utf8",
  );
  const context = {
    document: window.document,
    matchMedia: () => ({ matches: false }),
    getComputedStyle: window.getComputedStyle.bind(window),
  } as Record<string, unknown> & { window?: unknown; BOARD_MOTION?: any };
  context.window = context;
  runInNewContext(source, context);

  const host = window.document.createElement("div");
  window.document.body.append(host);
  context.BOARD_MOTION.progressiveBlur.mount(host, { direction: "top" });

  expect(host.children).toHaveLength(8);
  expect((host.children[0] as HTMLElement).style.maskImage).toContain(
    "linear-gradient(0deg",
  );
  expect((host.children[7] as HTMLElement).style.backdropFilter).toBe(
    "blur(1.75px)",
  );
});
