import type { ArdenIcon } from "@/components/icons";

/** A palette entry. Either an executable leaf (`run`) or a folder
 *  (`children`) that opens a sub-view via breadcrumb drill-down. */
export interface CommandEntry {
  id: string;
  section: "suggested" | "open" | "session" | "provider" | "model" | "appearance" | "system";
  label: string;
  hint?: string;
  shortcut?: string;
  icon: ArdenIcon;
  /** Leaf action. Mutually exclusive with `children`. */
  run?: () => void | Promise<void>;
  /** Folder. Returning a view defers entries until drilled into. */
  children?: () => CommandView;
  /** Lower-cased haystack used for fuzzy matching. */
  search: string;
}

/** One level of the drill-down tree. `placeholder` swaps the input
 *  placeholder so the user knows what to type for. The crumb chip
 *  itself reuses the parent entry's label — no separate copy. */
export interface CommandView {
  placeholder: string;
  entries: CommandEntry[];
}

export interface Crumb {
  id: string;
  label: string;
}

export const SECTION_LABEL: Record<CommandEntry["section"], string> = {
  suggested: "Suggested",
  open: "Navigation",
  appearance: "Appearance",
  session: "Sessions",
  system: "System",
  provider: "Providers",
  model: "Models",
};

export const SECTION_ORDER: CommandEntry["section"][] = [
  "suggested",
  "open",
  "appearance",
  "provider",
  "model",
  "session",
  "system",
];
