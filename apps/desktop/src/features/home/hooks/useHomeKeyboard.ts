import { useEffect, useRef, type MutableRefObject, type RefObject } from "react";
import { hasBlockingOverlay } from "@/lib/overlayStack";

export type HomeDeckSlot = 1 | 2 | 3;

/** The Home deck owns the concrete mutations; this hook owns only keyboard
 * routing and its global-input boundary. */
export interface HomeDeckShortcutActions {
  activateSlot: (slot: HomeDeckSlot) => void;
  toggleSnooze: () => void;
  closeTransient: () => boolean;
  skip: () => void;
  undo: () => void;
}

export interface HomeKeyboardBindings {
  captureInput: RefObject<HTMLInputElement | null>;
  deckActions: MutableRefObject<HomeDeckShortcutActions | null>;
}

function isTypingTarget(target: Element | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable
    || target.tagName === "INPUT"
    || target.tagName === "TEXTAREA"
    || target.tagName === "SELECT";
}

function isButtonTarget(target: Element | null): boolean {
  return target instanceof HTMLButtonElement;
}

/**
 * Mirrors board-home.js without leaking Home verbs into App-wide shortcuts.
 * Returns whether the caller should consume the browser event.
 */
export function dispatchHomeKeyboardShortcut(
  event: KeyboardEvent,
  { captureInput, deckActions }: HomeKeyboardBindings,
): boolean {
  if (event.defaultPrevented || event.isComposing || hasBlockingOverlay()) return false;

  const capture = captureInput.current;
  const deck = deckActions.current;
  const meta = event.metaKey || event.ctrlKey;
  const key = event.key.toLowerCase();

  // Home reserves this chord for its front door. It deliberately comes before
  // the editable-target guard, matching the mock's select-and-replace flow.
  if (meta && key === "k" && capture) {
    capture.focus();
    capture.select();
    return true;
  }

  if (event.key === "Escape") {
    if (capture && document.activeElement === capture) {
      capture.blur();
      return true;
    }
    return deck?.closeTransient() ?? false;
  }

  if (isTypingTarget(document.activeElement) || meta || event.altKey || !deck) return false;

  if (key === "1" || key === "2" || key === "3") {
    deck.activateSlot(Number(key) as HomeDeckSlot);
    return true;
  }
  if (key === "enter") {
    if (isButtonTarget(document.activeElement)) return false;
    deck.activateSlot(1);
    return true;
  }
  if (key === "h") {
    deck.toggleSnooze();
    return true;
  }
  if (key === "z") {
    deck.undo();
    return true;
  }
  if (key === "j") {
    deck.skip();
    return true;
  }
  return false;
}

/** One capture-phase listener lets Home win Cmd/Ctrl+K before the global
 * palette, while canonical overlay ownership still takes precedence. */
export function useHomeKeyboard(bindings: HomeKeyboardBindings): void {
  const bindingsRef = useRef(bindings);
  bindingsRef.current = bindings;

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!dispatchHomeKeyboardShortcut(event, bindingsRef.current)) return;
      event.preventDefault();
      event.stopImmediatePropagation();
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, []);
}
