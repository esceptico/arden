import { useEffect } from "react";
import { useStore } from "@/stores";

export interface TypographyPrefs {
  uiFont: string;
  uiFontSize: number;
  fontSmoothing: boolean;
}

const DEFAULT_UI_FONT_SIZE = 14;
const MIN_GEOMETRY_FONT_SIZE = 12;

/** Applies the user's typography prefs to <html>. The explicit px token sizes
 * text; the proportional root size scales rem-based rows, gaps, icons, and
 * controls with it, so accessibility sizing does not strand larger glyphs in
 * frozen geometry. */
export function applyTypography(prefs: TypographyPrefs): void {
  const root = document.documentElement;

  if (prefs.uiFont) root.style.setProperty("--sans", prefs.uiFont);
  else root.style.removeProperty("--sans");

  root.style.setProperty("--ui-font-size", `${prefs.uiFontSize}px`);
  const geometryFontSize = Math.max(prefs.uiFontSize, MIN_GEOMETRY_FONT_SIZE);
  root.style.fontSize = `${(geometryFontSize / DEFAULT_UI_FONT_SIZE) * 100}%`;

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
