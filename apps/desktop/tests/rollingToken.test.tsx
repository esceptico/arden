import { afterEach, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { RollingToken } from "@/components/ui/RollingToken";

const roots = new Set<Root>();

function mount() {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  roots.add(root);
  return { host, root };
}

afterEach(async () => {
  for (const root of roots) await act(async () => root.unmount());
  roots.clear();
  document.body.replaceChildren();
});

test("letter cells take natural width; only digit slots get the fixed counter width", async () => {
  const { host, root } = mount();
  await act(async () => root.render(<RollingToken value="3 calls" />));

  const cells = [...host.querySelectorAll<HTMLElement>(".t-spin-cell")];
  expect(cells.length).toBe(7);

  const digitCells = cells.filter((cell) => /\d/.test(cell.textContent ?? ""));
  const letterCells = cells.filter((cell) => /[a-z]/.test(cell.textContent ?? ""));
  expect(digitCells.length).toBe(1);
  expect(letterCells.length).toBe(5);
  for (const cell of digitCells) expect(cell.className).toContain("min-w-");
  for (const cell of letterCells) expect(cell.className).not.toContain("min-w-");
});
