import { useMemo, useState } from "react";
import { Combobox } from "@base-ui/react/combobox";
import { Menu } from "@base-ui/react/menu";
import { Check, ChevronDown, Search } from "@/components/icons";
import clsx from "clsx";
import { useStore } from "@/stores";
import { updateServerConfig, fetchServerConfig } from "@/actions/server";
import { updateSessionModelAction, refreshSessions } from "@/actions/sessions";
import type { ModelGroup } from "@/api/types";
import { ICON } from "@/lib/icons";

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  google: "Google",
  openrouter: "OpenRouter",
  xai: "xAI",
  custom: "Custom",
};

/** Strip `provider/` prefix so the chip stays compact. */
function shortModelLabel(model: string): string {
  const slash = model.lastIndexOf("/");
  return slash >= 0 ? model.slice(slash + 1) : model;
}

export function ModelReasoningPicker({
  buttonLabel,
  currentModel,
  currentEffort,
  efforts,
  groups,
  disabled = false,
  placement = "above-right",
  onSelectModel,
  onSelectEffort,
}: {
  buttonLabel?: string;
  currentModel: string;
  currentEffort: string | null;
  efforts: string[];
  groups: ModelGroup[];
  disabled?: boolean;
  placement?: "above-right" | "below-left";
  onSelectModel: (model: string) => void;
  onSelectEffort: (effort: string | null) => void;
}) {
  const [modelOpen, setModelOpen] = useState(false);
  const [query, setQuery] = useState("");
  const groupedModels = useMemo(
    () =>
      groups.map((group) => ({
        value: PROVIDER_LABELS[group.provider] ?? group.provider,
        items: group.models,
      })),
    [groups],
  );
  const side = placement === "above-right" ? "top" : "bottom";
  const align = placement === "above-right" ? "end" : "start";
  const uniformControls = placement === "below-left";

  return (
    <div
      className={clsx(
        "min-w-0 items-center",
        uniformControls
          ? "grid w-[276px] grid-cols-[180px_88px] gap-2"
          : "inline-flex gap-1",
      )}
    >
      <Combobox.Root
        items={groupedModels}
        value={currentModel}
        inputValue={query}
        open={modelOpen}
        disabled={disabled}
        autoHighlight
        onOpenChange={(open) => {
          setModelOpen(open);
          if (!open) setQuery("");
        }}
        onInputValueChange={setQuery}
        onValueChange={(model) => {
          if (model && model !== currentModel) onSelectModel(model);
          setQuery("");
        }}
      >
        <Combobox.Trigger
          aria-label="Choose model"
          title={currentModel}
          className={clsx(
            "group inline-flex h-7 min-w-0 max-w-[220px] items-center gap-1.5 rounded-full pl-2.5 pr-2 text-xs font-medium tracking-[-0.005em] text-muted outline-none select-none",
            "transition-[background-color,color,scale,box-shadow] duration-check ease-out active:scale-[0.97] hover:bg-surface-soft hover:text-ink data-popup-open:bg-surface-soft data-popup-open:text-ink focus-visible:shadow-[0_0_0_3px_var(--color-accent-soft)]",
            uniformControls && "w-full max-w-none justify-between",
            disabled && "opacity-60",
          )}
        >
          <span className="composer-model-label min-w-0 truncate font-mono text-xs text-ink-soft">
            {buttonLabel ?? shortModelLabel(currentModel)}
          </span>
          <Combobox.Icon className="shrink-0 text-faint">
            <ChevronDown
              size={ICON.SM}
              strokeWidth={2}
              className="transition-transform duration-check ease-out group-data-[popup-open]:rotate-180"
            />
          </Combobox.Icon>
        </Combobox.Trigger>

        <Combobox.Portal>
          <Combobox.Positioner side={side} align={align} sideOffset={6} className="z-[var(--z-popover)] outline-none">
            <Combobox.Popup
              aria-label="Choose model"
              className={clsx(
                "surface-panel surface-popover w-[320px] max-w-[var(--available-width)] origin-[var(--transform-origin)] overflow-hidden outline-none transition-[transform,opacity] duration-palette ease-out data-starting-style:scale-[0.98] data-starting-style:opacity-0 data-ending-style:scale-[0.98] data-ending-style:opacity-0",
                side === "top"
                  ? "data-starting-style:translate-y-1"
                  : "data-starting-style:-translate-y-1",
              )}
            >
              <div className="mx-2 mt-2 mb-1 flex h-8 items-center gap-2 rounded-full bg-surface-soft px-2.5 text-faint transition-[background-color,box-shadow] duration-check ease-out focus-within:bg-surface-sunken focus-within:shadow-[0_0_0_2px_var(--color-accent-soft)]">
                <Search size={ICON.SM} className="shrink-0" aria-hidden="true" />
                <Combobox.Input
                  placeholder="Search models…"
                  className="min-w-0 flex-1 border-0 bg-transparent text-sm text-ink outline-none placeholder:text-muted"
                />
              </div>
              <Combobox.Empty className="px-3 py-3 text-sm text-muted italic">
                No matches.
              </Combobox.Empty>
              <Combobox.List className="relative max-h-[260px] overflow-y-auto scroll-thin p-1 outline-none data-empty:p-0">
                {(group: { value: string; items: string[] }) => (
                  <Combobox.Group key={group.value} items={group.items} className="pb-1 last:pb-0">
                    {groups.length > 1 && (
                      <Combobox.GroupLabel className="px-2.5 pb-1 pt-2 font-mono text-[11px] font-medium uppercase tracking-[0.06em] text-faint select-none">
                        {group.value}
                      </Combobox.GroupLabel>
                    )}
                    <Combobox.Collection>
                      {(model: string) => (
                        <Combobox.Item
                          key={model}
                          value={model}
                          className="relative flex min-h-8 cursor-default items-center gap-2 rounded-md py-1.5 pl-2 pr-8 font-mono text-sm text-ink-soft outline-none select-none data-highlighted:bg-fill-hover data-highlighted:text-ink data-selected:bg-fill-selected data-selected:text-ink"
                        >
                          <span className="min-w-0 flex-1 truncate">{model}</span>
                          <Combobox.ItemIndicator className="absolute right-2 grid size-4 place-items-center text-ink">
                            <Check size={ICON.SM} strokeWidth={2.4} />
                          </Combobox.ItemIndicator>
                        </Combobox.Item>
                      )}
                    </Combobox.Collection>
                  </Combobox.Group>
                )}
              </Combobox.List>
            </Combobox.Popup>
          </Combobox.Positioner>
        </Combobox.Portal>
      </Combobox.Root>

      {efforts.length > 0 && (
        <ReasoningMenu
          currentEffort={currentEffort}
          efforts={efforts}
          disabled={disabled}
          side={side}
          align={align}
          uniform={uniformControls}
          onSelect={onSelectEffort}
        />
      )}
    </div>
  );
}

function ReasoningMenu({
  currentEffort,
  efforts,
  disabled,
  side,
  align,
  uniform,
  onSelect,
}: {
  currentEffort: string | null;
  efforts: string[];
  disabled: boolean;
  side: "top" | "bottom";
  align: "start" | "end";
  uniform: boolean;
  onSelect: (effort: string | null) => void;
}) {
  const value = currentEffort ?? "off";

  return (
    <Menu.Root>
      <Menu.Trigger
        disabled={disabled}
        aria-label={`Reasoning effort: ${value}`}
        title={`Reasoning effort: ${value}`}
        className={clsx(
          "inline-flex h-7 shrink-0 items-center gap-1 rounded-full px-2 text-xs font-medium capitalize tracking-[-0.005em] text-muted outline-none select-none",
          "transition-[background-color,color,scale,box-shadow] duration-check ease-out active:scale-[0.97] hover:bg-surface-soft hover:text-ink data-popup-open:bg-surface-soft data-popup-open:text-ink focus-visible:shadow-[0_0_0_3px_var(--color-accent-soft)]",
          uniform && "w-full justify-between",
          disabled && "opacity-60",
        )}
      >
        {value}
        <ChevronDown size={ICON.SM} strokeWidth={2} className="text-faint" />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner side={side} align={align} sideOffset={6} className="z-[var(--z-popover)] outline-none">
          <Menu.Popup
            className={clsx(
              "surface-panel surface-popover min-w-32 origin-[var(--transform-origin)] p-1 outline-none transition-[transform,opacity] duration-palette ease-out data-starting-style:scale-[0.98] data-starting-style:opacity-0 data-ending-style:scale-[0.98] data-ending-style:opacity-0",
              side === "top"
                ? "data-starting-style:translate-y-1"
                : "data-starting-style:-translate-y-1",
            )}
          >
            <Menu.RadioGroup
              value={value}
              onValueChange={(next) => onSelect(next === "off" ? null : next)}
            >
              {["off", ...efforts].map((effort) => (
                <Menu.RadioItem
                  key={effort}
                  value={effort}
                  className="relative flex min-h-8 cursor-default items-center rounded-md py-1.5 pl-2 pr-8 text-sm capitalize text-ink-soft outline-none select-none data-highlighted:bg-fill-hover data-highlighted:text-ink"
                >
                  {effort}
                  <Menu.RadioItemIndicator className="absolute right-2 grid size-4 place-items-center text-ink">
                    <Check size={ICON.SM} strokeWidth={2.4} />
                  </Menu.RadioItemIndicator>
                </Menu.RadioItem>
              ))}
            </Menu.RadioGroup>
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}

/** Adjacent model combobox + reasoning menu used at the right edge of the composer. */
export function ModelReasoningChip() {
  const cfg = useStore((s) => s.serverConfig);
  const models = useStore((s) => s.serverModels);
  const currentSessionId = useStore((s) => s.currentSessionId);
  const sessions = useStore((s) => s.sessions);
  const [busy, setBusy] = useState(false);

  const groups = useMemo(() => {
    if (!models) return [];
    return models.groups.length > 0
      ? models.groups
      : [{ provider: "all", models: models.models }];
  }, [models]);

  if (!cfg) return null;
  if (!Object.prototype.hasOwnProperty.call(cfg, "model_reasoning_efforts")) return null;

  // Per-chat model: the active session's override, falling back to the
  // global default (also what new chats inherit). Legacy sessions with no
  // stored model resolve to the global default too.
  const session = sessions.find((s) => s.session_id === currentSessionId);
  const currentModel = session?.chat_model ?? cfg.chat_model;
  const modelReasoningEfforts = cfg.model_reasoning_efforts;
  const efforts = models?.reasoning_efforts?.[currentModel] ?? cfg.reasoning_efforts;
  const currentEffort = modelReasoningEfforts[currentModel] ?? cfg.reasoning_effort;

  const apply = async (patch: Record<string, unknown>) => {
    if (busy) return;
    setBusy(true);
    try {
      await updateServerConfig(patch);
    } catch {
      await fetchServerConfig();
    } finally {
      setBusy(false);
    }
  };

  const selectModel = async (model: string) => {
    if (busy || !currentSessionId) return;
    setBusy(true);
    try {
      await updateSessionModelAction(currentSessionId, model);
    } catch {
      await refreshSessions();
    } finally {
      setBusy(false);
    }
  };

  return (
    <ModelReasoningPicker
      currentModel={currentModel}
      currentEffort={currentEffort}
      efforts={efforts}
      groups={groups}
      disabled={busy || !models || !currentSessionId}
      placement="above-right"
      onSelectModel={(model) => void selectModel(model)}
      onSelectEffort={(effort) =>
        void apply({ reasoning_model: currentModel, reasoning_effort: effort })
      }
    />
  );
}
