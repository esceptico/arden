import { useEffect, useState, type CSSProperties } from "react";
import clsx from "clsx";
import {
  buildDiffLineGroups,
  type DiffLineGroup,
  type RawDiffRendererProps,
} from "@/components/ui/diffReviewTypes";

type ThemeStyle = CSSProperties & Record<`--${string}`, string>;
type Side = "before" | "after";
type Change = "removed" | "added";

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

function RawCell({
  side,
  line,
  number,
  change,
}: {
  side: Side;
  line?: string;
  number?: number;
  change?: Change;
}) {
  const placeholder = line === undefined;
  return (
    <div
      role="cell"
      data-side={side}
      data-change-line={change}
      data-placeholder={placeholder ? side : undefined}
      aria-label={placeholder ? `No ${side} line` : undefined}
      className={clsx(
        "grid h-full min-h-[19px] min-w-0 grid-cols-[4ch_minmax(0,1fr)] font-mono text-xs leading-[19px]",
        change === "removed" && "bg-bad-soft",
        change === "added" && "bg-ok-soft",
        placeholder && "bg-surface-soft/35",
      )}
    >
      <span
        aria-hidden
        data-line-number={number}
        className="select-none border-r border-line-soft px-1 text-right tabular-nums text-faint"
      >
        {number ?? ""}
      </span>
      {placeholder ? (
        <span aria-hidden className="block min-w-0 border-l-2 border-transparent px-2" />
      ) : (
        <code
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
      )}
    </div>
  );
}

function lineNumber(group: DiffLineGroup, side: Side, index: number) {
  return (side === "before" ? group.beforeStart : group.afterStart) + index + 1;
}

function pairedLine(group: DiffLineGroup, index: number) {
  const common = group.kind === "common";
  return (
    <div
      key={`${group.id}:pair:${index}`}
      role="row"
      data-paired-row
      data-context-row={common ? "true" : undefined}
      data-change-row={common ? undefined : "true"}
      className="grid min-w-0 grid-cols-[minmax(0,1fr)_minmax(0,1fr)] items-stretch divide-x divide-line-soft"
    >
      <RawCell
        side="before"
        line={group.beforeLines[index]}
        number={group.beforeLines[index] === undefined ? undefined : lineNumber(group, "before", index)}
        change={common || group.beforeLines[index] === undefined ? undefined : "removed"}
      />
      <RawCell
        side="after"
        line={group.afterLines[index]}
        number={group.afterLines[index] === undefined ? undefined : lineNumber(group, "after", index)}
        change={common || group.afterLines[index] === undefined ? undefined : "added"}
      />
    </div>
  );
}

function PairedLines({ group, from = 0, to }: { group: DiffLineGroup; from?: number; to?: number }) {
  const count = Math.max(group.beforeLines.length, group.afterLines.length);
  const end = to ?? count;
  return <>{Array.from({ length: Math.max(0, end - from) }, (_, offset) => pairedLine(group, from + offset))}</>;
}

function ExpandRow({ hidden, expanded, onToggle, columnSpan = 1 }: {
  hidden: number;
  expanded: boolean;
  onToggle: () => void;
  columnSpan?: number;
}) {
  return (
    <div role="row" className="grid grid-cols-1">
      <div role="cell" aria-colspan={columnSpan}>
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
      </div>
    </div>
  );
}

function PairedGroup({ group, expanded, onToggle }: {
  group: DiffLineGroup;
  expanded: boolean;
  onToggle: () => void;
}) {
  const count = group.beforeLines.length;
  if (group.kind === "changed" || count < COLLAPSE_THRESHOLD) return <PairedLines group={group} />;
  const hidden = count - LEADING_CONTEXT - TRAILING_CONTEXT;
  return (
    <>
      <PairedLines group={group} to={LEADING_CONTEXT} />
      <ExpandRow hidden={hidden} expanded={expanded} onToggle={onToggle} columnSpan={2} />
      {expanded && <PairedLines group={group} from={LEADING_CONTEXT} to={count - TRAILING_CONTEXT} />}
      <PairedLines group={group} from={count - TRAILING_CONTEXT} />
    </>
  );
}

function SplitComparison({ groups, expanded, onToggle }: {
  groups: DiffLineGroup[];
  expanded: ReadonlySet<string>;
  onToggle: (id: string) => void;
}) {
  return (
    <section role="table" aria-label="Split raw Markdown comparison" className="min-w-0 bg-bg-main">
      <div role="row" className="sticky top-0 z-10 grid grid-cols-2 divide-x divide-line-soft border-b border-line-soft bg-surface">
        <div role="columnheader" aria-label="Before raw Markdown" className="px-3 py-2 text-2xs font-semibold uppercase tracking-[0.08em] text-faint">Before</div>
        <div role="columnheader" aria-label="After raw Markdown" className="px-3 py-2 text-2xs font-semibold uppercase tracking-[0.08em] text-faint">After</div>
      </div>
      <div role="rowgroup" data-raw-diff-lines>
        {groups.map((group) => (
          <PairedGroup
            key={group.id}
            group={group}
            expanded={expanded.has(group.id)}
            onToggle={() => onToggle(group.id)}
          />
        ))}
      </div>
    </section>
  );
}

function StackedLine({ group, side, index }: { group: DiffLineGroup; side: Side; index: number }) {
  const line = side === "before" ? group.beforeLines[index] : group.afterLines[index];
  const change = group.kind === "changed" ? (side === "before" ? "removed" : "added") : undefined;
  return (
    <div role="row" key={`${group.id}:${side}:${index}`}>
      <RawCell side={side} line={line} number={lineNumber(group, side, index)} change={change} />
    </div>
  );
}

function StackedLines({ group, side, from = 0, to }: {
  group: DiffLineGroup;
  side: Side;
  from?: number;
  to?: number;
}) {
  const lines = side === "before" ? group.beforeLines : group.afterLines;
  return <>{lines.slice(from, to).map((_, offset) => <StackedLine key={`${group.id}:${side}:${from + offset}`} group={group} side={side} index={from + offset} />)}</>;
}

function StackedGroup({ group, side, expanded, onToggle }: {
  group: DiffLineGroup;
  side: Side;
  expanded: boolean;
  onToggle: () => void;
}) {
  const sideLines = side === "before" ? group.beforeLines : group.afterLines;
  if (group.kind === "changed" || sideLines.length < COLLAPSE_THRESHOLD) return <StackedLines group={group} side={side} />;
  const hidden = sideLines.length - LEADING_CONTEXT - TRAILING_CONTEXT;
  return (
    <>
      <StackedLines group={group} side={side} to={LEADING_CONTEXT} />
      <ExpandRow hidden={hidden} expanded={expanded} onToggle={onToggle} />
      {expanded && <StackedLines group={group} side={side} from={LEADING_CONTEXT} to={sideLines.length - TRAILING_CONTEXT} />}
      <StackedLines group={group} side={side} from={sideLines.length - TRAILING_CONTEXT} />
    </>
  );
}

function StackedPane({ side, groups, expanded, onToggle }: {
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
          <StackedGroup
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
      className="min-w-0 overflow-auto bg-bg-main text-ink scroll-thin"
      style={style}
    >
      {layout === "split" ? (
        <SplitComparison groups={groups} expanded={expanded} onToggle={toggle} />
      ) : (
        <div className="grid grid-cols-1 divide-y divide-line-soft">
          <StackedPane side="before" groups={groups} expanded={expanded} onToggle={toggle} />
          <StackedPane side="after" groups={groups} expanded={expanded} onToggle={toggle} />
        </div>
      )}
    </div>
  );
}
