import { useState, type ReactNode } from "react";
import clsx from "clsx";
import type { DiffReviewFile, DiffReviewLayout } from "@/components/ui/diffReviewTypes";

interface CommonSegment {
  kind: "common";
  id: string;
  lines: string[];
}

interface ChangedSegment {
  kind: "changed";
  id: string;
  before: string[];
  after: string[];
}

type Segment = CommonSegment | ChangedSegment;

const COLLAPSE_THRESHOLD = 7;
const LEADING_CONTEXT = 1;
const TRAILING_CONTEXT = 2;

function proseContent(content: string) {
  const lines = content.split("\n");
  if (lines[0]?.trim() !== "---") return content;
  const end = lines.findIndex((line, index) => index > 0 && line.trim() === "---");
  return end === -1 ? content : lines.slice(end + 1).join("\n");
}

function lineSegments(before: string, after: string): Segment[] {
  const beforeLines = before.split("\n");
  const afterLines = after.split("\n");
  let prefix = 0;
  while (
    prefix < beforeLines.length
    && prefix < afterLines.length
    && beforeLines[prefix] === afterLines[prefix]
  ) prefix += 1;

  let suffix = 0;
  while (
    suffix < beforeLines.length - prefix
    && suffix < afterLines.length - prefix
    && beforeLines[beforeLines.length - suffix - 1] === afterLines[afterLines.length - suffix - 1]
  ) suffix += 1;

  const beforeCounts = new Map<string, number>();
  const afterPositions = new Map<string, number[]>();
  for (const line of beforeLines) beforeCounts.set(line, (beforeCounts.get(line) ?? 0) + 1);
  for (let index = 0; index < afterLines.length; index += 1) {
    const line = afterLines[index];
    const positions = afterPositions.get(line) ?? [];
    positions.push(index);
    afterPositions.set(line, positions);
  }

  const candidates: Array<{ before: number; after: number }> = [];
  for (let index = prefix; index < beforeLines.length - suffix; index += 1) {
    const line = beforeLines[index];
    const positions = afterPositions.get(line);
    if (beforeCounts.get(line) === 1 && positions?.length === 1) {
      const afterIndex = positions[0];
      if (afterIndex >= prefix && afterIndex < afterLines.length - suffix) {
        candidates.push({ before: index, after: afterIndex });
      }
    }
  }

  const tails: number[] = [];
  const previous = new Array<number>(candidates.length).fill(-1);
  for (let index = 0; index < candidates.length; index += 1) {
    let low = 0;
    let high = tails.length;
    while (low < high) {
      const middle = (low + high) >> 1;
      if (candidates[tails[middle]].after < candidates[index].after) low = middle + 1;
      else high = middle;
    }
    if (low > 0) previous[index] = tails[low - 1];
    tails[low] = index;
  }
  const middleAnchors: Array<{ before: number; after: number }> = [];
  let cursor = tails.at(-1) ?? -1;
  while (cursor >= 0) {
    middleAnchors.push(candidates[cursor]);
    cursor = previous[cursor];
  }
  middleAnchors.reverse();

  const anchors = [
    ...Array.from({ length: prefix }, (_, index) => ({ before: index, after: index })),
    ...middleAnchors,
    ...Array.from({ length: suffix }, (_, index) => ({
      before: beforeLines.length - suffix + index,
      after: afterLines.length - suffix + index,
    })),
  ];
  const segments: Segment[] = [];
  let beforeIndex = 0;
  let afterIndex = 0;
  let segmentIndex = 0;
  for (let anchorIndex = 0; anchorIndex < anchors.length;) {
    const anchor = anchors[anchorIndex];
    if (anchor.before > beforeIndex || anchor.after > afterIndex) {
      segments.push({
        kind: "changed",
        id: `change-${segmentIndex++}`,
        before: beforeLines.slice(beforeIndex, anchor.before),
        after: afterLines.slice(afterIndex, anchor.after),
      });
    }
    const common: string[] = [beforeLines[anchor.before]];
    let last = anchor;
    anchorIndex += 1;
    while (
      anchorIndex < anchors.length
      && anchors[anchorIndex].before === last.before + 1
      && anchors[anchorIndex].after === last.after + 1
    ) {
      last = anchors[anchorIndex];
      common.push(beforeLines[last.before]);
      anchorIndex += 1;
    }
    segments.push({ kind: "common", id: `common-${segmentIndex++}`, lines: common });
    beforeIndex = last.before + 1;
    afterIndex = last.after + 1;
  }
  if (beforeIndex < beforeLines.length || afterIndex < afterLines.length) {
    segments.push({
      kind: "changed",
      id: `change-${segmentIndex}`,
      before: beforeLines.slice(beforeIndex),
      after: afterLines.slice(afterIndex),
    });
  }
  return segments;
}

function visibleText(line: string) {
  return line
    .replace(/^#{1,6}\s+/, "")
    .replace(/^\s*[-*+]\s+/, "")
    .replace(/^>\s?/, "")
    .replace(/\*\*|__|~~/g, "")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, "$2")
    .replace(/\[\[([^\]]+)\]\]/g, "$1");
}

function highlightedText(line: string, otherLine: string, change: "added" | "removed") {
  const words = visibleText(line).split(/(\s+)/);
  const other = visibleText(otherLine).split(/(\s+)/);
  let prefix = 0;
  while (prefix < words.length && prefix < other.length && words[prefix] === other[prefix]) prefix += 1;
  let suffix = 0;
  while (
    suffix < words.length - prefix
    && suffix < other.length - prefix
    && words[words.length - suffix - 1] === other[other.length - suffix - 1]
  ) suffix += 1;
  const end = words.length - suffix;
  return words.map((word, index) => index >= prefix && index < end ? (
    <mark
      key={`${index}:${word}`}
      data-change={change}
      className={clsx(
        "rounded-[3px] px-0.5 text-inherit",
        change === "added" ? "bg-ok-soft" : "bg-bad-soft line-through decoration-bad/50",
      )}
    >
      {word}
    </mark>
  ) : word);
}

function ProseLine({ line, children }: { line: string; children?: ReactNode }) {
  const content = children ?? visibleText(line);
  const heading = /^(#{1,6})\s+/.exec(line);
  if (heading) {
    const level = heading[1].length;
    if (level === 1) return <h1 className="text-xl font-semibold tracking-tight text-ink">{content}</h1>;
    if (level === 2) return <h2 className="text-lg font-semibold text-ink">{content}</h2>;
    if (level === 3) return <h3 className="text-md font-semibold text-ink">{content}</h3>;
    return <h4 className="text-sm font-semibold text-ink">{content}</h4>;
  }
  if (/^\s*[-*+]\s+/.test(line)) return <div className="pl-4 before:mr-2 before:content-['•']">{content}</div>;
  if (/^>\s?/.test(line)) return <blockquote className="border-l-2 border-line pl-3 text-muted">{content}</blockquote>;
  if (!line) return <div aria-hidden className="h-3" />;
  return <p>{content}</p>;
}

function CommonLines({
  segment,
  expanded,
  onExpand,
}: {
  segment: CommonSegment;
  expanded: boolean;
  onExpand: () => void;
}) {
  if (segment.lines.length < COLLAPSE_THRESHOLD || expanded) {
    return <>{segment.lines.map((line, index) => <ProseLine key={`${segment.id}:${index}`} line={line} />)}</>;
  }
  const hidden = segment.lines.length - LEADING_CONTEXT - TRAILING_CONTEXT;
  return (
    <>
      {segment.lines.slice(0, LEADING_CONTEXT).map((line, index) => <ProseLine key={`${segment.id}:start:${index}`} line={line} />)}
      <button
        type="button"
        aria-label={`Show ${hidden} unchanged lines`}
        onClick={onExpand}
        className="my-1 w-full rounded-md border border-line-soft bg-surface-soft/40 px-2 py-1 text-center text-2xs text-faint hover:text-muted"
      >
        {hidden} unchanged lines
      </button>
      {segment.lines.slice(-TRAILING_CONTEXT).map((line, index) => <ProseLine key={`${segment.id}:end:${index}`} line={line} />)}
    </>
  );
}

function Version({
  side,
  segments,
  expanded,
  onExpand,
}: {
  side: "before" | "after";
  segments: Segment[];
  expanded: ReadonlySet<string>;
  onExpand: (id: string) => void;
}) {
  const change = side === "before" ? "removed" : "added";
  return (
    <section aria-label={side === "before" ? "Before changes" : "After changes"} className="min-w-0 px-4 py-4">
      <h2 className="mb-3 text-2xs font-semibold uppercase tracking-[0.08em] text-faint">
        {side === "before" ? "Before" : "After"}
      </h2>
      <div className="space-y-2 text-sm leading-relaxed text-ink-soft">
        {segments.map((segment) => segment.kind === "common" ? (
          <CommonLines key={segment.id} segment={segment} expanded={expanded.has(segment.id)} onExpand={() => onExpand(segment.id)} />
        ) : (
          <div
            key={segment.id}
            data-change-block={change}
            className={clsx("space-y-2 border-l-2 pl-3", change === "added" ? "border-ok/70" : "border-bad/60")}
          >
            {(side === "before" ? segment.before : segment.after).map((line, index) => {
              const other = (side === "before" ? segment.after : segment.before)[index] ?? "";
              return <ProseLine key={`${segment.id}:${index}`} line={line}>{highlightedText(line, other, change)}</ProseLine>;
            })}
          </div>
        ))}
      </div>
    </section>
  );
}

export function RenderedProseDiff({
  before,
  after,
  layout,
  reducedMotion = false,
}: {
  before: DiffReviewFile;
  after: DiffReviewFile;
  layout: DiffReviewLayout;
  reducedMotion?: boolean;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const segments = lineSegments(proseContent(before.content), proseContent(after.content));
  const expand = (id: string) => setExpanded((current) => new Set(current).add(id));
  return (
    <div
      data-rendered-prose-diff
      data-layout={layout}
      className={clsx(
        "grid min-w-0 overflow-hidden bg-bg-main",
        layout === "split" ? "grid-cols-2 divide-x divide-line-soft" : "grid-cols-1 divide-y divide-line-soft",
      )}
      style={{ transition: reducedMotion ? "none" : undefined }}
    >
      <Version side="before" segments={segments} expanded={expanded} onExpand={expand} />
      <Version side="after" segments={segments} expanded={expanded} onExpand={expand} />
    </div>
  );
}
