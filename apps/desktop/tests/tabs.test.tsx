import { expect, test } from "bun:test";
import React, { StrictMode } from "react";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { Tab, Tabs } from "@/components/ui/Tabs";
import { SLIDE_PAGE_VARIANTS } from "@/components/ui/TabPanels";

test("renders a button per tab and marks the active one", () => {
  const html = renderToStaticMarkup(
    <Tabs value="b" onChange={() => {}} variant="underline">
      <Tab value="a">Alpha</Tab>
      <Tab value="b">Beta</Tab>
    </Tabs>,
  );
  expect(html).toContain("Alpha");
  expect(html).toContain("Beta");
  expect(html).toContain('role="tablist"');
  expect((html.match(/role="tab"/g) ?? []).length).toBe(2);
  expect(html).toContain('aria-selected="true"');
});

test("underline variant renders exactly one underline indicator", () => {
  const html = renderToStaticMarkup(
    <Tabs value="a" onChange={() => {}} variant="underline">
      <Tab value="a">Alpha</Tab>
      <Tab value="b">Beta</Tab>
    </Tabs>,
  );
  expect((html.match(/data-tab-indicator/g) ?? []).length).toBe(1);
  expect(html).toContain('data-tab-indicator="underline"');
});

test("segmented variant renders the track, pill indicator, and item sizing", () => {
  const html = renderToStaticMarkup(
    <Tabs
      value="a"
      onChange={() => {}}
      variant="segmented"
      size="sm"
      indicatorClassName="indicator-probe"
    >
      <Tab value="a">Alpha</Tab>
    </Tabs>,
  );
  expect(html).toContain('data-tab-indicator="segmented"');
  expect(html).toContain("indicator-probe");
  expect(html).toContain("segmented-control");
  expect(html).toContain("arden-segmented-tab");
  const foundation = readFileSync(new URL("../src/design/foundation.css", import.meta.url), "utf8");
  expect(foundation).toContain("min-height: var(--tab-control-size)");
  expect(foundation).toContain("padding-inline: var(--tab-inline-padding)");
});

test("expanding tabs keep one list-owned indicator while button layout commits", () => {
  const html = renderToStaticMarkup(
    <Tabs value="approve" onChange={() => {}} variant="expanding">
      <Tab value="approve">Approve</Tab>
      <Tab value="ask">Ask</Tab>
    </Tabs>,
  );
  expect(html).toContain('data-tab-indicator="expanding"');
  expect(html).toContain('data-tabs-expanding="true"');
  expect(html).toContain("data-[active]:flex-none");
  expect((html.match(/data-tab-indicator="expanding"/g) ?? []).length).toBe(1);
});

test("page panel variant matches settings-style directional swaps", () => {
  expect(SLIDE_PAGE_VARIANTS.exit(-1)).toMatchObject({ x: 8 });
  expect(SLIDE_PAGE_VARIANTS.enter(-1)).toMatchObject({ x: -8 });
  expect(SLIDE_PAGE_VARIANTS.exit(1)).toMatchObject({ x: -8 });
  expect(SLIDE_PAGE_VARIANTS.enter(1)).toMatchObject({ x: 8 });
});

test("shared tabs measure geometry directly and animate only indicator transform", () => {
  const tabs = readFileSync(new URL("../src/components/ui/Tabs.tsx", import.meta.url), "utf8");
  const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

  expect(tabs).toContain("getBoundingClientRect()");
  expect(tabs).toContain("new ResizeObserver(measure)");
  expect(tabs).toContain("transform: `translate(${geometry.x}px, ${geometry.y}px)`");
  expect(tabs).not.toContain("layoutId");
  expect(tabs).not.toContain("<motion.");
  expect(styles).toContain(".arden-tab-indicator {");
  expect(styles).toContain("transition: transform var(--tabs-dur) var(--tabs-ease);");
});

test("StrictMode cleanup cannot strand the shared indicator without motion", async () => {
  const originalRects = HTMLElement.prototype.getClientRects;
  const originalRect = HTMLElement.prototype.getBoundingClientRect;
  const originalRaf = window.requestAnimationFrame;
  const originalCancelRaf = window.cancelAnimationFrame;
  const frames = new Map<number, FrameRequestCallback>();
  let frameId = 0;
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);

  HTMLElement.prototype.getClientRects = function getClientRects() {
    return [{ left: 0 }] as unknown as DOMRectList;
  };
  HTMLElement.prototype.getBoundingClientRect = function getBoundingClientRect() {
    const value = this.getAttribute("data-tab-value");
    const left = value === "b" ? 80 : 0;
    const width = this.getAttribute("role") === "tablist" ? 160 : 80;
    const height = this.getAttribute("role") === "tablist" ? 34 : 28;
    return {
      x: left,
      y: 0,
      left,
      top: 0,
      right: left + width,
      bottom: height,
      width,
      height,
      toJSON: () => ({}),
    };
  };
  window.requestAnimationFrame = (callback) => {
    const id = ++frameId;
    frames.set(id, callback);
    return id;
  };
  window.cancelAnimationFrame = (id) => {
    frames.delete(id);
  };

  try {
    await act(async () => root.render(
      <StrictMode>
        <Tabs value="a" onChange={() => {}} variant="surface">
          <Tab value="a">Alpha</Tab>
          <Tab value="b">Beta</Tab>
        </Tabs>
      </StrictMode>,
    ));
    expect(frames.size).toBe(1);
    await act(async () => {
      for (const [id, callback] of [...frames]) {
        frames.delete(id);
        callback(performance.now());
      }
    });
    const indicator = host.querySelector<HTMLElement>(".arden-tab-indicator");
    expect(indicator?.dataset.ready).toBe("true");
    expect(indicator?.style.transition).not.toBe("none");
  } finally {
    await act(async () => root.unmount());
    host.remove();
    HTMLElement.prototype.getClientRects = originalRects;
    HTMLElement.prototype.getBoundingClientRect = originalRect;
    window.requestAnimationFrame = originalRaf;
    window.cancelAnimationFrame = originalCancelRaf;
  }
});
