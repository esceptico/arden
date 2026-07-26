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

/** The capture field's own router-sheet state. `onEscape` reports whether it
 *  consumed the key (closed the sheet, then cleared the text) — false means
 *  there is nothing sheet-side to do, so the caller falls through to its own
 *  default (blur the field). */
export interface HomeOmniboxShortcutActions {
  onEscape: () => boolean;
}

export interface HomeKeyboardBindings {
  captureInput: RefObject<HTMLInputElement | null>;
  deckActions: MutableRefObject<HomeDeckShortcutActions | null>;
  omnibox?: MutableRefObject<HomeOmniboxShortcutActions | null>;
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
  { captureInput, deckActions, omnibox }: HomeKeyboardBindings,
): boolean {
  if (event.defaultPrevented || event.isComposing || hasBlockingOverlay()) return false;

  const capture = captureInput.current;
  const deck = deckActions.current;
  const meta = event.metaKey || event.ctrlKey;
  const key = event.key.toLowerCase();

  // On Home the chord summons the router FIELD, not the floating palette:
  // both run the same entry brain, so the palette would be a redundant
  // clone hovering over its own twin. Same intent as everywhere else —
  // "search/command" — in the surface-appropriate body. Pressing it again
  // while focused releases the focus (and the veil with it).
  if (meta && key === "k" && capture) {
    if (document.activeElement === capture) {
      capture.blur();
    } else {
      capture.focus();
      capture.select();
    }
    return true;
  }

  if (event.key === "Escape") {
    if (capture && document.activeElement === capture) {
      // The router sheet gets first say: close-then-clear before the field
      // itself blurs. An empty field has nothing for it to do, so it falls
      // through to the original blur.
      if (omnibox?.current?.onEscape()) return true;
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

/** One capture-phase listener for Home's deck verbs and the router's
 * Escape staging; canonical overlay ownership still takes precedence. */
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
