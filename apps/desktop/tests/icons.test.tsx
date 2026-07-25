import { expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { Copy, House, PanelRightOpen } from "../src/components/icons";

test("desktop semantic exports render Hugeicons", () => {
  const markup = renderToStaticMarkup(
    <>
      <Copy aria-label="copy" />
      <House aria-label="home" />
      <PanelRightOpen aria-label="open right panel" />
    </>,
  );

  expect((markup.match(/viewBox="0 0 24 24"/g) ?? []).length).toBe(3);
  expect((markup.match(/fill="none"/g) ?? []).length).toBe(3);
});
