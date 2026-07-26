import { useCallback, useEffect, useRef, useState } from "react";
import { Camera, Check, ChevronDown, Plus } from "@/components/icons";
import { motion, MotionConfig } from "motion/react";
import clsx from "clsx";
import { Collapse } from "@/components/ui/Collapse";
import { Tooltip } from "@/components/ui/Tooltip";
import { IconButton } from "@/components/ui/IconButton";
import { ICON } from "@/lib/icons";
import { useThemeEffect } from "@/lib/theme";
import { useCornerProfileEffect } from "@/lib/cornerProfile";
import { useTypographyEffect } from "@/lib/typography";
import {
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

/** Shadow gutters around the card inside the transparent window —
 *  mirrors the pt-2/pb-9 padding on the root and the geometry in
 *  electron/main.cjs. The card hugs its content; a ResizeObserver
 *  requests `card height + gutters` from main, which clamps to
 *  [QUICK_BASE_HEIGHT, QUICK_MAX_HEIGHT]. */
const WINDOW_GUTTERS = 8 + 36;
/** Covers a full picker unfold in one leading resize (7 rows + padding). */
const GROWTH_HEADROOM = 240;
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
  useTypographyEffect();

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
  const cardRef = useRef<HTMLDivElement>(null);

  const items: PickerItem[] = [
    { sessionId: null, label: "New chat" },
    ...sessions.map((s) => ({ sessionId: s.session_id, label: s.name?.trim() || "Untitled chat" })),
  ];

  const closePicker = useCallback(() => setPickerOpen(false), []);

  const openPicker = useCallback(() => {
    setPickerIndex(Math.max(0, items.findIndex((i) => i.sessionId === (target?.session_id ?? null))));
    setPickerOpen(true);
  }, [items, target]);

  // The bar grows with content (multi-line drafts, image chips, the
  // picker); the window follows the card instead of hand-maintained
  // height math per state. Growth LEADS with transparent headroom — the
  // window must never clip the springing card, and oversize is invisible
  // in a transparent window — then settles to the exact height once the
  // card stops moving. Shrinks only settle, so collapse-out never clips.
  useEffect(() => {
    const card = cardRef.current;
    if (!card) return;
    let settleTimer: ReturnType<typeof setTimeout> | null = null;
    let requested = 0;
    const observer = new ResizeObserver(() => {
      const target = Math.ceil(card.getBoundingClientRect().height) + WINDOW_GUTTERS;
      if (settleTimer !== null) clearTimeout(settleTimer);
      if (target > requested) {
        requested = target + GROWTH_HEADROOM;
        void window.ardenDesktop?.quickCapture?.resize?.(requested);
      }
      settleTimer = setTimeout(() => {
        requested = target;
        void window.ardenDesktop?.quickCapture?.resize?.(target);
      }, 280);
    });
    observer.observe(card);
    return () => {
      observer.disconnect();
      if (settleTimer !== null) clearTimeout(settleTimer);
    };
  }, []);

  // Single-line at rest, grows to a cap with the draft, scrolls beyond.
  const autogrow = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 132)}px`;
  }, []);
  useEffect(() => {
    autogrow();
  }, [text, summonId, autogrow]);

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

  return (
    <MotionConfig reducedMotion="user">
      {/* Padding gives the card's drop shadow room to render inside the
          transparent BrowserWindow (24px sides, 36px below — matched to
          the window size set in electron/main.cjs). The card is a
          Spotlight-class BAR: one input row with inline accessories,
          growing only with content (draft lines, image chips, picker).
          Esc and window blur dismiss; Enter sends, ⇧↩ breaks a line. */}
      <div className="quick-capture-root grid min-h-screen items-end px-6 pt-2 pb-9">
        <motion.div
          key={summonId}
          initial={POSE_SHEET_IN}
          animate={exiting ? POSE_SHEET_OUT : POSE_SHEET_VISIBLE}
          transition={exiting ? SHEET_EXIT_TRANSITION : SHEET_ENTER_TRANSITION}
          onAnimationComplete={onCardAnimationComplete}
          className="quick-capture-card surface-sheet flex flex-col overflow-hidden rounded-[var(--r-panel)]"
        >
          {/* Bottom-anchored: the window grows UPWARD, so the picker and
              image chips render ABOVE the input row — the bar itself never
              moves on screen. */}
          <div ref={cardRef} className="flex flex-col">
            <Collapse open={pickerOpen} mode="height">
              {/* Spacing lives INSIDE the measured content (Height Collapse
                  contract) so the reveal travels with it. */}
              <div
                className="border-b border-line-soft px-1.5 py-1.5"
                role="listbox"
                aria-label="Destination chat"
              >
                {items.map((item, index) => (
                  <button
                    key={item.sessionId ?? "new"}
                    type="button"
                    role="option"
                    aria-selected={(target?.session_id ?? null) === item.sessionId}
                    tabIndex={pickerOpen ? 0 : -1}
                    onClick={() => choosePickerItem(item)}
                    onMouseEnter={() => setPickerIndex(index)}
                    className={clsx(
                      "flex h-[30px] w-full items-center gap-2 rounded-[var(--r-control)] px-2 text-left text-sm",
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
              </div>
            </Collapse>
            {images.length > 0 && (
              <div className="flex items-center gap-1.5 px-5 pt-2.5">
                {images.map((image, index) => (
                  <Tooltip key={index} label="Remove screenshot">
                    <button
                      type="button"
                      onClick={() => setImages((prev) => prev.filter((_, i) => i !== index))}
                      className="h-8 w-11 shrink-0 overflow-hidden rounded-[var(--r-tag)] ring-1 ring-ink/15"
                    >
                      <img
                        src={`data:${image.media_type};base64,${image.data}`}
                        alt=""
                        className="h-full w-full object-cover"
                      />
                    </button>
                  </Tooltip>
                ))}
              </div>
            )}
            <div className="flex min-h-14 items-center gap-2 pl-5 pr-3">
              <textarea
                ref={inputRef}
                rows={1}
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Tab" && !e.metaKey && !e.ctrlKey && !e.altKey) {
                    e.preventDefault();
                    if (pickerOpen) closePicker();
                    else openPicker();
                    return;
                  }
                  if (pickerOpen && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
                    e.preventDefault();
                    const delta = e.key === "ArrowDown" ? 1 : -1;
                    setPickerIndex((index) => (index + delta + items.length) % items.length);
                    return;
                  }
                  if (e.key !== "Enter") return;
                  // Enter captures; ⇧↩ makes a newline; ⌘↩ keeps the old chord.
                  if (e.shiftKey && !e.metaKey && !e.ctrlKey) return;
                  e.preventDefault();
                  if (pickerOpen) choosePickerItem(items[pickerIndex]);
                  else onSubmit();
                }}
                placeholder="What needs attention?"
                spellCheck={false}
                autoCorrect="off"
                autoCapitalize="off"
                readOnly={exiting}
                aria-label="Capture message"
                className="min-w-0 flex-1 resize-none self-center overflow-y-auto bg-transparent py-1 text-lg leading-normal text-ink outline-none placeholder:text-faint"
              />
              <span className="flex shrink-0 items-center gap-1 self-center">
                <IconButton
                  onClick={() => void onCapture()}
                  disabled={capturing || exiting || images.length >= MAX_IMAGES}
                  aria-label="Capture screen"
                  title="Capture a screenshot"
                  tone="faint"
                >
                  <Camera size={ICON.SM} />
                </IconButton>
                <button
                  type="button"
                  onClick={() => (pickerOpen ? closePicker() : openPicker())}
                  aria-expanded={pickerOpen}
                  aria-label="Choose destination chat"
                  className={clsx(
                    "flex h-7 max-w-44 min-w-0 items-center gap-1 rounded-[var(--r-control)] px-2.5 text-xs",
                    pickerOpen ? "bg-fill-selected text-ink" : "text-muted hover:bg-fill-hover hover:text-ink",
                  )}
                >
                  <span className="truncate">
                    {target ? target.name?.trim() || "Untitled chat" : "New chat"}
                  </span>
                  <ChevronDown size={ICON.XS} aria-hidden className="shrink-0 text-faint" />
                </button>
              </span>
            </div>
          </div>
        </motion.div>
      </div>
    </MotionConfig>
  );
}
