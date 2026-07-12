import { lazy, Suspense, useState, type KeyboardEvent } from "react";
import clsx from "clsx";
import { Button } from "@/components/ui/Button";
import { radioGroupKeyDown } from "@/components/ui/RadioGroup";
import { RenderedProseDiff } from "@/components/ui/RenderedProseDiff";
import type {
  DiffReviewDecisionChoice,
  DiffReviewMode,
  DiffReviewOperation,
  DiffReviewProps,
} from "@/components/ui/diffReviewTypes";

const LazyRawDiffRenderer = lazy(() => import("@/components/ui/RawDiffRenderer").then((module) => ({
  default: module.RawDiffRenderer,
})));

const OPERATION_LABEL: Record<DiffReviewOperation["kind"], string> = {
  ADD: "Add memory",
  SUPERSEDE: "Replace memory",
  MERGE: "Merge memories",
  RETRACT: "Forget memory",
  NOOP: "No memory change",
  ASK: "Needs decision",
};

function prefersReducedMotion() {
  return typeof window !== "undefined"
    && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function operationDescription(operation: DiffReviewOperation) {
  if (operation.kind === "ASK") return operation.question;
  if (operation.kind === "NOOP") return operation.reason;
  if (operation.kind === "RETRACT") return `Retract ${operation.targetIds.join(", ")}`;
  return operation.text;
}

function DecisionRadio({
  label,
  value,
  selected,
  tabbable,
  onSelect,
}: {
  label: string;
  value: DiffReviewDecisionChoice;
  selected: boolean;
  tabbable: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      data-value={value}
      aria-label={label}
      aria-checked={selected}
      tabIndex={tabbable ? 0 : -1}
      onClick={onSelect}
      className={clsx(
        "min-w-0 flex-1 rounded-md border px-2 py-1.5 text-xs font-medium transition-colors duration-check",
        selected
          ? "border-accent bg-accent-soft text-ink"
          : "border-line-soft text-muted hover:border-line-strong hover:text-ink",
      )}
    >
      {label}
    </button>
  );
}

function AskDecision({
  operation,
  choice,
  onChange,
}: {
  operation: Extract<DiffReviewOperation, { kind: "ASK" }>;
  choice: DiffReviewDecisionChoice | undefined;
  onChange: (choice: DiffReviewDecisionChoice) => void;
}) {
  const choose = (next: string) => onChange(next as DiffReviewDecisionChoice);
  return (
    <div
      role="radiogroup"
      aria-label={`Decision for ${operation.question}`}
      className="mt-2 flex gap-1.5"
      onKeyDown={(event) => radioGroupKeyDown(event, choice ?? "", choose)}
    >
      <DecisionRadio label="Note only" value="note_only" selected={choice === "note_only"} tabbable={!choice || choice === "note_only"} onSelect={() => onChange("note_only")} />
      <DecisionRadio label="Forget memory" value="forget_memory" selected={choice === "forget_memory"} tabbable={choice === "forget_memory"} onSelect={() => onChange("forget_memory")} />
    </div>
  );
}

function MemoryEffects({
  operations,
  decisions,
  onDecision,
}: {
  operations: readonly DiffReviewOperation[];
  decisions: NonNullable<DiffReviewProps["decisions"]>;
  onDecision: NonNullable<DiffReviewProps["onDecision"]>;
}) {
  return (
    <aside aria-label="Memory effects" className="min-w-0 bg-surface px-3 py-4">
      <h2 className="text-sm font-semibold text-ink">Memory effects</h2>
      <p className="mt-0.5 text-2xs text-faint">Server-proposed record changes</p>
      <ul className="mt-3 grid gap-2">
        {operations.map((operation) => (
          <li
            key={operation.id}
            className={clsx(
              "rounded-lg bg-surface-soft/55 p-2.5",
              operation.kind === "ASK" && "border border-warning/30 bg-warning/5",
            )}
          >
            <div className={clsx(
              "text-2xs font-semibold uppercase tracking-[0.07em]",
              operation.kind === "ASK" ? "text-warning" : "text-muted",
            )}>
              {OPERATION_LABEL[operation.kind]}
            </div>
            <p className="mt-1 break-words text-xs leading-relaxed text-ink-soft">
              {operationDescription(operation)}
            </p>
            {operation.kind === "ASK" && (
              <AskDecision
                operation={operation}
                choice={decisions[operation.id]?.choice}
                onChange={(choice) => onDecision(operation.id, { choice, targetIds: operation.targetIds })}
              />
            )}
          </li>
        ))}
      </ul>
    </aside>
  );
}

function ModeTabs({
  modes,
  mode,
  onChange,
}: {
  modes: readonly DiffReviewMode[];
  mode: DiffReviewMode;
  onChange: (mode: DiffReviewMode) => void;
}) {
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight" && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    const current = modes.indexOf(mode);
    const next = event.key === "Home" ? 0
      : event.key === "End" ? modes.length - 1
        : event.key === "ArrowRight" ? (current + 1) % modes.length
          : (current - 1 + modes.length) % modes.length;
    onChange(modes[next]);
    event.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]')[next]?.focus();
  };
  return (
    <div role="tablist" aria-label="Diff view" onKeyDown={onKeyDown} className="flex rounded-md bg-surface-soft p-0.5">
      {modes.map((candidate) => (
        <button
          key={candidate}
          type="button"
          role="tab"
          aria-label={candidate === "rendered" ? "Rendered" : "Raw Markdown"}
          aria-selected={candidate === mode}
          tabIndex={candidate === mode ? 0 : -1}
          onClick={() => onChange(candidate)}
          className={clsx(
            "rounded px-2 py-1 text-xs font-medium transition-colors duration-check",
            candidate === mode ? "bg-bg-main text-ink shadow-1" : "text-faint hover:text-muted",
          )}
        >
          {candidate === "rendered" ? "Rendered" : "Raw Markdown"}
        </button>
      ))}
    </div>
  );
}

export function DiffReview({
  before,
  after,
  operations,
  decisions = {},
  onDecision = () => {},
  onApply,
  onCancel,
  applyLabel = "Apply changes",
  cancelLabel = "Back to edit",
  applyDisabled = false,
  layout = "split",
  modes = ["rendered", "raw"],
  initialMode,
  mode: controlledMode,
  onModeChange,
  reducedMotion,
}: DiffReviewProps) {
  const availableModes = modes.length ? modes : (["rendered"] as const);
  const fallbackMode = initialMode && availableModes.includes(initialMode) ? initialMode : availableModes[0];
  const [localMode, setLocalMode] = useState<DiffReviewMode>(fallbackMode);
  const mode = controlledMode && availableModes.includes(controlledMode) ? controlledMode : localMode;
  const reduced = reducedMotion ?? prefersReducedMotion();
  const unresolved = operations?.filter((operation) => operation.kind === "ASK" && !decisions[operation.id]).length ?? 0;
  const changeMode = (next: DiffReviewMode) => {
    setLocalMode(next);
    onModeChange?.(next);
  };

  return (
    <section
      data-diff-review
      data-layout={layout}
      data-motion={reduced ? "reduced" : "standard"}
      aria-label={`Review changes to ${after.path || before.path}`}
      className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-xl border border-line-soft bg-bg-main"
    >
      <header className="flex items-center gap-3 border-b border-line-soft bg-surface px-3 py-2.5">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold text-ink">Review changes</h2>
          <p className="truncate font-mono text-2xs text-faint">{before.path} → {after.path}</p>
        </div>
        {availableModes.length > 1 && <div className="ml-auto"><ModeTabs modes={availableModes} mode={mode} onChange={changeMode} /></div>}
      </header>

      <div className={clsx(
        "grid min-h-0 min-w-0 flex-1",
        operations === undefined
          ? "grid-cols-1"
          : layout === "split"
            ? "grid-cols-[minmax(0,1fr)_minmax(220px,280px)]"
            : "grid-cols-1",
      )}>
        <div
          role="tabpanel"
          aria-label={mode === "rendered" ? "Rendered changes" : "Raw Markdown changes"}
          className="min-h-0 min-w-0 overflow-auto scroll-thin"
        >
          {mode === "rendered" ? (
            <RenderedProseDiff before={before} after={after} layout={layout} reducedMotion={reduced} />
          ) : (
            <Suspense fallback={<div role="status" className="p-4 text-xs text-muted">Preparing raw diff…</div>}>
              <LazyRawDiffRenderer before={before} after={after} layout={layout} reducedMotion={reduced} />
            </Suspense>
          )}
        </div>
        {operations !== undefined && <MemoryEffects operations={operations} decisions={decisions} onDecision={onDecision} />}
      </div>

      <footer className="flex items-center gap-2 border-t border-line-soft bg-surface px-3 py-2.5">
        {operations !== undefined && (
          <span className="text-2xs text-faint">
            {operations.length} memory effect{operations.length === 1 ? "" : "s"}
            {unresolved > 0 ? ` · ${unresolved} decision${unresolved === 1 ? "" : "s"} required` : ""}
          </span>
        )}
        {onCancel && <Button variant="secondary" size="sm" className="ml-auto" onClick={onCancel}>{cancelLabel}</Button>}
        <Button
          size="sm"
          className={onCancel ? undefined : "ml-auto"}
          aria-label={applyLabel}
          disabled={applyDisabled || unresolved > 0}
          onClick={onApply}
        >
          {applyLabel}
        </Button>
      </footer>
    </section>
  );
}

export type {
  DiffReviewDecision,
  DiffReviewFile,
  DiffReviewLayout,
  DiffReviewMode,
  DiffReviewOperation,
  DiffReviewProps,
} from "@/components/ui/diffReviewTypes";
