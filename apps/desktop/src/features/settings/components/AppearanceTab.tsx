import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import { RotateCcw } from "@/components/icons";
import {
  DEFAULT_QUICK_CAPTURE_SHORTCUT,
  isThinkingAnimation,
  isThinkingIntensity,
  useStore,
  type CornerProfile,
  type ThinkingAnimation,
  type ThemeChoice,
} from "@/stores";
import {
  eventToAccelerator,
  formatAccelerator,
  formatAcceleratorParts,
} from "@/lib/accelerator";
import { EASE_OUT, MOTION } from "@/lib/tokens/motion";
import { ICON } from "@/lib/icons";
import { BlurSwap } from "@/components/ui/BlurSwap";
import { IconButton } from "@/components/ui/IconButton";
import { radioGroupKeyDown } from "@/components/ui/RadioGroup";
import { Tab, Tabs } from "@/components/ui/Tabs";
import { ACCENT_PALETTES, type AccentPalette } from "@/lib/palettes";
import {
  THINKING_INTENSITIES,
  THINKING_TREATMENTS,
} from "@/lib/thinkingIndicator";
import {
  SettingsSection,
  SettingsSettingRow,
  SettingsSurface,
} from "@/features/settings/components/SettingsPage";

const THEMES: { id: ThemeChoice; label: string }[] = [
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
  { id: "system", label: "System" },
];

const CORNER_PROFILES: { id: CornerProfile; label: string }[] = [
  { id: "round", label: "Round" },
  { id: "square", label: "Soft square" },
];

export function AppearanceTab() {
  const theme = useStore((s) => s.prefs.theme);
  const accent = useStore((s) => s.prefs.accent);
  const cornerProfile = useStore((s) => s.prefs.cornerProfile);
  const thinkingAnimation = useStore((s) => s.prefs.thinkingAnimation);
  const thinkingIntensity = useStore((s) => s.prefs.thinkingIntensity);
  const setPref = useStore((s) => s.setPref);

  const onAccentKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) =>
    radioGroupKeyDown(e, accent, (v) => setPref("accent", v));
  const selectThinkingAnimation = (value: string) => {
    if (isThinkingAnimation(value)) setPref("thinkingAnimation", value);
  };
  const selectThinkingIntensity = (value: string) => {
    if (isThinkingIntensity(value)) setPref("thinkingIntensity", value);
  };

  return (
    <>
      <SettingsSection title="Interface" detail="local preference">
        <SettingsSurface>
          <SettingsSettingRow
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
                    {t.label}
                  </Tab>
                ))}
              </Tabs>
            }
          />
          <SettingsSettingRow
            title="Corner profile"
            hint="The shared radius language for controls, rows, and panels — capsules or softened rectangles."
            control={
              <Tabs variant="segmented"
                size="sm"
                value={cornerProfile}
                onChange={(v) => setPref("cornerProfile", v as CornerProfile)}
              >
                {CORNER_PROFILES.map((p) => (
                  <Tab key={p.id} value={p.id}>
                    {p.label}
                  </Tab>
                ))}
              </Tabs>
            }
          />
          <SettingsSettingRow
            title="Accent"
            hint="The single hue for links, active states, and controls. Surfaces, text, status, and code stay neutral."
            control={
              <div
                role="radiogroup"
                aria-label="Accent palette"
                onKeyDown={onAccentKeyDown}
                className="settings-accent-swatches"
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
            }
          />

          <SettingsSettingRow
            title="Quick capture shortcut"
            hint={
              <>
                Global hotkey to summon the floating composer from anywhere.{" "}
                <kbd className="arden-kbd">Enter</kbd> creates a new session and sends the message.
              </>
            }
            control={<ShortcutRecorder />}
          />
        </SettingsSurface>
      </SettingsSection>

      <SettingsSection title="Thinking indicator" detail={`${thinkingIntensity} intensity`}>
        <SettingsSurface>
          <SettingsSettingRow
            title="Thinking indicator"
            hint="Shown on the composer while the agent is running but has not yet streamed its first token."
            control={
              <Tabs
                variant="segmented"
                size="sm"
                label="Thinking indicator intensity"
                value={thinkingIntensity}
                onChange={selectThinkingIntensity}
              >
                {THINKING_INTENSITIES.map((intensity) => (
                  <Tab key={intensity.id} value={intensity.id}>
                    {intensity.label}
                  </Tab>
                ))}
              </Tabs>
            }
          />
          <ThinkingTreatmentPicker
            value={thinkingAnimation}
            onChange={selectThinkingAnimation}
          />
        </SettingsSurface>
      </SettingsSection>
    </>
  );
}

function ThinkingTreatmentPicker({
  value,
  onChange,
}: {
  value: ThinkingAnimation;
  onChange: (value: string) => void;
}) {
  return (
    <div
      role="radiogroup"
      aria-label="Thinking indicator treatment"
      className="settings-thinking-preview-grid"
      onKeyDown={(event) => radioGroupKeyDown(event, value, onChange)}
    >
      {THINKING_TREATMENTS.map((treatment) => {
        const selected = value === treatment.id;
        return (
          <button
            key={treatment.id}
            type="button"
            role="radio"
            aria-checked={selected}
            data-value={treatment.id}
            data-thinking-animation={treatment.id}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(treatment.id)}
            className={clsx("settings-thinking-card", selected && "is-selected")}
          >
            <span aria-hidden className="settings-thinking-card__mark" />
            <span className="settings-thinking-card__title">{treatment.label}</span>
            <span className="settings-thinking-card__description">{treatment.description}</span>
          </button>
        );
      })}
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
            "settings-shortcut-record",
            recording && "is-recording",
          )}
        >
          <BlurSwap swapKey={recording ? "recording" : value || "disabled"} blur={2}>
            {recording ? (
              "Press chord…"
            ) : value ? (
              <span
                className="settings-shortcut-keys"
                aria-label={formatAccelerator(value)}
              >
                {formatAcceleratorParts(value).map((part, index) => (
                  <kbd className="arden-kbd" key={`${part}-${index}`}>{part}</kbd>
                ))}
              </span>
            ) : (
              "Disabled"
            )}
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
                <RotateCcw size={ICON.SM} />
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

/** One accent-palette swatch. The picker shows the active light-surface hue,
 * matching the rest of the Settings paper rather than a second mini-theme. */
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
        "settings-accent-swatch",
        selected && "is-selected",
      )}
      style={{
        "--settings-accent-swatch": palette.light.accent,
      } as CSSProperties}
    />
  );
}
