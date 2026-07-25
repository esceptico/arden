import { useEffect } from "react";
import { useStore, type CornerProfile } from "@/stores";

/** The Settings "Corner profile" radius toggle, wired to a real,
 *  persisted preference. Overrides the
 *  three switchable primitives on <html> — every token that derives from them
 *  (--r-row, --r-icon, --r-cluster, --r-outer, --r-panel, --r-dense-row,
 *  --surface-radius, …) follows for free, no per-component wiring needed. */
function applyCornerProfile(profile: CornerProfile): void {
  const root = document.documentElement;
  const square = profile === "square";

  root.classList.add("radius-transitioning");
  void root.offsetHeight; // flush so the transition below applies to this change

  root.style.setProperty("--r-control", square ? "var(--r-square)" : "var(--r-pill)");
  root.style.setProperty("--r-shell", square ? "var(--r-shell-square)" : "var(--r-shell-round)");
  root.style.setProperty("--r-menu", square ? "var(--r-menu-square)" : "var(--r-menu-round)");

  window.setTimeout(() => root.classList.remove("radius-transitioning"), 200);
}

/** Effect that keeps the <html> corner-radius overrides in sync with the
 *  user's prefs. Mount once per renderer window (App, QuickCapture). */
export function useCornerProfileEffect(): void {
  const cornerProfile = useStore((s) => s.prefs.cornerProfile);

  useEffect(() => {
    applyCornerProfile(cornerProfile);
  }, [cornerProfile]);
}
