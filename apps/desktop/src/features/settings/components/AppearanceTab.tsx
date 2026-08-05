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
  DEFAULT_PREFS,
  DEFAULT_QUICK_CAPTURE_SHORTCUT,
  FONT_SIZE_MAX,
  FONT_SIZE_MIN,
  isThinkingIntensity,
  useStore,
  type CornerProfile,
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
import { Input } from "@/components/ui/Input";
import { SwitchControl } from "@/components/ui/SwitchControl";
import { radioGroupKeyDown } from "@/components/ui/RadioGroup";
import { Tab, Tabs } from "@/components/ui/Tabs";
import { ACCENT_PALETTES, type AccentPalette } from "@/lib/palettes";
import {
  THINKING_INTENSITIES,
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
  const thinkingIntensity = useStore((s) => s.prefs.thinkingIntensity);
  const uiFont = useStore((s) => s.prefs.uiFont);
  const uiFontSize = useStore((s) => s.prefs.uiFontSize);
  const fontSmoothing = useStore((s) => s.prefs.fontSmoothing);
  const showReasoning = useStore((s) => s.prefs.showReasoning);
  const setPref = useStore((s) => s.setPref);

  const onAccentKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) =>
    radioGroupKeyDown(e, accent, (v) => setPref("accent", v));
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

      <SettingsSection title="Typography" detail="local preference">
        <SettingsSurface>
          <SettingsSettingRow
            title="Font"
            hint="Custom font-family stack for the interface. Empty uses the default Geist stack; code keeps Geist Mono."
            control={
              <Input
                value={uiFont}
                onChange={(e) => setPref("uiFont", e.target.value)}
                placeholder="Geist"
                aria-label="Font"
                spellCheck={false}
                autoComplete="off"
                className="w-[220px]"
              />
            }
          />
          <SettingsSettingRow
            title="Font size"
            hint="Base size the whole type scale derives from — code blocks and diffs follow."
            control={
              <FontSizeField
                value={uiFontSize}
                defaultValue={DEFAULT_PREFS.uiFontSize}
                onCommit={(v) => setPref("uiFontSize", v)}
                ariaLabel="Font size"
              />
            }
          />
          <SettingsSettingRow
            title="Font smoothing"
            hint="Grayscale anti-aliasing. Off uses the platform's native subpixel rendering."
            control={
              <SwitchControl
                checked={fontSmoothing}
                onChange={(next) => setPref("fontSmoothing", next)}
                aria-label="Font smoothing"
              />
            }
          />
        </SettingsSurface>
      </SettingsSection>

      <SettingsSection title="Agent activity">
        <SettingsSurface>
          <SettingsSettingRow
            title="Working strip"
            hint="Shown above the composer input while the agent is running. Names the tool it is currently in."
            control={
              <Tabs
                variant="segmented"
                size="sm"
                label="Working strip intensity"
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
          <SettingsSettingRow
            title="Show reasoning"
            hint="Renders the model's thinking as a collapsible block in chat. Only appears when the model runs with a reasoning effort set."
            control={
              <SwitchControl
                checked={showReasoning}
                onChange={(next) => setPref("showReasoning", next)}
                aria-label="Show reasoning"
              />
            }
          />
        </SettingsSurface>
      </SettingsSection>
    </>
  );
}


/** Numeric px entry for a font-size preference. Free typing is buffered
 *  locally and clamped to [FONT_SIZE_MIN, FONT_SIZE_MAX] on commit (blur or
 *  Enter) rather than on every keystroke, so typing "18" doesn't clamp
 *  through "1" first. Reset affordance follows ShortcutRecorder's pattern. */
function FontSizeField({
  value,
  defaultValue,
  onCommit,
  ariaLabel,
}: {
  value: number;
  defaultValue: number;
  onCommit: (next: number) => void;
  ariaLabel: string;
}) {
  const [draft, setDraft] = useState(String(value));

  useEffect(() => {
    setDraft(String(value));
  }, [value]);

  const commit = () => {
    const parsed = Math.round(Number(draft));
    const clamped = Number.isFinite(parsed)
      ? Math.min(FONT_SIZE_MAX, Math.max(FONT_SIZE_MIN, parsed))
      : value;
    setDraft(String(clamped));
    if (clamped !== value) onCommit(clamped);
  };

  return (
    <div className="inline-flex items-center gap-1.5">
      <div className="inline-flex items-center gap-1">
        <Input
          type="number"
          inputMode="numeric"
          aria-label={ariaLabel}
          min={FONT_SIZE_MIN}
          max={FONT_SIZE_MAX}
          step={1}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.key === "Enter") event.currentTarget.blur();
          }}
          className="w-16 text-right"
        />
        <span className="text-xs text-faint">px</span>
      </div>
      <AnimatePresence initial={false}>
        {value !== defaultValue && (
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
              onClick={() => onCommit(defaultValue)}
              aria-label="Reset to default"
              title="Reset to default"
            >
              <RotateCcw size={ICON.SM} />
            </IconButton>
          </motion.span>
        )}
      </AnimatePresence>
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
