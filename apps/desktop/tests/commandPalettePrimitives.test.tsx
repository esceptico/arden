import { afterEach, describe, expect, test } from "bun:test";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { PaletteBody } from "@/features/command-palette/components/PaletteBody";

let root: Root | null = null;

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  document.body.replaceChildren();
});

async function renderPalette(props: React.ComponentProps<typeof PaletteBody>) {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root?.render(<PaletteBody {...props} />));
  return host;
}

describe("command palette", () => {
  test("uses cmdk primitives for the root, input, list, groups, and items", async () => {
    const host = await renderPalette({
      query: "",
      setQuery: () => {},
      index: 0,
      setIndex: () => {},
      crumbs: [],
      setCrumbs: () => {},
      onClose: () => {},
    });

    expect(host.querySelector("[cmdk-root]")).not.toBeNull();
    expect(host.querySelector("[cmdk-input]")).not.toBeNull();
    expect(host.querySelector("[cmdk-list]")).not.toBeNull();
    expect(host.querySelector("[cmdk-group]")).not.toBeNull();
    expect(host.querySelector("[cmdk-item]")).not.toBeNull();
  });

  test("Cmd+Enter is not a submit affordance", async () => {
    let closes = 0;
    const host = await renderPalette({
      // A query no entry can match: Enter has nothing to activate, so anything
      // that closes the palette would have to be a separate submit path.
      query: "zzqq-no-entry-matches-this",
      setQuery: () => {},
      index: 0,
      setIndex: () => {},
      crumbs: [],
      setCrumbs: () => {},
      onClose: () => {
        closes += 1;
      },
    });

    const input = host.querySelector('[role="combobox"]');
    await act(async () =>
      input?.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", metaKey: true, bubbles: true })),
    );

    expect(closes).toBe(0);
    expect(host.querySelector(".command-palette__footer")?.textContent)
      .toBe("Enter opens the selected result");
    expect(host.textContent).not.toContain("⌘");
  });
});
