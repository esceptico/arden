import { useEffect, useMemo, useState } from "react";
import { ChevronRight, RotateCcw, ShieldCheck, TriangleAlert } from "lucide-react";
import { EmptyState } from "@/components/ui/EmptyState";
import { ICON } from "@/lib/icons";
import {
  getTurnInspector,
  type InspectorContextItem,
  type TurnInspector,
} from "@/api/turnInspector";
import { latestInspectableTurnId } from "@/features/context/lib/turnProof";
import { useStore } from "@/stores";

type LoadState =
  | { phase: "idle" | "loading"; data: null }
  | { phase: "ready"; data: TurnInspector | null }
  | { phase: "error"; data: null };

const TECHNICAL_CONTEXT_TYPES = new Set([
  "runtime_time",
  "system_prompt",
  "temporal_reminder",
]);

export function ContextPanel() {
  const config = useStore((state) => state.config);
  const sessionId = useStore((state) => state.currentSessionId);
  const exactTurnId = useStore((state) => state.contextTurnId);
  const revision = useStore((state) => state.sourceRefsRevision);
  const [retryKey, setRetryKey] = useState(0);
  const [technicalOpen, setTechnicalOpen] = useState(false);
  const [load, setLoad] = useState<LoadState>({ phase: "idle", data: null });
  const turnId = useMemo(() => {
    if (exactTurnId) return exactTurnId;
    const { messages, order } = useStore.getState();
    return latestInspectableTurnId(messages, order);
  }, [exactTurnId, revision]);

  useEffect(() => {
    if (!sessionId || !turnId) {
      setLoad({ phase: "idle", data: null });
      return;
    }
    const controller = new AbortController();
    let current = true;
    setLoad({ phase: "loading", data: null });
    void getTurnInspector(config, sessionId, turnId, controller.signal).then(
      (data) => current && setLoad({ phase: "ready", data }),
      () => current && !controller.signal.aborted && setLoad({ phase: "error", data: null }),
    );
    return () => {
      current = false;
      controller.abort();
    };
  }, [config, retryKey, sessionId, turnId]);

  useEffect(() => setTechnicalOpen(false), [turnId]);

  if (!sessionId || !turnId) {
    return <PanelEmpty>No completed turn to inspect.</PanelEmpty>;
  }
  if (load.phase === "loading" || load.phase === "idle") {
    return <p className="px-3 py-8 text-center text-xs text-muted">Loading turn details…</p>;
  }
  if (load.phase === "error") {
    return (
      <EmptyState
        size="sm"
        icon={TriangleAlert}
        className="min-h-[160px]"
        action={(
          <button
            type="button"
            onClick={() => setRetryKey((value) => value + 1)}
            className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-ink-soft hover:bg-surface-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <RotateCcw aria-hidden size={ICON.XS} />
            Retry
          </button>
        )}
      >
        Could not load turn details.
      </EmptyState>
    );
  }
  if (!load.data || !hasInspectorContent(load.data)) {
    return <PanelEmpty>No additional context or outcomes for this turn.</PanelEmpty>;
  }

  const { data } = load;
  const { applied, technical } = partitionContext(data.context);
  return (
    <div className="grid gap-5 pb-2">
      {applied.length > 0 && (
        <InspectorSection label="Context applied" count={applied.length}>
          {applied.map((item) => <ContextRow key={item.id} item={item} />)}
        </InspectorSection>
      )}

      {technical.length > 0 && (
        <section className="grid gap-1.5">
          <button
            type="button"
            aria-expanded={technicalOpen}
            onClick={() => setTechnicalOpen((value) => !value)}
            className="app-row flex w-full items-center gap-2 rounded-lg px-1 py-1.5 text-left text-xs text-muted hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <ChevronRight
              aria-hidden
              size={ICON.XS}
              className={`shrink-0 transition-transform duration-fast ${technicalOpen ? "rotate-90" : ""}`}
            />
            <span className="flex-1 font-medium">Technical details</span>
            <span className="text-[11px] text-faint">{technical.length}</span>
          </button>
          {technicalOpen && (
            <div className="grid gap-1">
              {technical.map((item) => <ContextRow key={item.id} item={item} technical />)}
            </div>
          )}
        </section>
      )}

      {evidenceCount(data) > 0 && (
        <InspectorSection label="Outcome evidence" count={evidenceCount(data)}>
          {data.evidence.limitations.map((item) => (
            <EvidenceRow
              key={`limitation:${item.toolCallId}`}
              icon={TriangleAlert}
              tone="warn"
              title={`${item.status} · ${item.code}`}
              detail={item.recoveryAction ?? item.toolCallId}
            />
          ))}
          {data.evidence.effects.map((item) => (
            <EvidenceRow
              key={`effect:${item.toolCallId}`}
              icon={ShieldCheck}
              title={`${item.operation} · ${item.target}`}
              detail={[item.beforeRef, item.afterRef].filter(Boolean).join(" → ") || item.toolCallId}
            />
          ))}
          {data.evidence.checks.map((item) => (
            <EvidenceRow
              key={`check:${item.toolCallId}`}
              icon={ShieldCheck}
              title={item.postcondition}
              detail={item.observed}
            />
          ))}
          {data.evidence.receipts.map((item) => (
            <EvidenceRow
              key={`receipt:${item.toolCallId}`}
              icon={ShieldCheck}
              title="Receipt"
              detail={item.receipt}
            />
          ))}
          {data.evidence.approvals.map((item) => (
            <EvidenceRow
              key={`approval:${item.toolCallId}`}
              icon={ShieldCheck}
              title={`${readable(item.toolName)} · ${item.status}`}
              detail={item.feedback ?? item.toolCallId}
            />
          ))}
        </InspectorSection>
      )}

    </div>
  );
}

function ContextRow({ item, technical = false }: { item: InspectorContextItem; technical?: boolean }) {
  return (
    <div className="grid gap-1 rounded-lg bg-surface-soft/55 px-2.5 py-2">
      <div className="flex min-w-0 items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-ink-soft">
          {readable(item.contentType)}
        </span>
        {technical && (
          <span className="shrink-0 text-[11px] text-faint">{formatBytes(item.sizeBytes)}</span>
        )}
      </div>
      <div className="break-words text-xs text-ink-soft">{item.selectionReason}</div>
      {technical && (
        <>
          <div className="break-words text-xs text-muted">{item.source} · {item.ref}</div>
          <div className="text-[11px] text-faint">Freshness: {item.freshness}</div>
        </>
      )}
    </div>
  );
}

function InspectorSection({ label, count, children }: { label: string; count: number; children: React.ReactNode }) {
  return (
    <section className="grid gap-1.5">
      <header className="flex items-center justify-between px-1">
        <h3 className="text-xs font-semibold text-ink-soft">{label}</h3>
        <span className="text-[11px] text-faint">{count}</span>
      </header>
      <div className="grid gap-1">{children}</div>
    </section>
  );
}

function EvidenceRow({
  icon: Icon,
  title,
  detail,
  tone = "neutral",
}: {
  icon: typeof ShieldCheck;
  title: string;
  detail: string;
  tone?: "neutral" | "warn";
}) {
  return (
    <div className="flex min-w-0 items-start gap-2 rounded-lg bg-surface-soft/55 px-2.5 py-2">
      <Icon aria-hidden size={ICON.XS} className={`mt-0.5 shrink-0 ${tone === "warn" ? "text-warn" : "text-faint"}`} />
      <span className="min-w-0">
        <span className="block break-words text-xs font-medium text-ink-soft">{title}</span>
        <span className="block break-words text-xs text-muted">{detail}</span>
      </span>
    </div>
  );
}

function PanelEmpty({ children }: { children: React.ReactNode }) {
  return (
    <EmptyState size="sm" icon={ShieldCheck} className="min-h-[160px]">
      {children}
    </EmptyState>
  );
}

function hasInspectorContent(data: TurnInspector): boolean {
  return data.context.length + evidenceCount(data) > 0;
}

function partitionContext(items: InspectorContextItem[]): {
  applied: InspectorContextItem[];
  technical: InspectorContextItem[];
} {
  const applied: InspectorContextItem[] = [];
  const technical: InspectorContextItem[] = [];
  for (const item of items) {
    (TECHNICAL_CONTEXT_TYPES.has(item.contentType) ? technical : applied).push(item);
  }
  return { applied, technical };
}

function evidenceCount(data: TurnInspector): number {
  const evidence = data.evidence;
  return evidence.approvals.length + evidence.effects.length + evidence.receipts.length
    + evidence.checks.length + evidence.limitations.length;
}

function readable(value: string): string {
  return value.replaceAll("_", " ").replace(/^./, (character) => character.toUpperCase());
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
}
