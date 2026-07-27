import {
  useMemo,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { Combobox } from "@base-ui/react/combobox";
import { Menu } from "@base-ui/react/menu";
import clsx from "clsx";
import { Check, ChevronDown } from "@/components/icons";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { updateServerConfig, fetchServerConfig } from "@/actions/server";
import { updateSessionModelAction, refreshSessions } from "@/actions/sessions";
import type { ModelGroup, ServerConfig } from "@/api/types";
import { ICON } from "@/lib/icons";
import { useOverlayLayer } from "@/lib/overlayStack";
import { useStore } from "@/stores";

const PICKER_POSITIONING = {
  sideOffset: 6,
  collisionPadding: 16,
  collisionAvoidance: { side: "flip", align: "shift", fallbackAxisSide: "none" },
} as const;

const PICKER_POPUP_MOTION = "model-picker__popup-motion";

const DEFAULT_EFFORT_VALUE = "__provider_default__";

const REASONING_EFFORT_LABELS: Record<string, string> = {
  none: "Off",
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
  max: "Max",
  ultra: "Ultra",
};

function usePickerOverlay(open: boolean, onDismiss: () => void): {
  popupRef: (element: HTMLDivElement | null) => void;
} {
  const [element, setElement] = useState<HTMLDivElement | null>(null);
  const overlayRef = useMemo<RefObject<HTMLDivElement | null>>(
    () => ({ current: element }),
    [element],
  );
  useOverlayLayer(
    overlayRef,
    open && element !== null,
    onDismiss,
    false,
    false,
    false,
  );
  return { popupRef: setElement };
}

export function shortModelLabel(model: string): string {
  const slash = model.lastIndexOf("/");
  return slash >= 0 ? model.slice(slash + 1) : model;
}

/**
 * `null` means no Arden override, not "reasoning disabled". Some providers
 * expose a literal `none` effort, which is the actual off state.
 */
export function reasoningEffortLabel(effort: string | null): string {
  if (effort === null) return "Default";
  return REASONING_EFFORT_LABELS[effort]
    ?? `${effort.slice(0, 1).toUpperCase()}${effort.slice(1)}`;
}

/**
 * What the server is already configured to run — the chat default plus every
 * role's model. `/models` is fetched with `.catch(() => null)` and never
 * retried, so one failed catalog load at startup leaves every picker in the
 * app empty for the rest of the session. These are known-good ids that need no
 * catalog to be offerable.
 */
export function configuredModelChoices(config: ServerConfig | null): string[] {
  if (!config) return [];
  return [
    config.chat_model,
    ...Object.values(config.model_roles ?? {}).map((role) => role.model),
  ].filter((model): model is string => Boolean(model));
}

export function availableModelChoices(
  currentModel: string | null,
  catalogModels?: string[] | null,
): string[] {
  return [...new Set(
    [currentModel, ...(catalogModels ?? [])]
      .filter((model): model is string => Boolean(model)),
  )];
}

function effortValue(effort: string | null): string {
  return effort ?? DEFAULT_EFFORT_VALUE;
}

function effortFromValue(value: string): string | null {
  return value === DEFAULT_EFFORT_VALUE ? null : value;
}

function reasoningChoices(efforts: string[]): Array<{ value: string; label: string }> {
  return [
    { value: DEFAULT_EFFORT_VALUE, label: reasoningEffortLabel(null) },
    ...efforts.map((effort) => ({ value: effort, label: reasoningEffortLabel(effort) })),
  ];
}

export function ModelReasoningPicker({
  buttonLabel,
  currentModel,
  currentEffort,
  efforts,
  groups,
  disabled = false,
  onSelectModel,
  onSelectEffort,
}: {
  buttonLabel?: string;
  currentModel: string;
  currentEffort: string | null;
  efforts: string[];
  groups: ModelGroup[];
  disabled?: boolean;
  onSelectModel: (model: string) => void;
  onSelectEffort: (effort: string | null) => void;
}) {
  const [modelOpen, setModelOpen] = useState(false);
  const [query, setQuery] = useState("");
  const { popupRef: modelPopupRef } = usePickerOverlay(
    modelOpen,
    () => setModelOpen(false),
  );
  const groupedModels = useMemo(
    () =>
      groups.map((group) => ({
        value: group.label,
        items: group.models,
      })),
    [groups],
  );

  return (
    <div className="model-picker model-picker--field">
      <Combobox.Root
        items={groupedModels}
        value={currentModel}
        inputValue={query}
        open={modelOpen}
        disabled={disabled}
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
          className="model-picker__trigger model-picker__model-trigger group"
        >
          <BlurSwap
            swapKey={`${buttonLabel ?? currentModel}`}
            className="model-picker__current-model"
          >
            {buttonLabel ?? shortModelLabel(currentModel)}
          </BlurSwap>
          <Combobox.Icon className="model-picker__chevron">
            <ChevronDown size={ICON.SM} strokeWidth={2} />
          </Combobox.Icon>
        </Combobox.Trigger>

        <Combobox.Portal>
          <Combobox.Positioner
            {...PICKER_POSITIONING}
            side="bottom"
            align="start"
            className="model-picker__positioner"
          >
            <Combobox.Popup
              ref={modelPopupRef}
              aria-label="Choose model"
              className={clsx(
                "model-picker__popup model-picker__model-menu surface-popover",
                PICKER_POPUP_MOTION,
              )}
            >
              <div className="model-search-wrap dp-search-shell dp-search-shell-compact model-picker__search">
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <use href="#dp-search" />
                </svg>
                <Combobox.Input placeholder="Search models…" />
              </div>
              <Combobox.Empty className="model-picker__empty">No matches.</Combobox.Empty>
              <Combobox.List className="model-picker__model-list">
                {(group: { value: string; items: string[] }) => (
                  <Combobox.Group
                    key={group.value}
                    items={group.items}
                    className="model-picker__group"
                  >
                    {groups.length > 1 && (
                      <Combobox.GroupLabel className="dp-menu-label model-picker__group-label">
                        {group.value}
                      </Combobox.GroupLabel>
                    )}
                    <Combobox.Collection>
                      {(model: string) => (
                        <Combobox.Item
                          key={model}
                          value={model}
                          className="dp-menu-item model-picker__option model-picker__model-option"
                        >
                          <span>{model}</span>
                          <Combobox.ItemIndicator className="model-picker__check">
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

      <ReasoningMenu
        currentEffort={currentEffort}
        efforts={efforts}
        disabled={disabled || efforts.length === 0}
        onSelect={onSelectEffort}
      />
    </div>
  );
}

function ReasoningMenu({
  currentEffort,
  efforts,
  disabled,
  onSelect,
}: {
  currentEffort: string | null;
  efforts: string[];
  disabled: boolean;
  onSelect: (effort: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const { popupRef } = usePickerOverlay(open, () => setOpen(false));
  const choices = reasoningChoices(efforts);
  const currentLabel = efforts.length > 0 ? reasoningEffortLabel(currentEffort) : "—";

  return (
    <Menu.Root disabled={disabled} open={open} onOpenChange={setOpen}>
      <Menu.Trigger
        aria-label={
          efforts.length > 0
            ? `Reasoning effort: ${currentLabel}`
            : "Reasoning effort unavailable"
        }
        title={
          efforts.length > 0
            ? `Reasoning effort: ${currentLabel}`
            : "This model does not expose reasoning effort"
        }
        className="model-picker__trigger model-picker__effort-trigger group"
      >
        <BlurSwap swapKey={currentLabel} className="model-picker__current-effort">
          {currentLabel}
        </BlurSwap>
        <ChevronDown
          size={ICON.SM}
          strokeWidth={2}
          className="model-picker__chevron"
        />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner
          {...PICKER_POSITIONING}
          side="bottom"
          align="end"
          className="model-picker__positioner"
        >
          <Menu.Popup
            ref={popupRef}
            aria-label="Reasoning effort"
            className={clsx(
              "model-picker__popup model-picker__effort-menu surface-popover",
              PICKER_POPUP_MOTION,
            )}
          >
            <Menu.RadioGroup
              value={effortValue(currentEffort)}
              onValueChange={(next) => onSelect(effortFromValue(next))}
            >
              {choices.map((choice) => (
                <Menu.RadioItem
                  key={choice.value}
                  value={choice.value}
                  className="model-picker__option model-picker__effort-option"
                >
                  <span>{choice.label}</span>
                  <Menu.RadioItemIndicator className="model-picker__check">
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

export interface ModelMenuOption {
  value: string;
  label: string;
}

/**
 * Compact single-choice model menu used when the mockup makes the whole
 * surrounding control row the trigger (Automation).
 */
export function ModelMenuPicker({
  value,
  options,
  ariaLabel,
  trigger,
  triggerClassName,
  popupClassName,
  disabled = false,
  onValueChange,
}: {
  value: string;
  options: ModelMenuOption[];
  ariaLabel: string;
  trigger: ReactNode;
  triggerClassName?: string;
  popupClassName?: string;
  disabled?: boolean;
  onValueChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const { popupRef } = usePickerOverlay(open, () => setOpen(false));
  const labels = useMemo(
    () => new Map(options.map((option) => [option.value, option.label])),
    [options],
  );

  return (
    <Combobox.Root
      items={options.map((option) => option.value)}
      value={value}
      inputValue={query}
      disabled={disabled}
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen);
        if (!nextOpen) setQuery("");
      }}
      onInputValueChange={setQuery}
      onValueChange={(nextValue) => {
        if (nextValue !== null) onValueChange(nextValue);
        setQuery("");
      }}
    >
      <Combobox.Trigger
        aria-label={ariaLabel}
        className={triggerClassName}
      >
        {trigger}
      </Combobox.Trigger>
      <Combobox.Portal>
        <Combobox.Positioner
          {...PICKER_POSITIONING}
          side="bottom"
          align="start"
          className="model-picker__positioner"
        >
          <Combobox.Popup
            ref={popupRef}
            aria-label={ariaLabel}
            className={clsx(
              "model-picker__popup model-picker__model-menu surface-popover",
              popupClassName,
              PICKER_POPUP_MOTION,
            )}
          >
            <div className="model-search-wrap dp-search-shell dp-search-shell-compact model-picker__search">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <use href="#dp-search" />
              </svg>
              <Combobox.Input placeholder="Search models…" />
            </div>
            <Combobox.Empty className="model-picker__empty">No matches.</Combobox.Empty>
            <Combobox.List className="model-picker__model-list">
              {(model: string) => (
                <Combobox.Item
                  key={model || DEFAULT_EFFORT_VALUE}
                  value={model}
                  className="dp-menu-item model-picker__option model-picker__model-option"
                >
                  <span>{labels.get(model) ?? model}</span>
                  <Combobox.ItemIndicator className="model-picker__check">
                    <Check size={ICON.SM} strokeWidth={2.4} />
                  </Combobox.ItemIndicator>
                </Combobox.Item>
              )}
            </Combobox.List>
          </Combobox.Popup>
        </Combobox.Positioner>
      </Combobox.Portal>
    </Combobox.Root>
  );
}

/** Board's separate searchable model and reasoning controls for Chat. */
export function ModelReasoningChip() {
  const config = useStore((state) => state.serverConfig);
  const catalog = useStore((state) => state.serverModels);
  const currentSessionId = useStore((state) => state.currentSessionId);
  const sessions = useStore((state) => state.sessions);
  const [pendingModel, setPendingModel] = useState<string | null>(null);
  const [pendingEffort, setPendingEffort] = useState<string | null>(null);

  if (!config) return null;
  if (!Object.prototype.hasOwnProperty.call(config, "model_reasoning_efforts")) return null;

  const session = sessions.find((item) => item.session_id === currentSessionId);
  const currentModel = session?.chat_model ?? config.chat_model;
  const modelReasoningEfforts = config.model_reasoning_efforts;
  const currentEffort = modelReasoningEfforts[currentModel] ?? null;
  const configured = availableModelChoices(currentModel, configuredModelChoices(config));
  const groups: ModelGroup[] = catalog?.groups.length
    ? catalog.groups
    : [{ provider: "configured", label: "Configured", models: configured }];

  const selectModel = async (model: string) => {
    if (!currentSessionId || model === currentModel || pendingModel) return;
    setPendingModel(model);
    try {
      await updateSessionModelAction(currentSessionId, model);
    } catch {
      await refreshSessions();
    } finally {
      setPendingModel((pending) => (pending === model ? null : pending));
    }
  };

  const selectEffort = async (model: string, effort: string | null) => {
    if (!currentSessionId || pendingEffort) return;
    setPendingEffort(model);
    try {
      await updateServerConfig({ reasoning_model: model, reasoning_effort: effort });
    } catch {
      await Promise.all([fetchServerConfig(), refreshSessions()]);
    } finally {
      setPendingEffort((pending) => (pending === model ? null : pending));
    }
  };

  return (
    <ModelReasoningPicker
      buttonLabel={shortModelLabel(currentModel)}
      currentModel={currentModel}
      currentEffort={currentEffort}
      groups={groups}
      efforts={catalog?.reasoning_efforts[currentModel] ?? []}
      disabled={!currentSessionId || Boolean(pendingModel || pendingEffort)}
      onSelectModel={(model) => void selectModel(model)}
      onSelectEffort={(effort) => void selectEffort(currentModel, effort)}
    />
  );
}
