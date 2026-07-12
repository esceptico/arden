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
