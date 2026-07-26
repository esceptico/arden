import { SECTION_ORDER, type CommandEntry } from "@/lib/commandEntries/types";

export function prettyProvider(provider: string): string {
  if (!provider) return "Unknown";
  if (provider === "openai") return "OpenAI";
  return provider.charAt(0).toUpperCase() + provider.slice(1);
}

export function stripProviderPrefix(model: string, provider: string): string {
  const prefix = `${provider}/`;
  return model.startsWith(prefix) ? model.slice(prefix.length) : model;
}

export function lastAssistantId(
  order: string[],
  messages: Map<string, { role: string }>,
): string | null {
  for (let i = order.length - 1; i >= 0; i--) {
    const id = order[i];
    if (messages.get(id)?.role === "assistant") return id;
  }
  return null;
}

export function filterEntries(entries: CommandEntry[], query: string): CommandEntry[] {
  const q = query.trim().toLowerCase();
  let filtered: CommandEntry[];
  if (!q) {
    // No query → show actions and open targets first, then a few recent
    // sessions. Keeps the default view useful as a "what can I do" list.
    // Areas/automations only earn a spot once the user actually searches —
    // unsliced, they could flood this view for a workspace with many of
    // either, and Home's own room already lists them ambiently.
    const HIDDEN_BY_DEFAULT = new Set(["session", "area", "automation"]);
    const acts = entries.filter((e) => !HIDDEN_BY_DEFAULT.has(e.section));
    const sess = entries.filter((e) => e.section === "session").slice(0, 6);
    filtered = [...acts, ...sess];
  } else {
    const tokens = q.split(/\s+/);
    filtered = entries.filter((e) => tokens.every((t) => e.search.includes(t)));
  }
  // Sort by section so the iteration order matches the rendered order.
  // groupBySection renders sections in SECTION_ORDER; without sorting,
  // arrow-down on the last "suggested" row could land on an "open" entry
  // that visually sits two sections lower because it was pushed earlier.
  // Stable sort preserves insertion order within each section.
  const sectionRank = new Map(SECTION_ORDER.map((s, i) => [s, i]));
  return filtered
    .map((entry, i) => ({ entry, i }))
    .sort((a, b) => {
      const sa = sectionRank.get(a.entry.section) ?? SECTION_ORDER.length;
      const sb = sectionRank.get(b.entry.section) ?? SECTION_ORDER.length;
      return sa - sb || a.i - b.i;
    })
    .map(({ entry }) => entry);
}

export function groupBySection(entries: CommandEntry[]): {
  section: CommandEntry["section"];
  items: CommandEntry[];
}[] {
  const buckets = new Map<CommandEntry["section"], CommandEntry[]>();
  for (const e of entries) {
    const arr = buckets.get(e.section) ?? [];
    arr.push(e);
    buckets.set(e.section, arr);
  }
  const out: { section: CommandEntry["section"]; items: CommandEntry[] }[] = [];
  for (const section of SECTION_ORDER) {
    const items = buckets.get(section);
    if (items && items.length > 0) out.push({ section, items });
  }
  return out;
}
