import { useEffect } from "react";
import { useStore } from "@/stores";

export interface TypographyPrefs {
  uiFont: string;
  uiFontSize: number;
  fontSmoothing: boolean;
}

/** Applies the user's typography prefs to <html> as CSS custom-property
 *  overrides. An empty font string removes the override so the stylesheet
 *  default (--sans) resolves; code sizing follows automatically since
 *  --code-font-size derives from --ui-font-size in base.css. Font
 *  smoothing stamps only the non-default state, mirroring the CSS
 *  override rule. */
export function applyTypography(prefs: TypographyPrefs): void {
  const root = document.documentElement;

  if (prefs.uiFont) root.style.setProperty("--sans", prefs.uiFont);
  else root.style.removeProperty("--sans");

  root.style.setProperty("--ui-font-size", `${prefs.uiFontSize}px`);

  if (prefs.fontSmoothing) delete root.dataset.fontSmoothing;
  else root.dataset.fontSmoothing = "native";
}

/** Effect that keeps the <html> typography overrides in sync with the
 *  user's prefs. Mount once per renderer window (App, QuickCapture). */
export function useTypographyEffect(): void {
  const uiFont = useStore((s) => s.prefs.uiFont);
  const uiFontSize = useStore((s) => s.prefs.uiFontSize);
  const fontSmoothing = useStore((s) => s.prefs.fontSmoothing);

  useEffect(() => {
    applyTypography({ uiFont, uiFontSize, fontSmoothing });
  }, [uiFont, uiFontSize, fontSmoothing]);
}
