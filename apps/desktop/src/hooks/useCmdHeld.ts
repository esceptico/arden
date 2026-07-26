import { useEffect, useState } from "react";

const HOLD_MS = 600;

/** True while the Command key has been held alone for a beat — the
 *  "show me the shortcuts" gesture. A chord (⌘ plus any other key)
 *  cancels the pending reveal and hides an active one; so do releasing
 *  ⌘ and leaving the window (⌘Tab). */
export function useCmdHeld(): boolean {
  const [held, setHeld] = useState(false);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;

    const clear = () => {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      setHeld(false);
    };

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Meta") {
        if (event.repeat || timer !== null) return;
        timer = setTimeout(() => {
          timer = null;
          setHeld(true);
        }, HOLD_MS);
        return;
      }
      clear();
    };

    const onKeyUp = (event: KeyboardEvent) => {
      if (event.key === "Meta") clear();
    };

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", clear);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", clear);
      if (timer !== null) clearTimeout(timer);
    };
  }, []);

  return held;
}
