import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import { ArrowUp, Check, Keyboard, Monitor, Moon, RotateCcw, Sun, type ArdenIcon } from "@/components/icons";
import {
  DEFAULT_QUICK_CAPTURE_SHORTCUT,
  useStore,
  type ThemeChoice,
  type ThinkingAnimation,
  type ThinkingIntensity,
} from "@/stores";
import { eventToAccelerator, formatAccelerator } from "@/lib/accelerator";
import { EASE_OUT, MOTION } from "@/lib/tokens/motion";
import { ICON } from "@/lib/icons";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { IconButton } from "@/components/ui/IconButton";
import { radioGroupKeyDown } from "@/components/ui/RadioGroup";
import { Tab, Tabs } from "@/components/ui/Tabs";
import { ACCENT_PALETTES, type AccentPalette } from "@/lib/palettes";

const VARIANTS: { id: ThinkingAnimation; label: string; hint: string }[] = [
  { id: "comet", label: "Comet", hint: "Single arc travels around the rim" },
  { id: "breath", label: "Breath", hint: "Wide diffuse halo that breathes slowly" },
  { id: "hue-cycle", label: "Border tint", hint: "Border color drifts toward accent — no motion" },
  { id: "send-orbit", label: "Send orbit", hint: "Spinner around the send button only" },
];

const THEMES: { id: ThemeChoice; label: string; icon: ArdenIcon }[] = [
  { id: "light", label: "Light", icon: Sun },
  { id: "dark", label: "Dark", icon: Moon },
  { id: "system", label: "System", icon: Monitor },
];

const INTENSITIES: { id: ThinkingIntensity; label: string }[] = [
  { id: "subtle", label: "Subtle" },
  { id: "normal", label: "Normal" },
  { id: "strong", label: "Strong" },
];

export function AppearanceTab() {
  const thinking = useStore((s) => s.prefs.thinkingAnimation);
  const intensity = useStore((s) => s.prefs.thinkingIntensity);
  const theme = useStore((s) => s.prefs.theme);
  const accent = useStore((s) => s.prefs.accent);
  const setPref = useStore((s) => s.setPref);

  const onAccentKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) =>
    radioGroupKeyDown(e, accent, (v) => setPref("accent", v));

  // The variant cards are a bespoke preview-card grid (RadioGroup's row-list +
  // radio dot don't fit), but the roving keyboard is the SHARED radioGroupKeyDown
  // — not re-implemented here. The cards carry role="radio" + data-value.
  const onVariantKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) =>
    radioGroupKeyDown(e, thinking, (v) => setPref("thinkingAnimation", v as ThinkingAnimation));

  return (
    <div className="grid gap-6">
      <section className="surface-rail divide-y divide-line-soft/50">
        <SettingRow
          title="Mode"
          hint="Light, Dark, or follow your system preference."
          control={
            <Tabs variant="segmented"
              size="sm"
              value={theme}
              onChange={(v) => setPref("theme", v as ThemeChoice)}
            >
              {THEMES.map((t) => (
                <Tab key={t.id} value={t.id}>
                  <t.icon size={ICON.MD} strokeWidth={2} />
                  {t.label}
                </Tab>
              ))}
            </Tabs>
          }
        />
      </section>

      <section className="surface-rail">
        <div className="px-4 py-3.5">
          <div className="text-base font-medium text-ink tracking-[-0.005em]">Accent</div>
          <div className="text-sm text-muted mt-0.5 leading-snug">
            The single hue for links, active states, and controls. Surfaces, text,
            status, and code stay neutral.
          </div>
          <div
            role="radiogroup"
            aria-label="Accent palette"
            onKeyDown={onAccentKeyDown}
            className="mt-3.5 flex flex-wrap gap-2.5"
          >
            {ACCENT_PALETTES.map((p) => (
              <AccentSwatch
                key={p.id}
                palette={p}
                selected={accent === p.id}
                onSelect={() => setPref("accent", p.id)}
              />
            ))}
          </div>
        </div>
      </section>

      <section className="surface-rail divide-y divide-line-soft/50">
        <SettingRow
          title="Quick capture shortcut"
          hint="Global hotkey to summon the floating composer from anywhere. Enter creates a new session and sends the message."
          control={<ShortcutRecorder />}
        />
      </section>

      <section className="surface-rail divide-y divide-line-soft/50">
        <SettingRow
          title="Thinking indicator"
          hint="Shown on the composer while the agent is running but has not yet streamed its first token."
          control={
            <Tabs variant="segmented"
              size="sm"
              value={intensity}
              onChange={(v) => setPref("thinkingIntensity", v as ThinkingIntensity)}
            >
              {INTENSITIES.map((o) => (
                <Tab key={o.id} value={o.id}>
                  {o.label}
                </Tab>
              ))}
            </Tabs>
          }
        />
        <div
          role="radiogroup"
          aria-label="Thinking animation"
          onKeyDown={onVariantKeyDown}
          className="px-4 py-4 grid grid-cols-[repeat(auto-fit,minmax(190px,1fr))] gap-2"
        >
          {VARIANTS.map((v) => (
            <VariantCard
              key={v.id}
              variant={v}
              intensity={intensity}
              selected={thinking === v.id}
              onSelect={() => setPref("thinkingAnimation", v.id)}
            />
          ))}
        </div>
      </section>

    </div>
  );
}

/** Click-to-record input for the global quick-capture shortcut.
 *
 *  Crucial detail: globally-registered chords are intercepted by the OS
 *  *before* the renderer gets a keydown event. If we left the current
 *  chord bound while recording, the user couldn't press it (or any
 *  other already-bound chord) — the keystroke would summon the quick
 *  window instead of reaching our handler. So we explicitly unregister
 *  during the recording window, then re-register either the new chord
 *  (on success) or the previous one (on cancel / failure). */
function ShortcutRecorder() {
  const value = useStore((s) => s.prefs.quickCaptureShortcut);
  const setPref = useStore((s) => s.setPref);
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!recording) return;

    // Snapshot the chord at record-start so cancel/cleanup can always
    // restore it even if the user changes the store mid-recording
    // (shouldn't happen, but defensive).
    const previous = value;
    let bound = false;

    // Unregister so the OS-level handler doesn't eat the chord we're
    // trying to record.
    void window.ardenDesktop?.quickCapture?.setShortcut?.("");

    const handler = async (event: KeyboardEvent) => {
      event.preventDefault();
      event.stopPropagation();
      // Escape cancels without binding anything.
      if (event.key === "Escape") {
        setRecording(false);
        return;
      }
      const accelerator = eventToAccelerator(event);
      if (!accelerator) return; // modifier-only or unsupported key — wait
      const ok = await window.ardenDesktop?.quickCapture?.setShortcut?.(accelerator);
      if (ok) {
        bound = true;
        setPref("quickCaptureShortcut", accelerator);
        setError(null);
      } else {
        setError(`'${formatAccelerator(accelerator)}' is already in use by another app.`);
      }
      setRecording(false);
    };
    window.addEventListener("keydown", handler, true);

    return () => {
      window.removeEventListener("keydown", handler, true);
      // If we didn't successfully bind a new chord, put the previous
      // one back so the user isn't left with no shortcut at all.
      if (!bound) void window.ardenDesktop?.quickCapture?.setShortcut?.(previous);
    };
    // value is captured via `previous`; intentionally only re-running
    // when recording flips so we don't re-snapshot mid-recording.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recording]);

  const reset = async () => {
    const ok = await window.ardenDesktop?.quickCapture?.setShortcut?.(DEFAULT_QUICK_CAPTURE_SHORTCUT);
    if (ok) {
      setPref("quickCaptureShortcut", DEFAULT_QUICK_CAPTURE_SHORTCUT);
      setError(null);
    } else {
      setError(`'${formatAccelerator(DEFAULT_QUICK_CAPTURE_SHORTCUT)}' is already in use.`);
    }
  };

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="inline-flex items-center gap-1.5">
        <button
          ref={ref}
          type="button"
          onClick={() => setRecording((r) => !r)}
          className={clsx(
            "inline-flex items-center gap-1.5 h-8 px-2.5 rounded-[8px] border text-sm font-medium tracking-[-0.005em] tabular-nums transition-[background-color,border-color,color,scale] duration-check ease-out active:scale-[0.97] min-w-[140px] justify-center",
            recording
              ? "border-accent bg-accent-soft text-accent-strong"
              : "border-line-soft bg-surface text-ink-soft hover:bg-surface-soft/60",
          )}
        >
          <Keyboard size={ICON.SM} strokeWidth={2} className="opacity-70" />
          <BlurSwap swapKey={recording ? "recording" : value || "disabled"} blur={2}>
            {recording ? "Press chord…" : (value ? formatAccelerator(value) : "Disabled")}
          </BlurSwap>
        </button>
        <AnimatePresence initial={false}>
          {value !== DEFAULT_QUICK_CAPTURE_SHORTCUT && (
            <motion.span
              key="reset"
              initial={{ opacity: 0, scale: 0.96 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.96 }}
              transition={{ duration: MOTION.check, ease: EASE_OUT }}
            >
              <IconButton
                size="lg"
                tone="faint"
                className="rounded-[8px]"
                onClick={() => void reset()}
                aria-label="Reset to default"
                title="Reset to default"
              >
                <RotateCcw size={ICON.SM} strokeWidth={2} />
              </IconButton>
            </motion.span>
          )}
        </AnimatePresence>
      </div>
      {error && (
        <span role="alert" className="text-xs text-bad text-right max-w-[260px]">{error}</span>
      )}
    </div>
  );
}

function SettingRow({
  title,
  hint,
  control,
}: {
  title: string;
  hint: string;
  control: ReactNode;
}) {
  return (
    <div className="flex flex-col items-start gap-3 px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
      <div className="min-w-0">
        <div className="text-base font-medium text-ink tracking-[-0.005em]">{title}</div>
        <div className="text-sm text-muted mt-0.5 leading-snug">{hint}</div>
      </div>
      <div className="shrink-0 max-w-full">{control}</div>
    </div>
  );
}

/** One accent-palette swatch. Split diagonally into the palette's light and
 *  dark accents so it stays legible on either theme's surface; the selected
 *  check uses `mix-blend-difference` to invert against whatever it sits on. */
function AccentSwatch({
  palette,
  selected,
  onSelect,
}: {
  palette: AccentPalette;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      aria-label={palette.name}
      title={palette.name}
      data-value={palette.id}
      tabIndex={selected ? 0 : -1}
      onClick={onSelect}
      className={clsx(
        "relative grid place-items-center w-9 h-9 rounded-full outline-none transition-[scale] duration-check ease-out active:scale-[0.9]",
        "focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface",
        selected
          ? "ring-2 ring-ink ring-offset-2 ring-offset-surface"
          : "ring-1 ring-inset ring-line-strong/40 hover:scale-[1.08]",
      )}
      style={{
        background: `linear-gradient(135deg, ${palette.light.accent} 0 50%, ${palette.dark.accent} 50% 100%)`,
      }}
    >
      {selected && (
        <Check size={ICON.SM} strokeWidth={3} className="text-white mix-blend-difference" />
      )}
    </button>
  );
}

/** Mini composer-shaped box that runs the variant continuously so the
 *  user can compare them side-by-side without leaving Settings. */
/** Wraps a Preview with hover-driven animation gating. Animations only
 *  run when the variant is selected or its card is being hovered — five
 *  always-on Houdini / conic-gradient previews on every settings open
 *  was wasteful (each comet preview compositor-tickrates a registered
 *  custom property at 60fps). */
function VariantCard({
  variant,
  intensity,
  selected,
  onSelect,
}: {
  variant: { id: ThinkingAnimation; label: string; hint: string };
  intensity: ThinkingIntensity;
  selected: boolean;
  onSelect: () => void;
}) {
  const [hovered, setHovered] = useState(false);
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      aria-label={variant.label}
      data-value={variant.id}
      tabIndex={selected ? 0 : -1}
      onClick={onSelect}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className={clsx(
        "group flex flex-col gap-2 p-3 rounded-[10px] border text-left transition-[background-color,border-color,scale] duration-check ease-out active:scale-[0.985]",
        selected
          ? "border-line-strong bg-surface-soft/60"
          : "border-line-soft bg-bg-main/30 hover:bg-surface-soft/40",
      )}
    >
      <Preview variant={variant.id} intensity={intensity} animate={selected || hovered} />
      <div className="grid gap-0.5">
        <div className="text-sm font-medium text-ink tracking-[-0.005em]">
          {variant.label}
        </div>
        <div className="text-xs text-faint leading-snug">{variant.hint}</div>
      </div>
    </button>
  );
}

function Preview({
  variant,
  intensity,
  animate,
}: {
  variant: ThinkingAnimation;
  intensity: ThinkingIntensity;
  animate: boolean;
}) {
  return (
    <div
      className="composer-card relative h-[44px] rounded-[10px] border border-line bg-surface flex items-center pl-3 pr-1.5"
      data-thinking={animate ? "true" : undefined}
      data-thinking-style={variant}
      data-thinking-intensity={intensity}
    >
      <span className="text-xs text-faint flex-1">Ask anything…</span>
      <span
        data-send="true"
        className="grid place-items-center w-6 h-6 rounded-full bg-ink text-on-ink shrink-0"
      >
        <ArrowUp size={ICON.SM} strokeWidth={2.4} />
      </span>
    </div>
  );
}
