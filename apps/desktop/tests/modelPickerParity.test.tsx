import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { act, useRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  ModelReasoningPicker,
  ModelMenuPicker,
  availableModelChoices,
  configuredModelChoices,
  reasoningEffortLabel,
} from "@/components/ui/ModelPickers";
import type { ModelGroup } from "@/api/types";
import { useOverlayLayer } from "@/lib/overlayStack";

const GROUPS: ModelGroup[] = [
  {
    provider: "anthropic",
    label: "Anthropic",
    models: ["claude-opus", "claude-sonnet"],
  },
  {
    provider: "openai-codex",
    label: "OpenAI Codex",
    models: ["openai-codex/gpt-sol", "openai-codex/gpt-terra"],
  },
];

function mount(): { el: HTMLElement; root: Root; restore: () => void } {
  const el = document.createElement("div");
  document.body.append(el);
  return { el, root: createRoot(el), restore: () => el.remove() };
}

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

test("model choices lead with the current model and survive an absent catalog", () => {
  // Quick choices used to be bounded by the configured role models. They are
  // the live catalog now, with the current model pinned first so a session
  // override stays selectable even when it is not in the list.
  expect(availableModelChoices("openai-codex/gpt-terra", ["claude-opus", "claude-sonnet"])).toEqual([
    "openai-codex/gpt-terra",
    "claude-opus",
    "claude-sonnet",
  ]);
  // A model already in the catalog is not repeated.
  expect(availableModelChoices("claude-opus", ["claude-opus", "claude-sonnet"])).toEqual([
    "claude-opus",
    "claude-sonnet",
  ]);
  // No catalog yet: the current model alone keeps the picker usable.
  expect(availableModelChoices("openai-codex/gpt-sol", null)).toEqual(["openai-codex/gpt-sol"]);
  expect(availableModelChoices(null, ["claude-opus"])).toEqual(["claude-opus"]);
});

test("configured models stand in for a catalog that never loaded", () => {
  // /models is fetched with .catch(() => null) and never retried, so one failed
  // load leaves every picker empty for the session unless something known-good
  // can be offered without it.
  const config = {
    chat_model: "openai-codex/gpt-sol",
    model_roles: {
      research: { model: "claude-opus", reasoning_effort: null },
      workflow: { model: "claude-opus", reasoning_effort: "high" },
      memory: { model: "claude-sonnet", reasoning_effort: null },
    },
  } as unknown as Parameters<typeof configuredModelChoices>[0];

  expect(configuredModelChoices(config)).toEqual([
    "openai-codex/gpt-sol",
    "claude-opus",
    "claude-opus",
    "claude-sonnet",
  ]);
  // Deduped, and the session's own model still leads.
  expect(availableModelChoices("claude-sonnet", configuredModelChoices(config))).toEqual([
    "claude-sonnet",
    "openai-codex/gpt-sol",
    "claude-opus",
  ]);
  expect(configuredModelChoices(null)).toEqual([]);
  // A role with no model set contributes nothing rather than a blank entry.
  expect(configuredModelChoices({
    chat_model: "claude-opus",
    model_roles: { research: { model: null, reasoning_effort: null } },
  } as unknown as Parameters<typeof configuredModelChoices>[0])).toEqual(["claude-opus"]);
});

test("reasoning labels preserve wire values without exposing implementation spelling", () => {
  expect(reasoningEffortLabel(null)).toBe("Model default");
  expect(reasoningEffortLabel("none")).toBe("Off");
  expect(reasoningEffortLabel("xhigh")).toBe("Extra high");
  expect(reasoningEffortLabel("ultra")).toBe("Ultra");
});

test("Settings field picker keeps the searchable model catalog and separate effort menu", async () => {
  const models: string[] = [];
  const efforts: Array<string | null> = [];
  const { el, root, restore } = mount();

  try {
    await act(async () => {
      root.render(
        <ModelReasoningPicker
          currentModel="openai-codex/gpt-sol"
          currentEffort="low"
          efforts={["low", "medium", "high"]}
          groups={GROUPS}
          onSelectModel={(model) => models.push(model)}
          onSelectEffort={(effort) => efforts.push(effort)}
        />,
      );
    });

    const picker = el.querySelector<HTMLElement>(".model-picker--field")!;
    const modelTrigger = picker.querySelector<HTMLElement>('[role="combobox"]')!;
    const effortTrigger = picker.querySelector<HTMLElement>(
      '[aria-label="Reasoning effort: Low"]',
    )!;
    expect(picker).not.toBeNull();
    expect(modelTrigger).not.toBeNull();
    expect(effortTrigger).not.toBeNull();

    await act(async () => modelTrigger.click());
    const catalog = document.querySelector<HTMLElement>(
      '[role="dialog"][aria-label="Choose model"]',
    )!;
    expect(catalog.classList.contains("model-picker__model-menu")).toBe(true);
    expect(catalog.querySelector('[placeholder="Search models…"]')).not.toBeNull();
    expect(catalog.textContent).toContain("OpenAI Codex");

    await act(async () => effortTrigger.click());
    const effortMenu = document.querySelector<HTMLElement>(
      '[role="menu"][aria-label="Reasoning effort"]',
    )!;
    expect(effortMenu.classList.contains("model-picker__effort-menu")).toBe(true);
    expect(effortMenu.textContent).toContain("Model default");
    expect(effortMenu.textContent).toContain("High");
    expect(models).toEqual([]);
    expect(efforts).toEqual([]);
  } finally {
    await act(async () => root.unmount());
    restore();
  }
});

function PickerInsideTakeover({ onDismiss }: { onDismiss: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useOverlayLayer(ref, true, onDismiss);
  return (
    <div ref={ref}>
      <ModelMenuPicker
        value="default"
        options={[
          { value: "default", label: "session default" },
          { value: "gpt-sol", label: "gpt-sol" },
        ]}
        ariaLabel="Automation model"
        trigger={<span>Model</span>}
        onValueChange={() => {}}
      />
    </div>
  );
}

test("Escape closes a model menu before its owning takeover", async () => {
  const app = document.createElement("div");
  app.id = "app";
  document.body.append(app);
  const root = createRoot(app);
  let takeoverDismissals = 0;

  try {
    await act(async () => {
      root.render(
        <PickerInsideTakeover onDismiss={() => { takeoverDismissals += 1; }} />,
      );
    });

    const trigger = app.querySelector<HTMLButtonElement>(
      '[aria-label="Automation model"]',
    )!;
    await act(async () => trigger.click());
    // ModelMenuPicker is a searchable Combobox now, so its popup is a dialog.
    expect(document.querySelector('[role="dialog"][aria-label="Automation model"]')).not.toBeNull();

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", {
        key: "Escape",
        bubbles: true,
        cancelable: true,
      }));
    });

    expect(takeoverDismissals).toBe(0);
    expect(trigger.getAttribute("aria-expanded")).toBe("false");

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", {
        key: "Escape",
        bubbles: true,
        cancelable: true,
      }));
    });
    expect(takeoverDismissals).toBe(1);
  } finally {
    await act(async () => root.unmount());
    app.remove();
  }
});

test("all picker variants use the mock geometry, material, and side-aware motion contract", () => {
  const css = read("../src/design/model-pickers.css");
  const pickers = read("../src/components/ui/ModelPickers.tsx");
  const automation = read("../src/features/automations/components/AutomationDetail.tsx");
  const chat = read("../src/design/chat.css");

  expect(css).toMatch(/\.model-picker--field\s*\{[^}]*width:\s*276px;[^}]*grid-template-columns:\s*minmax\(0, 1fr\) auto;/s);
  // Triggers read as UI text; mono is reserved for the menu, where full ids
  // stack in a column and alignment does real work.
  expect(css).toMatch(
    /\.model-picker__trigger\s*\{[^}]*font:[^;]*var\(--sans\);/s,
  );
  expect(css).toMatch(/\.model-picker__model-option\s*\{[^}]*var\(--mono\);/s);
  // The effort sits a tier below the model it qualifies.
  expect(css).toMatch(
    /\.model-picker--field \.model-picker__effort-trigger\s*\{[^}]*background:\s*transparent;[^}]*box-shadow:\s*none;/s,
  );
  expect(css).toMatch(/:is\(\.model-picker--field, \.model-picker--embedding\) \.model-picker__trigger\s*\{[^}]*height:\s*32px;[^}]*background:\s*var\(--paper\);[^}]*box-shadow:\s*var\(--shadow-2\);/s);
  // 260px is still the design cap, but it yields to the room actually available
  // so a long catalog cannot run off the bottom of the window.
  expect(css).toMatch(
    /\.model-picker__model-menu\s*\{[^}]*width:\s*300px;[^}]*max-height:\s*min\(260px, max\(var\(--available-height, 260px\), 12rem\)\);/s,
  );
  expect(css).toMatch(/\.model-picker__model-menu\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;[^}]*overflow:\s*hidden;/s);
  expect(css).toMatch(/\.model-picker__model-list\s*\{[^}]*flex:\s*1;[^}]*overflow:\s*auto;/s);
  expect(pickers).not.toContain("autoHighlight");
  expect(pickers).toContain("model-search-wrap dp-search-shell dp-search-shell-compact model-picker__search");
  expect(pickers).toContain('<use href="#dp-search" />');
  expect(css).toMatch(/\.model-picker__effort-menu\s*\{[^}]*width:\s*116px;/s);
  expect(css).toMatch(/\.model-picker__positioner\s*\{[^}]*z-index:\s*var\(--z-nested\);/s);
  expect(css).toContain('[data-side="top"]');
  expect(css).toContain("--popover-offset-above");
  expect(pickers).toMatch(/export function ModelMenuPicker[\s\S]*?side="bottom"\s+align="start"/);
  expect(automation).toContain("<AutomationModelPicker");
  // An automation picks a model AND its reasoning effort, so it uses the same
  // pair Chat and Settings do rather than a lone model menu.
  expect(automation).toContain("<ModelReasoningPicker");
  expect(automation).not.toContain("<ModelMenuPicker");
  // That row sits low in a settings panel: flipping the menu above the trigger
  // would cover the fields the user just read, so it stays anchored.
  expect(automation).toContain("anchored");
  // The composer's combined model+effort menu is gone — Chat uses the same two
  // controls as Settings via ModelReasoningChip, so its bespoke menu classes
  // should exist nowhere.
  for (const retired of [".model-config-menu", ".model-effort-menu", ".model-config-positioner"]) {
    expect(css).not.toContain(retired);
    expect(chat).not.toContain(retired);
  }
  expect(pickers).not.toContain("ComposerModelConfigPicker");
});

test("one scroll gesture moves reasoning effort exactly one position", async () => {
  const committed: Array<string | null> = [];
  const { el, root, restore } = mount();

  const wheel = (node: HTMLElement, deltaY: number) => {
    const event = new WheelEvent("wheel", { deltaY, bubbles: true, cancelable: true });
    node.dispatchEvent(event);
    return event;
  };
  const label = () =>
    el.querySelector('[aria-label^="Reasoning effort:"]')?.getAttribute("aria-label");
  const settle = async (ms: number) => {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, ms)); });
  };

  try {
    // Controlled, as in the app: a commit round-trips through the server and
    // returns as a prop.
    let value: string | null = null;
    const draw = async () => {
      await act(async () => {
        root.render(
          <ModelReasoningPicker
            currentModel="openai-codex/gpt-sol"
            currentEffort={value}
            efforts={["low", "medium", "high"]}
            groups={GROUPS}
            onSelectModel={() => {}}
            onSelectEffort={(effort) => { committed.push(effort); value = effort; void draw(); }}
          />,
        );
      });
    };
    await draw();

    const trigger = el.querySelector<HTMLElement>(
      '[aria-label="Reasoning effort: Model default"]',
    )!;
    expect(trigger).not.toBeNull();

    // Drift below the threshold stays the page's gesture, not ours.
    expect(wheel(trigger, 3).defaultPrevented).toBe(false);
    expect(label()).toBe("Reasoning effort: Model default");

    // One flick — momentum and all — moves exactly one position.
    await act(async () => {
      for (let i = 0; i < 20; i += 1) wheel(trigger, 30);
    });
    expect(label()).toBe("Reasoning effort: Low");

    // Momentum after the step is swallowed rather than passed to the page.
    expect(wheel(trigger, 30).defaultPrevented).toBe(true);
    expect(label()).toBe("Reasoning effort: Low");

    // One write per gesture, not one per event.
    await settle(700);
    expect(committed).toEqual(["low"]);

    // A second deliberate gesture, after the wheel goes quiet, steps again.
    await act(async () => { wheel(trigger, 30); });
    expect(label()).toBe("Reasoning effort: Medium");
    await settle(700);
    expect(committed).toEqual(["low", "medium"]);

    // The scale clamps at its end instead of wrapping round to the opt-out.
    for (const _ of [0, 1, 2]) {
      await settle(700);
      await act(async () => { wheel(trigger, 30); });
    }
    await settle(700);
    expect(label()).toBe("Reasoning effort: High");
    expect(committed.at(-1)).toBe("high");
  } finally {
    await act(async () => root.unmount());
    restore();
  }
});

test("a flick's momentum tail cannot step a second time", async () => {
  const committed: Array<string | null> = [];
  const { el, root, restore } = mount();
  const wheel = (node: HTMLElement, deltaY: number) =>
    node.dispatchEvent(new WheelEvent("wheel", { deltaY, bubbles: true, cancelable: true }));
  const label = () =>
    el.querySelector('[aria-label^="Reasoning effort:"]')?.getAttribute("aria-label");

  try {
    let value: string | null = null;
    const draw = async () => {
      await act(async () => {
        root.render(
          <ModelReasoningPicker
            currentModel="openai-codex/gpt-sol"
            currentEffort={value}
            efforts={["low", "medium", "high"]}
            groups={GROUPS}
            onSelectModel={() => {}}
            onSelectEffort={(effort) => { committed.push(effort); value = effort; void draw(); }}
          />,
        );
      });
    };
    await draw();
    const trigger = el.querySelector<HTMLElement>(
      '[aria-label="Reasoning effort: Model default"]',
    )!;

    // One physical swipe: a fast burst, then a decaying tail whose events thin
    // out to ~120ms apart. All of it is one gesture and moves one position.
    await act(async () => { for (let i = 0; i < 12; i += 1) wheel(trigger, 40); });
    expect(label()).toBe("Reasoning effort: Low");

    for (const delta of [18, 9, 4, 2, 1]) {
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 120)); });
      await act(async () => { wheel(trigger, delta); });
    }
    expect(label()).toBe("Reasoning effort: Low");

    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 700)); });
    expect(committed).toEqual(["low"]);
  } finally {
    await act(async () => root.unmount());
    restore();
  }
});
