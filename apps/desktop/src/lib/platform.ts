/** True when running inside the native (Electron) desktop shell on macOS,
 *  where the OS draws the traffic-light window controls in the top-left inset.
 *  In a plain browser tab there is no native chrome, so this is false. */
export const IS_DESKTOP_MAC =
  typeof window !== "undefined" &&
  !!window.ardenDesktop &&
  typeof navigator !== "undefined" &&
  navigator.platform.toUpperCase().includes("MAC");
