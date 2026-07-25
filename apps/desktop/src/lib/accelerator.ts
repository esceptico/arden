/** Helpers for working with Electron accelerator strings (the format
 *  used by `globalShortcut.register`, e.g. "CommandOrControl+Shift+Space").
 *
 *  Two responsibilities:
 *  - Turning a DOM KeyboardEvent into an accelerator string we can ship
 *    to the main process for registration.
 *  - Turning that accelerator back into a pretty chord for display (⌘⇧Space).
 */

const MAC = typeof navigator !== "undefined" && navigator.platform.toUpperCase().includes("MAC");

// Map DOM `event.key` values to the names Electron expects in
// accelerator strings. Most letters/digits pass through unchanged;
// special keys need explicit mapping.
const KEY_NAME_FOR_EVENT_KEY: Record<string, string> = {
  " ": "Space",
  Spacebar: "Space",
  ArrowUp: "Up",
  ArrowDown: "Down",
  ArrowLeft: "Left",
  ArrowRight: "Right",
  Enter: "Return",
  Escape: "Escape",
  Backspace: "Backspace",
  Tab: "Tab",
  Delete: "Delete",
  Insert: "Insert",
  PageUp: "PageUp",
  PageDown: "PageDown",
  Home: "Home",
  End: "End",
  "+": "Plus",
};

/** Build an Electron accelerator from a KeyboardEvent, or return null
 *  if the chord isn't valid for a global shortcut. Rules:
 *  - Must have at least one non-shift modifier (Cmd/Ctrl/Alt) — the OS
 *    won't register plain letters / Shift-only chords globally.
 *  - The non-modifier key must be present (modifier-only chords are
 *    meaningless — you'd trigger every time you held the key down). */
export function eventToAccelerator(event: KeyboardEvent): string | null {
  const k = event.key;
  // Ignore modifier-only presses; we wait for a "real" key to complete
  // the chord. Otherwise typing ⌘ would record an empty accelerator.
  if (k === "Control" || k === "Meta" || k === "Alt" || k === "Shift") return null;

  const mods: string[] = [];
  // Use the cross-platform alias on macOS/Windows so users get the
  // right primary modifier. Standalone Control on macOS is also valid
  // (e.g. for chords that should ONLY use ⌃), but rare — we treat
  // ⌘ on mac and Ctrl on win/linux as the canonical primary.
  if (event.metaKey && MAC) mods.push("CommandOrControl");
  else if (event.ctrlKey && !MAC) mods.push("CommandOrControl");
  else if (event.ctrlKey) mods.push("Control");
  if (event.altKey) mods.push("Alt");
  if (event.shiftKey) mods.push("Shift");

  if (mods.length === 0) return null;

  let keyName = KEY_NAME_FOR_EVENT_KEY[k];
  if (!keyName) {
    if (k.length === 1) {
      // Letters: uppercase regardless of shift state. Digits: as typed
      // (`event.code` would give "Digit1"; we want "1").
      keyName = /[a-z]/i.test(k) ? k.toUpperCase() : k;
    } else if (/^F\d+$/.test(k)) {
      keyName = k; // F1..F24 pass through
    } else {
      return null;
    }
  }

  return [...mods, keyName].join("+");
}

/** Turn an accelerator back into a pretty chord with platform-correct
 *  symbols. macOS uses ⌘⌥⌃⇧ glyphs; Windows/Linux uses the words
 *  separated by + so the chord stays scannable. */
export function formatAccelerator(accelerator: string): string {
  if (!accelerator) return "Disabled";
  const parts = accelerator.split("+");
  if (MAC) {
    const sym: Record<string, string> = {
      CommandOrControl: "⌘",
      CmdOrCtrl: "⌘",
      Command: "⌘",
      Cmd: "⌘",
      Control: "⌃",
      Ctrl: "⌃",
      Alt: "⌥",
      Option: "⌥",
      Shift: "⇧",
      Return: "↩",
      Enter: "↩",
      Escape: "⎋",
      Backspace: "⌫",
      Delete: "⌦",
      Tab: "⇥",
      Up: "↑",
      Down: "↓",
      Left: "←",
      Right: "→",
    };
    return parts.map((p) => sym[p] ?? p).join("");
  }
  return parts.join(" + ");
}

/** Split the display form into individual keycaps without duplicating the
 * platform mapping. macOS modifiers are adjacent glyphs; other platforms use
 * the existing spaced `+` separator. */
export function formatAcceleratorParts(accelerator: string): string[] {
  const formatted = formatAccelerator(accelerator);
  if (formatted.includes(" + ")) return formatted.split(" + ");

  const parts: string[] = [];
  let key = "";
  for (const character of formatted) {
    if ("⌘⌥⌃⇧".includes(character)) {
      if (key) parts.push(key);
      parts.push(character);
      key = "";
    } else {
      key += character;
    }
  }
  if (key) parts.push(key);
  return parts;
}
