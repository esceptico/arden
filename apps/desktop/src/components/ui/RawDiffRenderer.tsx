import { useEffect, useState, type CSSProperties } from "react";
import clsx from "clsx";
import {
  buildDiffLineGroups,
  type DiffLineGroup,
  type RawDiffRendererProps,
} from "@/components/ui/diffReviewTypes";

type ThemeStyle = CSSProperties & Record<`--${string}`, string>;
type Side = "before" | "after";

const COLLAPSE_THRESHOLD = 7;
const LEADING_CONTEXT = 1;
const TRAILING_CONTEXT = 2;

function lineCount(content: string) {
  return content === "" ? 0 : content.split("\n").length;
}

function useDarkTheme() {
  const read = () => document.documentElement.classList.contains("dark");
  const [dark, setDark] = useState(read);
  useEffect(() => {
    const observer = new MutationObserver(() => setDark(read()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return dark;
}

function RawLine({ line, number, change }: {
  line: string;
  number: number;
  change?: "removed" | "added";
}) {
  return (
    <div
      role="row"
      data-change-line={change}
      className={clsx(
        "grid min-h-[19px] grid-cols-[4ch_minmax(0,1fr)] font-mono text-xs leading-[19px]",
        change === "removed" && "bg-bad-soft",
        change === "added" && "bg-ok-soft",
      )}
    >
      <span
        role="rowheader"
        aria-hidden
        data-line-number={number}
        className="select-none border-r border-line-soft px-1 text-right tabular-nums text-faint"
      >
        {number}
      </span>
      <code
        role="cell"
        data-raw-line
        className={clsx(
          "block min-w-0 break-words px-2 text-ink",
          change === "removed" && "border-l-2 border-bad/70",
          change === "added" && "border-l-2 border-ok/70",
        )}
        style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
      >
        {line}
      </code>
    </div>
  );
}

function lineNumber(group: DiffLineGroup, side: Side, index: number) {
  return (side === "before" ? group.beforeStart : group.afterStart) + index + 1;
}

function Lines({ group, side, from = 0, to }: {
  group: DiffLineGroup;
  side: Side;
  from?: number;
  to?: number;
}) {
  const lines = side === "before" ? group.beforeLines : group.afterLines;
  const change = group.kind === "changed" ? (side === "before" ? "removed" : "added") : undefined;
  return <>{lines.slice(from, to).map((line, offset) => (
    <RawLine
      key={`${group.id}:${side}:${from + offset}`}
      line={line}
      number={lineNumber(group, side, from + offset)}
      change={change}
    />
  ))}</>;
}

function RawGroup({ group, side, expanded, onToggle }: {
  group: DiffLineGroup;
  side: Side;
  expanded: boolean;
  onToggle: () => void;
}) {
  const sideLines = side === "before" ? group.beforeLines : group.afterLines;
  if (group.kind === "changed" || sideLines.length < COLLAPSE_THRESHOLD) {
    return <Lines group={group} side={side} />;
  }
  const hidden = sideLines.length - LEADING_CONTEXT - TRAILING_CONTEXT;
  return (
    <>
      <Lines group={group} side={side} to={LEADING_CONTEXT} />
      <button
        type="button"
        aria-label={`${expanded ? "Hide" : "Show"} ${hidden} unchanged lines`}
        aria-expanded={expanded}
        onClick={onToggle}
        onKeyDown={(event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          onToggle();
        }}
        className="sticky left-0 my-1 w-full rounded-md border border-line-soft bg-surface-soft px-2 py-1 font-sans text-2xs text-muted hover:text-ink"
      >
        {expanded ? "Hide" : "Show"} {hidden} unchanged lines
      </button>
      {expanded && <Lines group={group} side={side} from={LEADING_CONTEXT} to={sideLines.length - TRAILING_CONTEXT} />}
      <Lines group={group} side={side} from={sideLines.length - TRAILING_CONTEXT} />
    </>
  );
}

function RawPane({ side, groups, expanded, onToggle }: {
  side: Side;
  groups: DiffLineGroup[];
  expanded: ReadonlySet<string>;
  onToggle: (id: string) => void;
}) {
  const label = side === "before" ? "Before raw Markdown" : "After raw Markdown";
  return (
    <section role="table" aria-label={label} className="min-w-0 bg-bg-main">
      <h2 className="sticky top-0 z-10 border-b border-line-soft bg-surface px-3 py-2 text-2xs font-semibold uppercase tracking-[0.08em] text-faint">
        {side === "before" ? "Before" : "After"}
      </h2>
      <div role="rowgroup" data-raw-diff-lines>
        {groups.map((group) => (
          <RawGroup
            key={`${side}:${group.id}`}
            group={group}
            side={side}
            expanded={expanded.has(group.id)}
            onToggle={() => onToggle(group.id)}
          />
        ))}
      </div>
    </section>
  );
}

export function RawDiffRenderer({ before, after, layout, reducedMotion = false }: RawDiffRendererProps) {
  const dark = useDarkTheme();
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const groups = buildDiffLineGroups(before.content, after.content);
  const toggle = (id: string) => setExpanded((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });
  const style: ThemeStyle = {
    "--diff-review-bg": "var(--color-bg-main)",
    "--diff-review-fg": "var(--color-ink)",
    background: "var(--color-bg-main)",
    color: "var(--color-ink)",
    userSelect: "text",
    transition: reducedMotion ? "none" : undefined,
  };
  return (
    <div
      data-raw-diff-renderer
      data-renderer="local"
      data-layout={layout}
      data-language="markdown"
      data-wrap="true"
      data-line-numbers="true"
      data-collapsed-hunks="true"
      data-motion={reducedMotion ? "reduced" : "standard"}
      data-theme={dark ? "dark" : "light"}
      data-before-lines={lineCount(before.content)}
      data-after-lines={lineCount(after.content)}
      data-before-bytes={before.content.length}
      data-after-bytes={after.content.length}
      aria-label={`Raw Markdown changes for ${after.path || before.path}`}
      className={clsx(
        "grid min-w-0 overflow-auto bg-bg-main text-ink scroll-thin",
        layout === "split" ? "grid-cols-2 divide-x divide-line-soft" : "grid-cols-1 divide-y divide-line-soft",
      )}
      style={style}
    >
      <RawPane side="before" groups={groups} expanded={expanded} onToggle={toggle} />
      <RawPane side="after" groups={groups} expanded={expanded} onToggle={toggle} />
    </div>
  );
}
