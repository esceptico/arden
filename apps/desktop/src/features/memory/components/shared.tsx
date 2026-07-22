import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Calendar, List, Text } from "@/components/icons";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";

// ─── Display helpers ──────────────────────────────────────────────────

/** Relative-time string for a freshness / recency stamp. Null-safe. */
export function relativeTime(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diff = Date.now() - then;
  const m = Math.round(diff / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d}d ago`;
  const mo = Math.round(d / 30);
  if (mo < 12) return `${mo}mo ago`;
  return `${Math.round(mo / 12)}y ago`;
}

export function DetailMeta({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-center flex-wrap gap-2 text-xs text-faint">{children}</div>
  );
}

export function Sep() {
  return (
    <span aria-hidden className="text-line">
      ·
    </span>
  );
}

// ─── Obsidian-style frontmatter properties ────────────────────────────

type FmValue = string | number | boolean | null | Array<string | number | boolean | null>;

// Internal/opaque bookkeeping a human shouldn't see as a property: the title is the
// page header, scope_key is an opaque id, aliases mirror the title, type is folder-derived,
// prose_* are synthesis telemetry. What's left is meaningful (updated + the label tags).
const _HIDE_PROPS = new Set([
  "title", "labels", "type", "aliases", "scope_key", "prose_synced", "prose_tokens", "prose_cites",
]);
const _DATE_KEYS = new Set(["created", "updated", "prose_synced", "last_updated_at", "date"]);

function humanizeKey(k: string): string {
  return k.replace(/_/g, " ");
}
function fmtDate(v: string): string {
  const m = v.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : v;
}

/** Render a page's YAML frontmatter as Obsidian-style typed properties (text / date /
 *  list-of-tags), each with an icon. Hides internal bookkeeping keys. */
export function Properties({ frontmatter }: { frontmatter?: Record<string, FmValue> }) {
  const entries = Object.entries(frontmatter ?? {}).filter(
    ([k, v]) => !_HIDE_PROPS.has(k) && v != null && v !== "" && !(Array.isArray(v) && v.length === 0),
  );
  if (entries.length === 0) return null;
  return (
    <div>
      <dl className="flex flex-col gap-1.5">
        {entries.map(([key, value]) => {
          const isList = Array.isArray(value);
          const isDate = !isList && _DATE_KEYS.has(key) && typeof value === "string" && /^\d{4}-\d{2}-\d{2}/.test(value);
          const Icon = isList ? List : isDate ? Calendar : Text;
          return (
            <div key={key} className="grid grid-cols-[96px_minmax(0,1fr)] items-start gap-2 text-xs">
              <dt className="flex items-center gap-1.5 pt-0.5 text-muted">
                <Icon className="h-3.5 w-3.5 shrink-0 text-faint" strokeWidth={2} />
                <span className="truncate">{humanizeKey(key)}</span>
              </dt>
              <dd className="min-w-0 text-ink-soft">
                {isList ? (
                  <div className="flex flex-wrap gap-1">
                    {(value as Array<string | number | boolean | null>).map((v, i) => (
                      <Pill key={`${String(v)}-${i}`} tone="neutral">{String(v)}</Pill>
                    ))}
                  </div>
                ) : isDate ? (
                  <span className="inline-flex items-center gap-1.5 tabular-nums">{fmtDate(value as string)}</span>
                ) : (
                  <span className="break-words">{String(value)}</span>
                )}
              </dd>
            </div>
          );
        })}
      </dl>
    </div>
  );
}

// ─── Buttons ──────────────────────────────────────────────────────────

export function PrimaryBtn({
  children,
  onClick,
  disabled,
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <Button variant="primary" size="sm" onClick={onClick} disabled={disabled}>
      {children}
    </Button>
  );
}

export function GhostBtn({
  children,
  onClick,
  disabled,
  ...buttonProps
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type" | "onClick" | "disabled" | "className">) {
  return (
    <Button {...buttonProps} variant="ghost" size="sm" onClick={onClick} disabled={disabled}>
      {children}
    </Button>
  );
}

export function DangerBtn({
  children,
  onClick,
  disabled,
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <Button variant="danger" size="sm" onClick={onClick} disabled={disabled}>
      {children}
    </Button>
  );
}
