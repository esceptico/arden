import type { ActivityItem } from "@/stores";

/** Semantic icon key for a step — mapped to a lucide glyph in the trace row. */
export type StepIconKey =
  | "search"
  | "globe"
  | "file"
  | "edit"
  | "file-plus"
  | "folder"
  | "terminal"
  | "brain"
  | "book"
  | "list"
  | "mail"
  | "slack"
  | "calendar"
  | "clock"
  | "bell"
  | "image"
  | "wrench"
  | "history"
  | "dot";

export interface OperationLabel {
  /** Natural-language, corpus-specific operation, e.g. "Searched email". */
  verb: string;
  /** The object of the operation (a path / query / command), or null. */
  detail: string | null;
  /** Category icon for the step. */
  iconKey: StepIconKey;
  /** Singular unit for grouping summaries ("file" → "Read 4 files"), or null. */
  noun: string | null;
}

interface ToolMeta {
  verb: string;
  icon: StepIconKey;
  noun?: string;
}

// Curated registry for the tools a user actually sees, keyed by the exact tool
// name the server sends (apps/server/arden/integrations). Labels name the CORPUS
// so "Searched email" / "Searched Slack" / "Searched the web" are unambiguous.
// `noun` drives the grouped summary ("Read 4 files") and is only valid when one
// CALL is one unit — search tools get none (their unit counts results, so a run
// of N calls would miscount or stutter: "Searched 4 searches"). The long tail
// falls back to a category icon (PREFIX_ICON) + the server display_name, humanized.
const TOOL_META: Record<string, ToolMeta> = {
  // System / files
  file_read: { verb: "Read", icon: "file", noun: "file" },
  file_write: { verb: "Wrote", icon: "file-plus", noun: "file" },
  file_edit: { verb: "Edited", icon: "edit", noun: "file" },
  file_list: { verb: "Listed files", icon: "folder" },
  file_find: { verb: "Found files", icon: "search" },
  file_search_text: { verb: "Searched code", icon: "search" },
  bash: { verb: "Ran", icon: "terminal", noun: "command" },
  current_time: { verb: "Checked the time", icon: "clock" },
  render_html: { verb: "Rendered a view", icon: "image" },
  load_tools: { verb: "Loaded tools", icon: "wrench" },
  tool_search: { verb: "Searched tools", icon: "search" },
  notify: { verb: "Notified you", icon: "bell" },
  todo_update: { verb: "Updated the plan", icon: "list" },

  // Web
  web_search: { verb: "Searched the web", icon: "globe" },
  web_fetch: { verb: "Fetched a page", icon: "globe", noun: "page" },

  // Gmail
  email_search: { verb: "Searched email", icon: "mail" },
  email_read: { verb: "Read an email", icon: "mail", noun: "email" },
  email_send: { verb: "Sent an email", icon: "mail" },
  email_reply: { verb: "Replied to an email", icon: "mail" },

  // Slack
  slack_search: { verb: "Searched Slack", icon: "slack" },
  slack_channel: { verb: "Read a Slack channel", icon: "slack" },
  slack_channels: { verb: "Listed Slack channels", icon: "slack" },
  slack_dm: { verb: "Read a Slack DM", icon: "slack" },
  slack_dms: { verb: "Listed Slack DMs", icon: "slack" },
  slack_thread: { verb: "Read a Slack thread", icon: "slack" },
  slack_user: { verb: "Looked up a Slack user", icon: "slack" },
  slack_users: { verb: "Searched Slack users", icon: "slack" },
  slack_file: { verb: "Fetched a Slack file", icon: "image" },
  slack_post_message: { verb: "Posted to Slack", icon: "slack" },
  slack_post_blocks: { verb: "Posted to Slack", icon: "slack" },

  // Calendar
  calendar_search: { verb: "Checked the calendar", icon: "calendar" },
  calendar_create_event: { verb: "Created an event", icon: "calendar" },
  calendar_edit_event: { verb: "Edited an event", icon: "calendar" },
  calendar_delete_event: { verb: "Deleted an event", icon: "calendar" },

  // Memory
  fact_search: { verb: "Searched memory", icon: "brain" },
  fact_get: { verb: "Recalled a fact", icon: "brain" },
  fact_history: { verb: "Read fact history", icon: "brain" },
  fact_plan_changes: { verb: "Planned memory changes", icon: "brain" },
  fact_commit_changes: { verb: "Updated memory", icon: "brain" },
  wiki_read_page: { verb: "Read a page", icon: "book" },
  wiki_list_pages: { verb: "Listed pages", icon: "book" },

  // Sessions
  session_search_transcripts: { verb: "Searched transcripts", icon: "history" },
  session_read: { verb: "Read a session", icon: "history" },
  session_list: { verb: "Listed sessions", icon: "history" },
};

// Category by tool-name shape, for the long tail not in TOOL_META.
const PREFIX_ICON: ReadonlyArray<readonly [RegExp, StepIconKey]> = [
  [/^slack_/, "slack"],
  [/^calendar_/, "calendar"],
  [/^fact_/, "brain"],
  [/^wiki_/, "book"],
  [/^web_/, "globe"],
  [/^research/, "brain"],
  [/^email_/, "mail"],
  [/^session_/, "history"],
  [/^automation_|^loop_|cron/, "clock"],
  [/^skill_/, "wrench"],
  [/notif/, "bell"],
  [/^todo_|^goal_|^directives_/, "list"],
];

// Last-resort verb heuristic (user/MCP tools with no useful display_name).
const VERB_RULES: ReadonlyArray<readonly [RegExp, string, StepIconKey]> = [
  [/\bsearch\b|\bgrep\b|\bfind\b/, "Searched", "search"],
  [/\bfetch\b|\bcurl\b|\bhttp\b|\bget\b/, "Fetched", "globe"],
  [/\bread\b|\bcat\b|\bview\b/, "Read", "file"],
  [/\bwrite\b|\bcreate\b|\bsave\b/, "Wrote", "file-plus"],
  [/\bedit\b|\breplace\b|\bpatch\b|\bupdate\b/, "Edited", "edit"],
  [/\blist\b|\bls\b/, "Listed", "folder"],
  [/\brun\b|\bexec\b|\bbash\b/, "Ran", "terminal"],
];

const DETAIL_KEYS = [
  "path", "file_path", "filename", "file", "query", "q", "command", "cmd",
  "url", "pattern", "name", "tools", "prompt", "task", "channel", "id",
] as const;

const MAX_DETAIL = 64;

function truncate(s: string): string {
  const t = s.trim().replace(/\s+/g, " ");
  return t.length > MAX_DETAIL ? `${t.slice(0, MAX_DETAIL - 1)}…` : t;
}

function parseArgs(args: string | undefined): Record<string, unknown> | null {
  if (!args) return null;
  try {
    const parsed = JSON.parse(args);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null; // partial JSON mid-stream; detail/sources fill in once it lands.
  }
}

function detailFromArgs(obj: Record<string, unknown> | null): string | null {
  if (!obj) return null;
  for (const key of DETAIL_KEYS) {
    const v = obj[key];
    if (typeof v === "string" && v.trim()) return truncate(v);
    if (Array.isArray(v) && v.every((item) => typeof item === "string")) return truncate(v.join(", "));
  }
  return null;
}

// "SlackDMs" → "Slack DMs", "ListAutomations" → "List automations" (camelCase
// split + separator clean; acronym runs preserved, first letter capitalized).
function humanize(s: string | undefined): string | null {
  if (!s) return null;
  const spaced = s
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .trim();
  if (!spaced) return null;
  return spaced[0].toUpperCase() + spaced.slice(1);
}

const ICON_KEYS: ReadonlySet<string> = new Set([
  "search", "globe", "file", "edit", "file-plus", "folder", "terminal", "brain", "book",
  "list", "mail", "slack", "calendar", "clock", "bell", "image", "wrench", "history", "dot",
]);

function asIconKey(s: string | undefined): StepIconKey | null {
  return s && ICON_KEYS.has(s) ? (s as StepIconKey) : null;
}

/** The model's optional per-call action title. Projection extracts the one
 * canonical `_display_title` metadata argument before behavior args render. */
export function callTitle(item: ActivityItem): string | null {
  const t = item.displayTitle;
  return typeof t === "string" && t.trim() ? truncate(t) : null;
}

/** Stable per-KIND label — meta/heuristic only, no per-call title, no args.
 *  Used as the fallback verb and for grouped summaries (which must stay
 *  consistent across a run of differently-titled calls). */
function kindLabel(item: ActivityItem): { verb: string; iconKey: StepIconKey; noun: string | null } {
  const name = (item.kind ?? "").toLowerCase();
  const meta = TOOL_META[name];
  const norm = name.replace(/[^a-z0-9]+/g, " ").trim();
  const heuristic = VERB_RULES.find(([re]) => re.test(norm));
  const verb = meta?.verb ?? humanize(item.displayName) ?? heuristic?.[1] ?? humanize(item.kind) ?? "Tool";
  const iconKey =
    asIconKey(item.icon) ??
    meta?.icon ??
    PREFIX_ICON.find(([re]) => re.test(name))?.[1] ??
    heuristic?.[2] ??
    "dot";
  const noun = item.noun ?? meta?.noun ?? null;
  return { verb, iconKey, noun };
}

/** Map a tool activity item to its label + category icon. Agents carry their
 *  own friendly name and never reach this.
 *
 *  Label priority: the MODEL's action title (richest, e.g. "Searching for
 *  profiles") → the curated/heuristic kind verb. Icon + grouping noun prefer
 *  the backend's rendering hints (tool_presentation); the client registry is
 *  the fallback for history reload / uncategorized tools. */
export function operationLabel(item: ActivityItem): OperationLabel {
  const args = parseArgs(item.args);
  const base = kindLabel(item);
  const title = callTitle(item);
  return { verb: title ?? base.verb, detail: detailFromArgs(args), iconKey: base.iconKey, noun: base.noun };
}

function plural(noun: string, n: number): string {
  if (n === 1) return noun;
  return /(s|x|ch|sh)$/.test(noun) ? `${noun}es` : `${noun}s`;
}

/** Summary label + icon for a collapsed run of same-kind calls, e.g.
 *  "Read 8 files" / "Searched the web · 3". Uses the stable per-kind label
 *  (not the per-call model titles, which differ across the run). */
export function groupSummary(items: ActivityItem[]): { verb: string; iconKey: StepIconKey } {
  const { verb, iconKey, noun } = kindLabel(items[0]);
  const n = items.length;
  if (noun) {
    const action = verb.split(" ")[0]; // "Read" / "Searched" / "Fetched"
    return { verb: `${action} ${n} ${plural(noun, n)}`, iconKey };
  }
  return { verb: `${verb} · ${n}`, iconKey };
}
