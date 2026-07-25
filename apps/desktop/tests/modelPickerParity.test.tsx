import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { act, useRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  ModelReasoningPicker,
  ModelMenuPicker,
  availableConfiguredModelChoices,
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

test("configured quick choices are bounded by explicit model roles and retain a session override", () => {
  expect(configuredModelChoices({
    currentModel: "openai-codex/gpt-terra",
    chatModel: "openai-codex/gpt-sol",
    researchModel: "claude-opus",
    workflowModel: "claude-opus",
    memoryModel: "claude-sonnet",
  })).toEqual([
    "openai-codex/gpt-terra",
    "openai-codex/gpt-sol",
    "claude-opus",
    "claude-sonnet",
  ]);
});

test("configured quick choices remain usable while the live catalog is unavailable", () => {
  const configured = {
    currentModel: "openai-codex/gpt-sol",
    chatModel: "openai-codex/gpt-sol",
    researchModel: "claude-opus",
    workflowModel: "claude-sonnet",
  };

  expect(availableConfiguredModelChoices(configured, null)).toEqual([
    "openai-codex/gpt-sol",
    "claude-opus",
    "claude-sonnet",
  ]);
  expect(availableConfiguredModelChoices(configured, ["claude-opus"])).toEqual([
    "openai-codex/gpt-sol",
    "claude-opus",
  ]);
});

test("reasoning labels preserve wire values without exposing implementation spelling", () => {
  expect(reasoningEffortLabel(null)).toBe("Default");
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
    expect(effortMenu.textContent).toContain("Default");
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
    expect(document.querySelector('[role="menu"][aria-label="Automation model"]')).not.toBeNull();

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

  expect(css).toMatch(/\.model-picker--field\s*\{[^}]*width:\s*276px;[^}]*grid-template-columns:\s*180px 88px;/s);
  expect(css).toMatch(/\.model-picker--field \.model-picker__trigger\s*\{[^}]*height:\s*32px;[^}]*background:\s*var\(--paper\);[^}]*box-shadow:\s*var\(--shadow-2\);/s);
  expect(css).toMatch(/\.model-picker__model-menu\s*\{[^}]*width:\s*300px;[^}]*max-height:\s*260px;/s);
  expect(css).toMatch(/\.model-picker__model-menu\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;[^}]*overflow:\s*hidden;/s);
  expect(css).toMatch(/\.model-picker__model-list\s*\{[^}]*flex:\s*1;[^}]*overflow:\s*auto;/s);
  expect(pickers).not.toContain("autoHighlight");
  expect(pickers).toContain("modelListRef.current?.scrollTo({ top: 0 })");
  expect(pickers).toContain("model-search-wrap dp-search-shell dp-search-shell-compact model-picker__search");
  expect(pickers).toContain('<use href="#dp-search" />');
  expect(css).toMatch(/\.model-picker__effort-menu\s*\{[^}]*width:\s*116px;/s);
  expect(css).toMatch(/\.model-picker__positioner\s*\{[^}]*z-index:\s*var\(--z-nested\);/s);
  expect(css).toContain('[data-side="top"]');
  expect(css).toContain("--popover-offset-above");
  expect(css).toMatch(/\.model-config-menu\s*\{[^}]*width:\s*264px;/s);
  expect(css).toMatch(/\.model-effort-menu\s*\{[^}]*width:\s*128px;/s);
  expect(css).toMatch(/\.automation-model-menu\s*\{[^}]*width:\s*min\(320px,/s);
  expect(css).toMatch(/@media \(max-width: 38\.75rem\)[\s\S]*?\.model-config-positioner\s*\{[^}]*inset:\s*auto 16px 108px/);
  expect(pickers).toMatch(/export function ModelMenuPicker[\s\S]*?side="bottom"\s+align="start"/);
  expect(automation).toContain("<AutomationModelPicker");
  expect(automation).toContain("<ModelMenuPicker");
  expect(automation).not.toContain("<ModelReasoningPicker");
  expect(chat).not.toContain(".model-config-menu");
});
