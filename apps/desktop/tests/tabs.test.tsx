import { expect, test } from "bun:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
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
  expect(html).toContain("h-7");
});

test("expanding tabs mount the shared pill inside the active content", () => {
  const html = renderToStaticMarkup(
    <Tabs value="approve" onChange={() => {}} variant="expanding">
      <Tab value="approve">Approve</Tab>
      <Tab value="ask">Ask</Tab>
    </Tabs>,
  );
  expect(html).toContain('data-tab-indicator="expanding"');
  expect(html).toContain("data-[active]:flex-none");
  expect((html.match(/data-tab-indicator="expanding"/g) ?? []).length).toBe(1);
});

test("page panel variant matches settings-style directional swaps", () => {
  expect(SLIDE_PAGE_VARIANTS.exit(-1)).toMatchObject({ x: 16 });
  expect(SLIDE_PAGE_VARIANTS.enter(-1)).toMatchObject({ x: -16 });
  expect(SLIDE_PAGE_VARIANTS.exit(1)).toMatchObject({ x: -16 });
  expect(SLIDE_PAGE_VARIANTS.enter(1)).toMatchObject({ x: 16 });
});
