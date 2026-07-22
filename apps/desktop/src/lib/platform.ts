import { useEffect, useState } from "react";

/** True when running inside the native (Electron) desktop shell on macOS,
 *  where the OS draws the traffic-light window controls in the top-left inset.
 *  In a plain browser tab there is no native chrome, so this is false. */
export const IS_DESKTOP_MAC =
  typeof window !== "undefined" &&
  !!window.ardenDesktop &&
  typeof navigator !== "undefined" &&
  navigator.platform.toUpperCase().includes("MAC");

/** Whether the macOS traffic-light controls currently occupy the top-left
 *  inset. False in the browser (no native chrome) and in native fullscreen
 *  (macOS hides the lights). Reactive to fullscreen changes. */
export function useHasTrafficLights(): boolean {
  const [fullscreen, setFullscreen] = useState(false);
  useEffect(() => {
    if (!IS_DESKTOP_MAC) return;
    void window.ardenDesktop?.window?.isFullScreen?.().then(setFullscreen);
    return window.ardenDesktop?.window?.onFullScreenChange?.(setFullscreen);
  }, []);
  return IS_DESKTOP_MAC && !fullscreen;
}
