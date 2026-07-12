export interface DiffReviewFile {
  path: string;
  content: string;
}

export type DiffReviewLayout = "split" | "stacked";
export type DiffReviewMode = "rendered" | "raw";
export type DiffReviewDecisionChoice = "note_only" | "forget_memory";

export interface DiffReviewDecision {
  choice: DiffReviewDecisionChoice;
  targetIds: string[];
}

export interface DiffReviewScope {
  kind: string;
  key: string | null;
}

interface TextOperation {
  id: string;
  text: string;
  memoryKind?: string | null;
  scope?: DiffReviewScope | null;
  targetIds?: string[];
}

export type DiffReviewOperation =
  | ({ kind: "ADD" | "SUPERSEDE" | "MERGE" } & TextOperation)
  | { kind: "RETRACT"; id: string; targetIds: string[] }
  | { kind: "NOOP"; id: string; reason: string }
  | { kind: "ASK"; id: string; question: string; targetIds: string[] };

export interface RawDiffRendererProps {
  before: DiffReviewFile;
  after: DiffReviewFile;
  layout: DiffReviewLayout;
  reducedMotion?: boolean;
}

export interface DiffReviewProps {
  before: DiffReviewFile;
  after: DiffReviewFile;
  operations?: readonly DiffReviewOperation[];
  decisions?: Readonly<Record<string, DiffReviewDecision>>;
  onDecision?: (operationId: string, decision: DiffReviewDecision) => void;
  onApply?: () => void;
  onCancel?: () => void;
  applyLabel?: string;
  cancelLabel?: string;
  applyDisabled?: boolean;
  layout?: DiffReviewLayout;
  modes?: readonly DiffReviewMode[];
  initialMode?: DiffReviewMode;
  mode?: DiffReviewMode;
  onModeChange?: (mode: DiffReviewMode) => void;
  reducedMotion?: boolean;
}

export interface DiffLineGroup {
  id: string;
  kind: "common" | "changed";
  beforeStart: number;
  afterStart: number;
  beforeLines: string[];
  afterLines: string[];
}

function lines(content: string) {
  return content === "" ? [] : content.split("\n");
}

export function buildDiffLineGroups(before: string, after: string): DiffLineGroup[] {
  const beforeLines = lines(before);
  const afterLines = lines(after);
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
    const afterIndex = positions?.[0];
    if (
      beforeCounts.get(line) === 1
      && positions?.length === 1
      && afterIndex !== undefined
      && afterIndex >= prefix
      && afterIndex < afterLines.length - suffix
    ) candidates.push({ before: index, after: afterIndex });
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

  const groups: DiffLineGroup[] = [];
  let beforeIndex = 0;
  let afterIndex = 0;
  let groupIndex = 0;
  for (let anchorIndex = 0; anchorIndex < anchors.length;) {
    const anchor = anchors[anchorIndex];
    if (anchor.before > beforeIndex || anchor.after > afterIndex) {
      groups.push({
        id: `changed-${groupIndex++}`,
        kind: "changed",
        beforeStart: beforeIndex,
        afterStart: afterIndex,
        beforeLines: beforeLines.slice(beforeIndex, anchor.before),
        afterLines: afterLines.slice(afterIndex, anchor.after),
      });
    }
    const commonStartBefore = anchor.before;
    const commonStartAfter = anchor.after;
    let last = anchor;
    anchorIndex += 1;
    while (
      anchorIndex < anchors.length
      && anchors[anchorIndex].before === last.before + 1
      && anchors[anchorIndex].after === last.after + 1
    ) {
      last = anchors[anchorIndex];
      anchorIndex += 1;
    }
    const common = beforeLines.slice(commonStartBefore, last.before + 1);
    groups.push({
      id: `common-${groupIndex++}`,
      kind: "common",
      beforeStart: commonStartBefore,
      afterStart: commonStartAfter,
      beforeLines: common,
      afterLines: common,
    });
    beforeIndex = last.before + 1;
    afterIndex = last.after + 1;
  }
  if (beforeIndex < beforeLines.length || afterIndex < afterLines.length) {
    groups.push({
      id: `changed-${groupIndex}`,
      kind: "changed",
      beforeStart: beforeIndex,
      afterStart: afterIndex,
      beforeLines: beforeLines.slice(beforeIndex),
      afterLines: afterLines.slice(afterIndex),
    });
  }
  return groups;
}
