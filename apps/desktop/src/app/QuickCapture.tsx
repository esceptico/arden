import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUp, Camera, Check, ChevronDown, Plus, Sparkles } from "@/components/icons";
import { AnimatePresence, motion, MotionConfig } from "motion/react";
import clsx from "clsx";
import { Tooltip } from "@/components/ui/Tooltip";
import { ICON } from "@/lib/icons";
import { useThemeEffect } from "@/lib/theme";
import { EASE_OUT, MOTION, SPRING_POPOVER } from "@/lib/tokens/motion";
import { apiWithConfig, loadInitialConfig } from "@/api/core";
import type { SessionListItem } from "@/api/types";
import type { ImageBlock } from "@/stores";

/** Spotlight-style floating composer rendered in the quick-capture
 *  window (separate Electron BrowserWindow loaded with the
 *  `#quick-capture` hash). The window is frameless + transparent so
 *  this component owns the entire visible UI.
 *
 *  Flow: user types → Enter → IPC `quick:submit` → main process forwards
 *  to the main window's renderer, which routes into the chosen chat (or
 *  a fresh Inbox chat) via its existing actions. Capture is silent — the
 *  main window stays wherever it was; the card's dissolve is the submit
 *  acknowledgment.
 *
 *  Why route through the main window: switchSession/createSession/
 *  sendMessage rely on the Zustand store, SSE subscription, route hash,
 *  etc. — all wired in the main App. Duplicating that here would be a
 *  second implementation we'd then have to keep in sync.  */

const CARD_ENTER = { opacity: 0, y: -10, scale: 0.97, filter: "blur(3px)" };
const CARD_SETTLED = { opacity: 1, y: 0, scale: 1, filter: "blur(0px)" };
/** Submit exit drifts up — the thought "leaves". Cancel dissolves in place. */
const CARD_EXIT_SUBMIT = { opacity: 0, y: -8, scale: 0.98, filter: "blur(2px)" };
const CARD_EXIT_CANCEL = { opacity: 0, scale: 0.97, filter: "blur(3px)" };

type Phase = "compose" | "exit-submit" | "exit-cancel";

/** Mirrors QUICK_BASE_HEIGHT in electron/main.cjs. */
const BASE_WINDOW_HEIGHT = 100;
const PICKER_ROW_HEIGHT = 30;
const PICKER_PADDING = 14;
const MAX_PICKER_SESSIONS = 6;
const MAX_IMAGES = 3;

interface PickerItem {
  sessionId: string | null;
  label: string;
}

export function QuickCapture() {
  // Drives the .dark / .palette-<id> classes on <html> from the user's
  // prefs (same store the main window reads). Without this the quick
  // window is stuck in the light theme regardless of what the user
  // picked in Settings.
  useThemeEffect();

  const [text, setText] = useState("");
  const [images, setImages] = useState<ImageBlock[]>([]);
  const [capturing, setCapturing] = useState(false);
  const [phase, setPhase] = useState<Phase>("compose");
  // Bumped on every summon so the card remounts and replays its
  // entrance in sync with the window popping in.
  const [summonId, setSummonId] = useState(0);
  // Chat picker: where the capture goes. null target = new Inbox chat.
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [target, setTarget] = useState<SessionListItem | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerIndex, setPickerIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const items: PickerItem[] = [
    { sessionId: null, label: "New chat" },
    ...sessions.map((s) => ({ sessionId: s.session_id, label: s.name?.trim() || "Untitled chat" })),
  ];

  const closePicker = useCallback(() => {
    setPickerOpen(false);
    void window.ntrpDesktop?.quickCapture?.resize?.(BASE_WINDOW_HEIGHT);
  }, []);

  const openPicker = useCallback(() => {
    const rows = Math.min(sessions.length, MAX_PICKER_SESSIONS) + 1;
    void window.ntrpDesktop?.quickCapture?.resize?.(
      BASE_WINDOW_HEIGHT + rows * PICKER_ROW_HEIGHT + PICKER_PADDING,
    );
    setPickerIndex(Math.max(0, items.findIndex((i) => i.sessionId === (target?.session_id ?? null))));
    setPickerOpen(true);
  }, [sessions.length, items, target]);

  // Each summon (window shown by the global shortcut): re-present the
  // card, refresh the recent-chats list, and focus the input. The draft
  // survives accidental blur dismissals — it comes back pre-selected, so
  // typing replaces it and Enter sends it. Esc and submit clear it.
  useEffect(() => {
    const present = () => {
      setPhase("compose");
      setPickerOpen(false);
      setSummonId((n) => n + 1);
      void (async () => {
        try {
          const config = await loadInitialConfig();
          const { sessions: list } = await apiWithConfig<{ sessions: SessionListItem[] }>(
            config,
            "/sessions?limit=12",
          );
          setSessions(
            list
              .filter((s) => (s.session_type ?? "chat") === "chat")
              .slice(0, MAX_PICKER_SESSIONS),
          );
        } catch {
          setSessions([]);
        }
      })();
    };
    present();
    // The window persists across summons (hidden, never destroyed), so
    // main signals each one over IPC rather than relying on focus events.
    return window.ntrpDesktop?.quickCapture?.onSummon?.(present);
  }, []);

  // Focus is genuinely racy in a non-activating panel: the window becomes
  // key before Chromium marks the page focused, so a single .select() can
  // land in a still-unfocused document and the keystrokes die before the
  // input. Retry across frames until focus actually sticks.
  useEffect(() => {
    let raf = 0;
    let tries = 0;
    const attempt = () => {
      const el = inputRef.current;
      if (el) {
        window.focus();
        el.select();
        if (document.hasFocus() && document.activeElement === el) return;
      }
      if (++tries < 60) raf = requestAnimationFrame(attempt);
    };
    raf = requestAnimationFrame(attempt);
    return () => cancelAnimationFrame(raf);
  }, [summonId]);

  const onSubmit = useCallback(() => {
    const trimmed = text.trim();
    if ((!trimmed && images.length === 0) || phase !== "compose") return;
    void window.ntrpDesktop?.quickCapture?.submit({
      message: trimmed,
      images: images.length > 0 ? images : undefined,
      sessionId: target?.session_id ?? null,
    });
    setPhase("exit-submit");
  }, [text, images, target, phase]);

  const onClose = useCallback(() => {
    if (phase !== "compose") return;
    if (pickerOpen) {
      closePicker();
      return;
    }
    setPhase("exit-cancel");
  }, [phase, pickerOpen, closePicker]);

  // Esc arrives via IPC: AppKit consumes the key at the NSPanel layer
  // before the DOM ever sees it, so main claims it as a global shortcut
  // while the panel is visible and signals us instead.
  useEffect(() => window.ntrpDesktop?.quickCapture?.onDismiss?.(onClose), [onClose]);

  const onCapture = useCallback(async () => {
    if (capturing || phase !== "compose" || images.length >= MAX_IMAGES) return;
    setCapturing(true);
    try {
      // The panel hides during the interactive snip and re-presents
      // after (a fresh summon — draft and chips survive in state).
      const image = await window.ntrpDesktop?.quickCapture?.captureScreen?.();
      if (image) setImages((prev) => (prev.length >= MAX_IMAGES ? prev : [...prev, image]));
    } finally {
      setCapturing(false);
    }
  }, [capturing, phase, images.length]);

  const choosePickerItem = useCallback(
    (item: PickerItem) => {
      setTarget(item.sessionId ? (sessions.find((s) => s.session_id === item.sessionId) ?? null) : null);
      closePicker();
      inputRef.current?.focus();
    },
    [sessions, closePicker],
  );

  // Exit animation finished → actually hide the window (main keeps it
  // alive for the next summon) and drop the draft.
  const onCardAnimationComplete = useCallback((definition: unknown) => {
    if (definition !== CARD_EXIT_SUBMIT && definition !== CARD_EXIT_CANCEL) return;
    setText("");
    setImages([]);
    setTarget(null);
    void window.ntrpDesktop?.quickCapture?.close();
  }, []);

  const exiting = phase !== "compose";
  const disabled = (!text.trim() && images.length === 0) || exiting;

  return (
    <MotionConfig reducedMotion="user">
      {/* Padding gives the card's drop shadow room to render inside the
          transparent BrowserWindow (24px sides, 36px below — matched to
          the window size set in electron/main.cjs). The card itself IS
          the visible surface — no outer frame, no vibrancy layer. */}
      <div className="quick-capture-root grid min-h-screen px-6 pt-2 pb-9">
        <motion.div
          key={summonId}
          initial={CARD_ENTER}
          animate={exiting ? (phase === "exit-submit" ? CARD_EXIT_SUBMIT : CARD_EXIT_CANCEL) : CARD_SETTLED}
          transition={exiting ? { duration: MOTION.row, ease: EASE_OUT } : SPRING_POPOVER}
          onAnimationComplete={onCardAnimationComplete}
          className="quick-capture-card flex flex-col rounded-[12px] px-3.5"
        >
          {/* 56px = base window height (100) minus the root's 8px top +
              36px bottom shadow padding. Keep in sync with main.cjs. */}
          <div className="flex items-center gap-2.5 h-[56px] shrink-0">
            <span
              aria-hidden
              className="grid place-items-center w-5 h-5 text-accent shrink-0"
            >
              <Sparkles size={ICON.MD} strokeWidth={2} />
            </span>
            <input
              ref={inputRef}
              type="text"
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  if (pickerOpen) choosePickerItem(items[pickerIndex]);
                  else onSubmit();
                } else if (e.key === "ArrowDown") {
                  e.preventDefault();
                  if (pickerOpen) setPickerIndex((i) => (i + 1) % items.length);
                  else openPicker();
                } else if (e.key === "ArrowUp" && pickerOpen) {
                  e.preventDefault();
                  setPickerIndex((i) => (i - 1 + items.length) % items.length);
                }
              }}
              placeholder="What can I help you with today?"
              spellCheck={false}
              autoCorrect="off"
              autoCapitalize="off"
              readOnly={exiting}
              className="flex-1 min-w-0 bg-transparent border-0 outline-none text-[15px] text-ink placeholder:text-muted tracking-[-0.005em]"
            />
            {images.map((image, index) => (
              <Tooltip key={index} label="Remove screenshot">
              <button
                type="button"
                onClick={() => setImages((prev) => prev.filter((_, i) => i !== index))}
                className="shrink-0 w-8 h-6 rounded-[5px] overflow-hidden ring-1 ring-ink/15 hover:opacity-75 transition-opacity duration-check"
              >
                <img
                  src={`data:${image.media_type};base64,${image.data}`}
                  alt=""
                  className="w-full h-full object-cover"
                />
              </button>
              </Tooltip>
            ))}
            <button
              type="button"
              onClick={() => (pickerOpen ? closePicker() : openPicker())}
              title="Choose chat"
              className={clsx(
                "flex items-center gap-1 shrink-0 h-6 px-1.5 rounded-md text-[12px] max-w-[140px]",
                "text-muted hover:text-ink hover:bg-ink/5 transition-colors duration-check",
                pickerOpen && "text-ink bg-ink/5",
              )}
            >
              <span className="truncate">{target ? (target.name?.trim() || "Untitled chat") : "New chat"}</span>
              <ChevronDown
                size={ICON.XS}
                strokeWidth={2}
                className={clsx("shrink-0 transition-transform duration-check", pickerOpen && "rotate-180")}
              />
            </button>
            <Tooltip label="Capture a screenshot">
              <button
                type="button"
                onClick={() => void onCapture()}
                disabled={capturing || exiting || images.length >= MAX_IMAGES}
                aria-label="Capture screen"
                className={clsx(
                  "grid place-items-center w-6 h-6 rounded-md shrink-0 transition-[color,background-color,opacity] duration-check",
                  capturing || images.length >= MAX_IMAGES
                    ? "text-faint"
                    : "text-muted hover:text-ink hover:bg-ink/5",
                )}
              >
                <Camera size={ICON.SM} strokeWidth={2} />
              </button>
            </Tooltip>
            <button
              type="button"
              onClick={onSubmit}
              disabled={disabled}
              aria-label="Send"
              className={clsx(
                "grid place-items-center w-6 h-6 rounded-md shrink-0 transition-[opacity,scale] duration-check ease-out",
                disabled
                  ? "text-faint"
                  : "bg-ink text-on-ink hover:opacity-90 active:scale-[0.97]",
              )}
            >
              <ArrowUp size={ICON.SM} strokeWidth={2.4} />
            </button>
          </div>
          <AnimatePresence>
            {pickerOpen && (
              <motion.div
                key="picker"
                initial={{ opacity: 0, y: -4, filter: "blur(3px)" }}
                animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                exit={{ opacity: 0, transition: { duration: 0 } }}
                transition={{ duration: MOTION.row, ease: EASE_OUT }}
                className="border-t border-ink/8 py-1.5"
              >
                {items.map((item, index) => (
                  <button
                    key={item.sessionId ?? "new"}
                    type="button"
                    onClick={() => choosePickerItem(item)}
                    onMouseEnter={() => setPickerIndex(index)}
                    className={clsx(
                      "flex items-center gap-2 w-full h-[30px] px-2 rounded-md text-[13px] text-left",
                      index === pickerIndex ? "bg-ink/6 text-ink" : "text-muted",
                    )}
                  >
                    <span aria-hidden className="grid place-items-center w-4 shrink-0 text-faint">
                      {item.sessionId === null && <Plus size={ICON.XS} strokeWidth={2} />}
                    </span>
                    <span className="flex-1 truncate">{item.label}</span>
                    {(target?.session_id ?? null) === item.sessionId && (
                      <Check size={ICON.XS} strokeWidth={2.4} className="shrink-0 text-accent" />
                    )}
                  </button>
                ))}
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>
      </div>
    </MotionConfig>
  );
}
