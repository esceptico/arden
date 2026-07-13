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
  rawPatch?: string;
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
  hideFooter?: boolean;
  interactionDisabled?: boolean;
  /** Server-authored unified patch for approval surfaces that do not have full files. Raw mode only. */
  rawPatch?: string;
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

interface LineAnchor {
  before: number;
  after: number;
}

const EXACT_LCS_COMPARISON_BUDGET = 4_000_000;

interface ComparisonBudget {
  remaining: number;
}

function reserveComparisons(
  budget: ComparisonBudget,
  firstLength: number,
  secondLength: number,
) {
  if (firstLength === 0 || secondLength === 0) return true;
  if (firstLength > Math.floor(budget.remaining / secondLength)) return false;
  budget.remaining -= firstLength * secondLength;
  return true;
}

function lcsPrefixLengths(
  first: string[],
  firstStart: number,
  firstEnd: number,
  second: string[],
  secondStart: number,
  secondEnd: number,
) {
  const secondLength = secondEnd - secondStart;
  let previous = new Uint32Array(secondLength + 1);
  let current = new Uint32Array(secondLength + 1);
  for (let firstIndex = firstStart; firstIndex < firstEnd; firstIndex += 1) {
    current[0] = 0;
    for (let offset = 1; offset <= secondLength; offset += 1) {
      current[offset] = first[firstIndex] === second[secondStart + offset - 1]
        ? previous[offset - 1] + 1
        : Math.max(previous[offset], current[offset - 1]);
    }
    [previous, current] = [current, previous];
  }
  return previous;
}

function lcsSuffixLengths(
  first: string[],
  firstStart: number,
  firstEnd: number,
  second: string[],
  secondStart: number,
  secondEnd: number,
) {
  const secondLength = secondEnd - secondStart;
  let previous = new Uint32Array(secondLength + 1);
  let current = new Uint32Array(secondLength + 1);
  for (let firstIndex = firstEnd - 1; firstIndex >= firstStart; firstIndex -= 1) {
    current[secondLength] = 0;
    for (let offset = secondLength - 1; offset >= 0; offset -= 1) {
      current[offset] = first[firstIndex] === second[secondStart + offset]
        ? previous[offset + 1] + 1
        : Math.max(previous[offset], current[offset + 1]);
    }
    [previous, current] = [current, previous];
  }
  return previous;
}

function lcsSplit(
  first: string[],
  firstStart: number,
  firstMiddle: number,
  firstEnd: number,
  second: string[],
  secondStart: number,
  secondEnd: number,
) {
  const prefix = lcsPrefixLengths(first, firstStart, firstMiddle, second, secondStart, secondEnd);
  const suffix = lcsSuffixLengths(first, firstMiddle, firstEnd, second, secondStart, secondEnd);
  let bestOffset = 0;
  let bestLength = -1;
  for (let offset = 0; offset < prefix.length; offset += 1) {
    const length = prefix[offset] + suffix[offset];
    if (length > bestLength) {
      bestLength = length;
      bestOffset = offset;
    }
  }
  return secondStart + bestOffset;
}

function collectOrderedMatches(
  first: string[],
  firstStart: number,
  firstEnd: number,
  second: string[],
  secondStart: number,
  secondEnd: number,
  matches: LineAnchor[],
  budget: ComparisonBudget,
) {
  const firstLength = firstEnd - firstStart;
  const secondLength = secondEnd - secondStart;
  if (firstLength === 0 || secondLength === 0) return;
  if (firstLength === 1) {
    if (!reserveComparisons(budget, 1, secondLength)) return;
    for (let secondIndex = secondStart; secondIndex < secondEnd; secondIndex += 1) {
      if (first[firstStart] !== second[secondIndex]) continue;
      matches.push({ before: firstStart, after: secondIndex });
      return;
    }
    return;
  }
  if (secondLength === 1) {
    if (!reserveComparisons(budget, firstLength, 1)) return;
    for (let firstIndex = firstStart; firstIndex < firstEnd; firstIndex += 1) {
      if (first[firstIndex] !== second[secondStart]) continue;
      matches.push({ before: firstIndex, after: secondStart });
      return;
    }
    return;
  }

  if (!reserveComparisons(budget, firstLength, secondLength)) return;

  const firstMiddle = firstStart + Math.floor(firstLength / 2);
  const secondMiddle = lcsSplit(
    first,
    firstStart,
    firstMiddle,
    firstEnd,
    second,
    secondStart,
    secondEnd,
  );
  collectOrderedMatches(
    first,
    firstStart,
    firstMiddle,
    second,
    secondStart,
    secondMiddle,
    matches,
    budget,
  );
  collectOrderedMatches(
    first,
    firstMiddle,
    firstEnd,
    second,
    secondMiddle,
    secondEnd,
    matches,
    budget,
  );
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

  const middleAnchors: Array<{ before: number; after: number }> = [];
  const comparisonBudget: ComparisonBudget = { remaining: EXACT_LCS_COMPARISON_BUDGET };
  const beforeMiddleEnd = beforeLines.length - suffix;
  const afterMiddleEnd = afterLines.length - suffix;
  if (afterMiddleEnd - prefix <= beforeMiddleEnd - prefix) {
    collectOrderedMatches(
      beforeLines,
      prefix,
      beforeMiddleEnd,
      afterLines,
      prefix,
      afterMiddleEnd,
      middleAnchors,
      comparisonBudget,
    );
  } else {
    const swapped: LineAnchor[] = [];
    collectOrderedMatches(
      afterLines,
      prefix,
      afterMiddleEnd,
      beforeLines,
      prefix,
      beforeMiddleEnd,
      swapped,
      comparisonBudget,
    );
    for (const anchor of swapped) {
      middleAnchors.push({ before: anchor.after, after: anchor.before });
    }
  }
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
