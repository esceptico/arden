import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { Check, ChevronDown } from "lucide-react";
import clsx from "clsx";
import { useStore } from "@/stores";
import { updateServerConfig, fetchServerConfig } from "@/actions/server";
import { updateSessionModelAction, refreshSessions } from "@/actions/sessions";
import type { ModelGroup } from "@/api/types";
import { ICON } from "@/lib/icons";
import { DURATION_POPOVER, EASE_DECELERATE, EASE_OUT, MOTION } from "@/lib/tokens/motion";
import { useReanchor } from "@/lib/hooks";
import { Caption } from "@/components/ui/Caption";

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
  modelReasoningEfforts = {},
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
  modelReasoningEfforts?: Record<string, string>;
  placement?: "above-right" | "below-left";
  onSelectModel: (model: string) => void;
  onSelectEffort: (effort: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  // Anchor the portaled popover off the trigger's bounding rect.
  // `above-right` floats the popover above the chip with its right edge
  // aligned to the chip's right edge (default for the composer
  // toolbar). `below-left` is a future-proofing escape hatch.
  const [coords, setCoords] = useState<{
    bottom?: number;
    top?: number;
    left?: number;
    right?: number;
  } | null>(null);

  useReanchor(open, () => {
    const t = triggerRef.current;
    if (!t) return;
    const r = t.getBoundingClientRect();
    if (placement === "above-right") {
      setCoords({ bottom: window.innerHeight - r.top + 6, right: window.innerWidth - r.right });
    } else {
      setCoords({ top: r.bottom + 6, left: r.left });
    }
  });

  // Outside-click closes the picker. The portaled popover lives outside
  // `wrapRef` so we have to check both the trigger and the popover refs;
  // clicks anywhere else dismiss.
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target)) return;
      if (popoverRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const filteredGroups = useMemo(() => {
    if (!query.trim()) return groups;
    const q = query.toLowerCase();
    return groups
      .map((g) => ({ ...g, models: g.models.filter((m) => m.toLowerCase().includes(q)) }))
      .filter((g) => g.models.length > 0);
  }, [groups, query]);

  return (
    <div ref={wrapRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        title={efforts.length > 0 ? `${currentModel} · thinking ${currentEffort ?? "off"}` : currentModel}
        className={clsx(
          "inline-flex items-center gap-1.5 h-7 pl-2.5 pr-2 rounded-full text-xs font-medium tracking-[-0.005em] transition-[background-color,color,scale] duration-check ease-out active:scale-[0.97] select-none max-w-[260px]",
          open
            ? "bg-surface-soft text-ink"
            : "text-muted hover:bg-surface-soft hover:text-ink",
          disabled && "opacity-60",
        )}
      >
        <span className="composer-model-label truncate font-mono text-xs text-ink-soft">
          {buttonLabel ?? shortModelLabel(currentModel)}
        </span>
        {efforts.length > 0 && (
          <>
            <span className="composer-effort-separator text-whisper">·</span>
            <span className="text-muted">{currentEffort ?? "off"}</span>
          </>
        )}
        <ChevronDown size={ICON.SM} strokeWidth={2} className="shrink-0 opacity-70" />
      </button>

      {createPortal(
        <AnimatePresence>
          {open && coords && (
        <motion.div
          ref={popoverRef}
          initial={{ opacity: 0, y: 4, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, scale: 0.98, transition: { duration: MOTION.fast, ease: EASE_OUT } }}
          transition={{ duration: DURATION_POPOVER, ease: EASE_DECELERATE }}
          style={{
            position: "fixed",
            ...coords,
            zIndex: 60,
            transformOrigin: placement === "above-right" ? "bottom right" : "top left",
          }}
          className="surface-panel surface-popover w-[300px] overflow-hidden"
        >
          {efforts.length > 0 && (
            <div className="grid gap-1 px-3 pt-2.5 pb-2 border-b border-line-soft">
              <Caption as="div">Reasoning effort</Caption>
              <div className="flex flex-wrap gap-1">
                <EffortPill
                  label="off"
                  active={currentEffort === null}
                  onClick={() => onSelectEffort(null)}
                />
                {efforts.map((eff) => (
                  <EffortPill
                    key={eff}
                    label={eff}
                    active={currentEffort === eff}
                    onClick={() => onSelectEffort(eff)}
                  />
                ))}
              </div>
            </div>
          )}
          <div className="grid">
            <input
              type="text"
              placeholder="Search models…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="w-full h-8 px-3 border-0 border-b border-line-soft bg-transparent text-sm text-ink outline-none placeholder:text-muted"
              autoFocus
            />
            <div className="relative max-h-[260px] overflow-y-auto scroll-thin py-1">
              {filteredGroups.length === 0 && (
                <div className="px-3 py-2 text-sm text-muted italic">No matches.</div>
              )}
              {filteredGroups.map((g) => (
                <div key={g.provider}>
                  {groups.length > 1 && (
                    <Caption as="div" className="px-3 pt-2 pb-1 select-none">
                      {PROVIDER_LABELS[g.provider] ?? g.provider}
                    </Caption>
                  )}
                  {g.models.map((m) => {
                    const isCurrent = m === currentModel;
                    const savedEffort = modelReasoningEfforts[m];
                    return (
                      <button
                        key={m}
                        type="button"
                        onClick={() => {
                          if (m !== currentModel) onSelectModel(m);
                          setQuery("");
                        }}
                        data-active={isCurrent ? "true" : undefined}
                        className="app-row w-full flex items-center gap-2 px-3 py-1.5 text-left text-sm font-mono text-ink-soft hover:bg-ink/[0.06] focus-visible:bg-ink/[0.06]"
                      >
                        <span className="grid place-items-center w-3 h-3 shrink-0">
                          {isCurrent && <Check size={ICON.SM} strokeWidth={2.4} className="text-accent" />}
                        </span>
                        <span className="min-w-0 flex-1 truncate">{m}</span>
                        {savedEffort && (
                          <span className="shrink-0 text-xs font-sans text-muted">
                            {savedEffort}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
        </motion.div>
          )}
        </AnimatePresence>,
        document.body
      )}
    </div>
  );
}

/** Combined model + reasoning chip used at the right edge of the composer. */
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
      modelReasoningEfforts={modelReasoningEfforts}
      placement="above-right"
      onSelectModel={(model) => void selectModel(model)}
      onSelectEffort={(effort) =>
        void apply({ reasoning_model: currentModel, reasoning_effort: effort })
      }
    />
  );
}

function EffortPill({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        "h-6 px-2 rounded-full text-xs font-medium tracking-[-0.005em] transition-[background-color,color,scale] duration-check ease-out select-none capitalize active:scale-[0.97]",
        active
          ? "bg-accent-soft text-accent-strong"
          : "text-muted hover:bg-surface-soft hover:text-ink",
      )}
    >
      {label}
    </button>
  );
}
