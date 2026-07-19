import { useEffect, useMemo, useState } from "react";
import { ChevronRight, ShieldCheck, TriangleAlert } from "lucide-react";
import { ICON } from "@/lib/icons";
import { turnProofSummary } from "@/features/context/lib/turnProof";
import { useStore } from "@/stores";

export function ProofSummary({ turnId }: { turnId: string }) {
  const revision = useStore((state) => state.sourceRefsRevision);
  const openContextForTurn = useStore((state) => state.openContextForTurn);
  const [open, setOpen] = useState(false);
  const summary = useMemo(() => {
    const { messages, order } = useStore.getState();
    return turnProofSummary(messages, order, turnId);
  }, [revision, turnId]);

  useEffect(() => setOpen(false), [turnId]);
  if (!summary) return null;

  const attention = summary.tone === "attention";
  const Icon = attention ? TriangleAlert : ShieldCheck;
  const label = summaryLabel(summary);
  const counts = countLabels(summary);

  return (
    <div
      data-proof-summary="true"
      className="w-full max-w-[680px] rounded-lg bg-surface-soft/45 text-xs text-muted"
    >
      <button
        type="button"
        aria-expanded={open}
        aria-label={`${open ? "Collapse" : "Expand"} outcome evidence`}
        onClick={() => setOpen((value) => !value)}
        className="app-row flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-surface-soft/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <Icon
          aria-hidden
          size={ICON.XS}
          strokeWidth={2}
          className={attention ? "text-warn" : "text-ok"}
        />
        <span className="font-medium text-ink-soft">{label}</span>
        {counts && <span className="min-w-0 flex-1 truncate">{counts}</span>}
        <ChevronRight
          aria-hidden
          size={ICON.XS}
          className={`shrink-0 text-faint transition-transform duration-fast ${open ? "rotate-90" : ""}`}
        />
      </button>
      {open && (
        <div className="grid gap-3 px-3 pb-3 pt-1">
          <ProofRows label="Actions" rows={summary.actions.map((row) => ({
            key: row.toolCallId,
            primary: row.toolLabel,
            secondary: `${readable(row.operation)} · ${row.target}`,
          }))} />
          <ProofRows label="Checks" rows={summary.checks.map((row) => ({
            key: row.toolCallId,
            primary: row.postcondition,
            secondary: row.observed,
          }))} />
          <ProofRows label="Receipts" rows={summary.receipts.map((row) => ({
            key: row.toolCallId,
            primary: row.receipt,
            secondary: row.toolLabel,
          }))} />
          <ProofRows label="Limitations" tone="warn" rows={summary.limitations.map((row) => ({
            key: row.toolCallId,
            primary: row.code,
            secondary: row.recoveryAction ?? row.status,
          }))} />
          <button
            type="button"
            onClick={() => openContextForTurn(turnId)}
            className="w-fit rounded-md px-1.5 py-1 font-medium text-ink-soft transition-colors hover:bg-surface-soft hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            View turn details
          </button>
        </div>
      )}
    </div>
  );
}

function ProofRows({
  label,
  rows,
  tone = "neutral",
}: {
  label: string;
  rows: { key: string; primary: string; secondary: string }[];
  tone?: "neutral" | "warn";
}) {
  if (rows.length === 0) return null;
  return (
    <section className="grid gap-1">
      <h4 className={`text-[11px] font-semibold uppercase tracking-wide ${tone === "warn" ? "text-warn" : "text-faint"}`}>
        {label}
      </h4>
      {rows.map((row) => (
        <div key={`${label}:${row.key}`} className="min-w-0 rounded-md bg-surface/55 px-2 py-1.5">
          <div className="break-words font-medium text-ink-soft">{row.primary}</div>
          <div className="break-words text-muted">{row.secondary}</div>
        </div>
      ))}
    </section>
  );
}

function countLabels(summary: ReturnType<typeof turnProofSummary> & {}): string {
  if (!summary) return "";
  return [
    countLabel(summary.checkCount, "check"),
    countLabel(summary.receiptCount, "receipt"),
    countLabel(summary.limitationCount, "limitation"),
  ].filter(Boolean).join(" · ");
}

function summaryLabel(summary: NonNullable<ReturnType<typeof turnProofSummary>>): string {
  if (summary.tone === "attention") return "Needs attention";
  if (summary.actionCount === 1) return `${summary.actions[0]?.toolLabel ?? "Action"} completed`;
  if (summary.actionCount > 1) return `${summary.actionCount} actions completed`;
  return "Outcome verified";
}

function countLabel(count: number, noun: string): string {
  return count > 0 ? `${count} ${noun}${count === 1 ? "" : "s"}` : "";
}

function readable(value: string): string {
  return value.replaceAll("_", " ").replace(/^./, (character) => character.toUpperCase());
}
