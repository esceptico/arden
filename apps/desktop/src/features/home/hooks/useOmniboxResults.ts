import { useMemo } from "react";
import { useEntries } from "@/hooks/useEntries";
import { filterEntries } from "@/lib/commandEntries/filter";
import type { CommandEntry } from "@/lib/commandEntries/types";

export type OmniboxGroupKey = "chats" | "areas" | "automations" | "actions";

const GROUP_LABEL: Record<OmniboxGroupKey, string> = {
  chats: "Chats",
  areas: "Areas",
  automations: "Automations",
  actions: "Actions",
};

const GROUP_ORDER: OmniboxGroupKey[] = ["chats", "areas", "automations", "actions"];

/** Hard cap so the docked sheet stays a tiny list, never a second palette. */
const GROUP_CAP = 2;

function bucketOf(entry: CommandEntry): OmniboxGroupKey {
  if (entry.section === "session") return "chats";
  if (entry.section === "area") return "areas";
  if (entry.section === "automation") return "automations";
  return "actions";
}

export interface OmniboxGroup {
  key: OmniboxGroupKey;
  label: string;
  items: CommandEntry[];
}

/**
 * Home's router door onto the same entry machinery the cmd+K palette uses —
 * `useEntries` for the candidate list, `filterEntries` for the ranked fuzzy
 * match. Only the presentation differs: capped, grouped, and docked instead
 * of a floating unbounded list. Folder entries (drill-down "children") have
 * nowhere to drill into here, so they're excluded — the sheet only ever
 * shows things it can actually open.
 */
export function useOmniboxResults(query: string): { groups: OmniboxGroup[]; flat: CommandEntry[] } {
  const entries = useEntries();
  return useMemo(() => {
    if (!query.trim()) return { groups: [], flat: [] };
    const filtered = filterEntries(entries, query).filter((entry) => entry.run);
    const buckets = new Map<OmniboxGroupKey, CommandEntry[]>();
    for (const entry of filtered) {
      const key = bucketOf(entry);
      const items = buckets.get(key) ?? [];
      if (items.length < GROUP_CAP) {
        items.push(entry);
        buckets.set(key, items);
      }
    }
    const groups = GROUP_ORDER
      .map((key) => ({ key, label: GROUP_LABEL[key], items: buckets.get(key) ?? [] }))
      .filter((group) => group.items.length > 0);
    return { groups, flat: groups.flatMap((group) => group.items) };
  }, [entries, query]);
}
