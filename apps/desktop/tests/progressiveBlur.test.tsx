import { expect, test } from "bun:test";
import { createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ScrollBlurTop } from "@/components/ui/ScrollBlur";

test("chat scroll blur renders the canonical eight masked layers", () => {
  const html = renderToStaticMarkup(
    <ScrollBlurTop scrollerRef={createRef<HTMLElement>()} />,
  );

  expect((html.match(/data-progressive-blur-layer=/g) ?? []).length).toBe(8);
  expect(html).toContain("backdrop-filter:blur(1.75px)");
  expect(html).toContain("linear-gradient(0deg");
});
