import { afterEach, describe, expect, mock, test } from "bun:test";
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

describe("command palette agent shortcut", () => {
  test("uses cmdk primitives for the root, input, list, groups, and items", async () => {
    const host = await renderPalette(
      {
        query: "",
        setQuery: () => {},
        index: 0,
        setIndex: () => {},
        crumbs: [],
        setCrumbs: () => {},
        onClose: () => {},
        onAgentSubmit: () => {},
      } as React.ComponentProps<typeof PaletteBody>,
    );

    expect(host.querySelector("[cmdk-root]")).not.toBeNull();
    expect(host.querySelector("[cmdk-input]")).not.toBeNull();
    expect(host.querySelector("[cmdk-list]")).not.toBeNull();
    expect(host.querySelector("[cmdk-group]")).not.toBeNull();
    expect(host.querySelector("[cmdk-item]")).not.toBeNull();
  });

  test("Cmd+Enter submits the raw query even when deterministic results are empty", () => {
    const submit = mock(() => {});
    const close = mock(() => {});
    const setQuery = mock(() => {});

    return renderPalette(
      {
        query: "go to email automation",
        setQuery,
        index: 0,
        setIndex: () => {},
        crumbs: [],
        setCrumbs: () => {},
        onClose: close,
        onAgentSubmit: submit,
      } as React.ComponentProps<typeof PaletteBody>,
    ).then(async (host) => {
      const input = host.querySelector('[role="combobox"]');
      await act(async () => input?.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", metaKey: true, bubbles: true })));

      expect(submit).toHaveBeenCalledWith("go to email automation");
      expect(close).toHaveBeenCalledTimes(1);
    });
  });

  test("plain Enter does not submit to the agent", () => {
    const submit = mock(() => {});

    return renderPalette(
      {
        query: "go to email automation",
        setQuery: () => {},
        index: 0,
        setIndex: () => {},
        crumbs: [],
        setCrumbs: () => {},
        onClose: () => {},
        onAgentSubmit: submit,
      } as React.ComponentProps<typeof PaletteBody>,
    ).then(async (host) => {
      const input = host.querySelector('[role="combobox"]');
      await act(async () => input?.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
      expect(submit).not.toHaveBeenCalled();
    });
  });
});
