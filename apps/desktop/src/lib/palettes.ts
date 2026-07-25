/** Selectable accent palettes. Only the accent hue changes — surfaces,
 *  text, status, and syntax stay neutral (owned by the greyscale palette
 *  block in styles.css). Each palette carries a light + dark accent (and a
 *  deeper `strong` step for hover/press); `soft` and selection tints are
 *  derived at apply time so the data stays to two hexes per theme. */
export interface AccentPalette {
  id: string;
  name: string;
  light: { accent: string; strong: string };
  dark: { accent: string; strong: string };
}

export const ACCENT_PALETTES: AccentPalette[] = [
  { id: "mono",   name: "Mono",   light: { accent: "#171717", strong: "#000000" }, dark: { accent: "#ededed", strong: "#ffffff" } },
  { id: "slate",  name: "Slate",  light: { accent: "#475569", strong: "#334155" }, dark: { accent: "#94a3b8", strong: "#cbd5e1" } },
  { id: "blue",   name: "Blue",   light: { accent: "#0070f3", strong: "#0060d1" }, dark: { accent: "#3291ff", strong: "#5aa9ff" } },
  { id: "indigo", name: "Indigo", light: { accent: "#4f46e5", strong: "#3730a3" }, dark: { accent: "#818cf8", strong: "#a5b4fc" } },
  { id: "ocean",  name: "Ocean",  light: { accent: "#006494", strong: "#003554" }, dark: { accent: "#00a6fb", strong: "#5cc2ff" } },
  { id: "teal",   name: "Teal",   light: { accent: "#0a7d77", strong: "#0a6560" }, dark: { accent: "#68d8d6", strong: "#9ceaef" } },
  { id: "forest", name: "Forest", light: { accent: "#3f5a41", strong: "#2b3f2d" }, dark: { accent: "#8fb996", strong: "#a1cca5" } },
  { id: "sage",   name: "Sage",   light: { accent: "#718355", strong: "#5f6f45" }, dark: { accent: "#a7b98a", strong: "#cfe1b9" } },
  { id: "coffee", name: "Coffee", light: { accent: "#7f5539", strong: "#634230" }, dark: { accent: "#d3a983", strong: "#e6ccb2" } },
  { id: "rose",   name: "Rose",   light: { accent: "#c33862", strong: "#a32d52" }, dark: { accent: "#ff8fab", strong: "#ffb3c6" } },
];

export const DEFAULT_ACCENT = "slate";

const STYLE_ID = "arden-accent";
const mix = (hex: string, pct: number) => `color-mix(in srgb, ${hex} ${pct}%, transparent)`;

/** Inject the chosen palette into the canonical Board accent tokens. The
 *  compatibility `--color-accent*` aliases resolve from these values. */
export function applyAccentPalette(id: string): void {
  const p =
    ACCENT_PALETTES.find((x) => x.id === id) ??
    ACCENT_PALETTES.find((x) => x.id === DEFAULT_ACCENT) ??
    ACCENT_PALETTES[0];
  const css =
    `:root:root{--accent:${p.light.accent};--accent-soft:${mix(p.light.accent, 13)};` +
    `--accent-ink:${p.light.strong};}` +
    `:root.dark:root{--accent:${p.dark.accent};--accent-soft:${mix(p.dark.accent, 16)};` +
    `--accent-ink:${p.dark.strong};}`;
  let el = document.getElementById(STYLE_ID) as HTMLStyleElement | null;
  if (!el) {
    el = document.createElement("style");
    el.id = STYLE_ID;
    document.head.appendChild(el);
  }
  el.textContent = css;
}
