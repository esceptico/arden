import clsx from "clsx";
import { Markdown } from "@/components/ui/Markdown";
import {
  buildDiffLineGroups,
  type DiffReviewFile,
  type DiffReviewLayout,
} from "@/components/ui/diffReviewTypes";

interface ChangedPair {
  before: string;
  after: string;
}

function proseContent(content: string) {
  const lines = content.split("\n");
  if (lines[0]?.trim() !== "---") return content;
  const end = lines.findIndex((line, index) => index > 0 && line.trim() === "---");
  return end === -1 ? content : lines.slice(end + 1).join("\n");
}

function changedPairs(before: string, after: string): ChangedPair[] {
  const pairs: ChangedPair[] = [];
  for (const group of buildDiffLineGroups(before, after)) {
    if (group.kind !== "changed") continue;
    const count = Math.max(group.beforeLines.length, group.afterLines.length);
    for (let index = 0; index < count && pairs.length < 4; index += 1) {
      const beforeLine = group.beforeLines[index] ?? "";
      const afterLine = group.afterLines[index] ?? "";
      if (beforeLine || afterLine) pairs.push({ before: beforeLine, after: afterLine });
    }
    if (pairs.length === 4) break;
  }
  return pairs;
}

function highlightedText(line: string, otherLine: string, change: "added" | "removed") {
  const words = line.split(/(\s+)/);
  const other = otherLine.split(/(\s+)/);
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
        "rounded-[3px] px-0.5 font-mono text-xs text-inherit",
        change === "added" ? "bg-ok-soft" : "bg-bad-soft line-through decoration-bad/50",
      )}
    >
      {word || (change === "added" ? "Added line" : "Removed line")}
    </mark>
  ) : word);
}

function ChangeSummary({ side, pairs }: { side: "before" | "after"; pairs: ChangedPair[] }) {
  if (pairs.length === 0) return null;
  const change = side === "before" ? "removed" : "added";
  return (
    <div
      aria-label={side === "before" ? "Removed wording" : "Added wording"}
      className={clsx(
        "mb-4 grid gap-1.5 border-l-2 pl-3 text-xs leading-relaxed text-muted",
        change === "added" ? "border-ok/70" : "border-bad/60",
      )}
    >
      {pairs.map((pair, index) => (
        <div key={`${side}:${index}`} className="break-words">
          {highlightedText(side === "before" ? pair.before : pair.after, side === "before" ? pair.after : pair.before, change)}
        </div>
      ))}
    </div>
  );
}

function Version({
  side,
  file,
  pairs,
}: {
  side: "before" | "after";
  file: DiffReviewFile;
  pairs: ChangedPair[];
}) {
  const label = side === "before" ? "Before changes" : "After changes";
  return (
    <section aria-label={label} className="min-w-0 px-4 py-4">
      <h2 className="mb-3 text-2xs font-semibold uppercase tracking-[var(--tracking-caps)] text-faint">
        {side === "before" ? "Before" : "After"}
      </h2>
      <ChangeSummary side={side} pairs={pairs} />
      <Markdown content={proseContent(file.content)} className="typeset-compact text-ink-soft" />
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
  const pairs = changedPairs(proseContent(before.content), proseContent(after.content));
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
      <Version side="before" file={before} pairs={pairs} />
      <Version side="after" file={after} pairs={pairs} />
    </div>
  );
}
