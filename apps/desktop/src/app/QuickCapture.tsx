import { useCallback, useEffect, useRef, useState } from "react";
import { Camera, Check, Plus, X } from "@/components/icons";
import { AnimatePresence, motion, MotionConfig } from "motion/react";
import clsx from "clsx";
import { Tooltip } from "@/components/ui/Tooltip";
import { Button } from "@/components/ui/Button";
import { IconButton } from "@/components/ui/IconButton";
import { ICON } from "@/lib/icons";
import { useThemeEffect } from "@/lib/theme";
import { useCornerProfileEffect } from "@/lib/cornerProfile";
import {
  EXIT_FAST,
  POPOVER_ENTER_TRANSITION,
  POSE_INLINE_POPOVER_IN,
  POSE_INLINE_POPOVER_OUT,
  POSE_INLINE_POPOVER_VISIBLE,
  POSE_SHEET_IN,
  POSE_SHEET_OUT,
  POSE_SHEET_VISIBLE,
  SHEET_ENTER_TRANSITION,
  SHEET_EXIT_TRANSITION,
} from "@/lib/tokens/motion";
import { apiWithConfig, loadInitialConfig } from "@/api/core";
import type { SessionListItem } from "@/api/types";
import type { ImageBlock } from "@/stores";

/** Spotlight-style floating composer rendered in the quick-capture
 *  window (separate Electron BrowserWindow loaded with the
 *  `#quick-capture` hash). The window is frameless + transparent so
 *  this component owns the entire visible UI.
 *
 *  Flow: user captures a thought → Cmd/Ctrl+Enter → IPC `quick:submit` → main process forwards
 *  to the main window's renderer, which routes into the chosen chat (or
 *  a fresh Inbox chat) via its existing actions. Capture is silent — the
 *  main window stays wherever it was; the card's dissolve is the submit
 *  acknowledgment.
 *
 *  Why route through the main window: switchSession/createSession/
 *  sendMessage rely on the Zustand store, SSE subscription, route hash,
 *  etc. — all wired in the main App. Duplicating that here would be a
 *  second implementation we'd then have to keep in sync.  */

type Phase = "compose" | "exit-submit" | "exit-cancel";

/** Mirrors QUICK_BASE_HEIGHT in electron/main.cjs. */
const BASE_WINDOW_HEIGHT = 324;
const PICKER_ROW_HEIGHT = 30;
// 12px gap + 1px rule + 6px inset above the 30px rows.
const PICKER_OVERHEAD = 19;
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
  useCornerProfileEffect();

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
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const items: PickerItem[] = [
    { sessionId: null, label: "New chat" },
    ...sessions.map((s) => ({ sessionId: s.session_id, label: s.name?.trim() || "Untitled chat" })),
  ];

  const closePicker = useCallback(() => {
    setPickerOpen(false);
    void window.ardenDesktop?.quickCapture?.resize?.(BASE_WINDOW_HEIGHT);
  }, []);

  const openPicker = useCallback(() => {
    const rows = Math.min(sessions.length, MAX_PICKER_SESSIONS) + 1;
    void window.ardenDesktop?.quickCapture?.resize?.(
      BASE_WINDOW_HEIGHT + rows * PICKER_ROW_HEIGHT + PICKER_OVERHEAD,
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
    return window.ardenDesktop?.quickCapture?.onSummon?.(present);
  }, []);

  // Focus is genuinely racy in a non-activating panel: the window becomes
  // key before Chromium marks the page focused, so a single .select() can
  // land in a still-unfocused document and the keystrokes die before the
  // field. Retry across frames until focus actually sticks.
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
    void window.ardenDesktop?.quickCapture?.submit({
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
  useEffect(() => window.ardenDesktop?.quickCapture?.onDismiss?.(onClose), [onClose]);

  const onCapture = useCallback(async () => {
    if (capturing || phase !== "compose" || images.length >= MAX_IMAGES) return;
    setCapturing(true);
    try {
      // The panel hides during the interactive snip and re-presents
      // after (a fresh summon — draft and chips survive in state).
      const image = await window.ardenDesktop?.quickCapture?.captureScreen?.();
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
  const onCardAnimationComplete = useCallback(() => {
    if (phase === "compose") return;
    setText("");
    setImages([]);
    setTarget(null);
    void window.ardenDesktop?.quickCapture?.close();
  }, [phase]);

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
          initial={POSE_SHEET_IN}
          animate={exiting ? POSE_SHEET_OUT : POSE_SHEET_VISIBLE}
          transition={exiting ? SHEET_EXIT_TRANSITION : SHEET_ENTER_TRANSITION}
          onAnimationComplete={onCardAnimationComplete}
          className="quick-capture-card surface-sheet flex h-full flex-col overflow-hidden rounded-[var(--r-panel)]"
        >
          <header className="flex h-12 shrink-0 items-center border-b border-line px-3 pl-4">
            <strong className="text-base font-semibold text-ink">Quick capture</strong>
            <IconButton
              onClick={onClose}
              aria-label="Close quick capture"
              title="Close quick capture"
              className="ml-auto"
            >
              <X size={ICON.SM} />
            </IconButton>
          </header>
          <div className="min-h-0 flex-1 p-4">
            <textarea
              ref={inputRef}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  if (pickerOpen) choosePickerItem(items[pickerIndex]);
                  else onSubmit();
                }
              }}
              placeholder="What needs attention?"
              spellCheck={false}
              autoCorrect="off"
              autoCapitalize="off"
              readOnly={exiting}
              aria-label="Capture message"
              className="arden-field min-h-28 resize-none rounded-[var(--r-panel)] py-3"
            />
            <div className="group mt-3 flex min-h-6 items-center justify-between gap-3 text-2xs font-mono text-faint">
              <div className="flex min-w-0 items-center gap-1">
                <button
                  type="button"
                  onClick={() => (pickerOpen ? closePicker() : openPicker())}
                  aria-expanded={pickerOpen}
                  aria-label="Choose destination chat"
                  className={clsx(
                    "flex min-w-0 items-center gap-1 rounded-[var(--r-tag)] text-left",
                    pickerOpen && "bg-fill-selected px-1 text-ink",
                  )}
                >
                  <span className="truncate">
                    {target ? `Inbox · ${target.name?.trim() || "Untitled chat"}` : "Inbox · new chat"}
                  </span>
                </button>
                {images.map((image, index) => (
                  <Tooltip key={index} label="Remove screenshot">
                    <button
                      type="button"
                      onClick={() => setImages((prev) => prev.filter((_, i) => i !== index))}
                      className="h-6 w-8 shrink-0 overflow-hidden rounded-[var(--r-tag)] ring-1 ring-ink/15"
                    >
                      <img
                        src={`data:${image.media_type};base64,${image.data}`}
                        alt=""
                        className="h-full w-full object-cover"
                      />
                    </button>
                  </Tooltip>
                ))}
                <IconButton
                  size="xs"
                  onClick={() => void onCapture()}
                  disabled={capturing || exiting || images.length >= MAX_IMAGES}
                  aria-label="Capture screen"
                  title="Capture a screenshot"
                  className="opacity-0 transition-opacity duration-check ease-out group-hover:opacity-100 focus-visible:opacity-100"
                >
                  <Camera size={ICON.XS} />
                </IconButton>
              </div>
              <span className="flex shrink-0 items-center gap-1">
                <span className="flex items-center gap-1" aria-label="Command Enter">
                  <kbd className="arden-kbd">⌘</kbd>
                  <kbd className="arden-kbd">Enter</kbd>
                </span>
                to send
              </span>
            </div>
            <AnimatePresence>
              {pickerOpen && (
                <motion.div
                  key="picker"
                  initial={POSE_INLINE_POPOVER_IN}
                  animate={POSE_INLINE_POPOVER_VISIBLE}
                  exit={{ ...POSE_INLINE_POPOVER_OUT, transition: EXIT_FAST }}
                  transition={POPOVER_ENTER_TRANSITION}
                  className="mt-3 border-t border-line pt-1.5"
                >
                  {items.map((item, index) => (
                    <button
                      key={item.sessionId ?? "new"}
                      type="button"
                      onClick={() => choosePickerItem(item)}
                      onMouseEnter={() => setPickerIndex(index)}
                      className={clsx(
                        "flex h-[30px] w-full items-center gap-2 rounded-[var(--r-mark)] px-2 text-left text-sm",
                        index === pickerIndex ? "bg-fill-selected text-ink" : "text-muted",
                      )}
                    >
                      <span aria-hidden className="grid w-4 shrink-0 place-items-center text-faint">
                        {item.sessionId === null && <Plus size={ICON.XS} />}
                      </span>
                      <span className="flex-1 truncate">{item.label}</span>
                      {(target?.session_id ?? null) === item.sessionId && (
                        <Check size={ICON.XS} className="shrink-0 text-accent" />
                      )}
                    </button>
                  ))}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
          <footer className="flex h-[3.25rem] shrink-0 items-center justify-end gap-2 border-t border-line px-3">
            <Button variant="quiet" onClick={onClose}>Cancel</Button>
            <Button variant="primary" onClick={onSubmit} disabled={disabled}>Send</Button>
          </footer>
        </motion.div>
      </div>
    </MotionConfig>
  );
}
