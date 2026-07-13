import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { DiffReview } from "@/components/ui/DiffReview";
import type { DiffReviewDecision, DiffReviewOperation } from "@/components/ui/diffReviewTypes";

export interface MemoryConflict {
  currentRevision: string;
  currentContent: string;
}

export function MemoryEditReview({
  kind,
  path,
  baseContent,
  draftContent,
  operations = [],
  decisions,
  analysisPending = false,
  conflict,
  pending,
  error,
  onDecision,
  onApply,
  onCancel,
  onRebase,
}: {
  kind: "preview" | "conflict" | "external";
  path: string;
  baseContent: string;
  draftContent: string;
  operations?: readonly DiffReviewOperation[];
  decisions: Readonly<Record<string, DiffReviewDecision>>;
  analysisPending?: boolean;
  conflict?: MemoryConflict;
  pending: boolean;
  error: string | null;
  onDecision: (operationId: string, decision: DiffReviewDecision) => void;
  onApply: () => void;
  onCancel: () => void;
  onRebase?: () => void;
}) {
  const containerRef = useRef<HTMLElement>(null);
  const [stacked, setStacked] = useState(true);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === "undefined") return;
    const measure = () => setStacked(container.clientWidth < 860);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  if (kind === "conflict" && conflict) {
    return (
      <section
        ref={containerRef}
        data-memory-edit-review
        data-reduced-motion-ready="true"
        data-long-content-ready="true"
        aria-label={`Three-way conflict for ${path}`}
        className="flex h-full min-h-0 min-w-0 flex-col bg-bg-main p-3 sm:p-4"
      >
        <header className="mb-3 flex items-center gap-3">
          <div className="min-w-0">
            <h1 className="text-lg font-semibold text-ink">Page changed elsewhere</h1>
            <p className="truncate text-xs text-muted">Compare both changes from your saved base. Your draft was not changed.</p>
          </div>
          <Button className="ml-auto" variant="secondary" size="sm" aria-label="Back to draft" onClick={onCancel}>Back to draft</Button>
          <Button size="sm" aria-label="Continue with current page" onClick={onRebase}>Continue with current</Button>
        </header>
        <div className="grid min-h-0 min-w-0 flex-1 gap-3 overflow-auto lg:grid-cols-2">
          <section aria-label="Current page" className="flex min-h-[280px] min-w-0 flex-col">
            <h2 className="mb-1 text-xs font-semibold text-muted">Current page · rev {conflict.currentRevision.slice(0, 12)}</h2>
            <DiffReview
              before={{ path, content: baseContent }}
              after={{ path, content: conflict.currentContent }}
              layout="stacked"
              hideFooter
            />
          </section>
          <section aria-label="Your draft" className="flex min-h-[280px] min-w-0 flex-col">
            <h2 className="mb-1 text-xs font-semibold text-muted">Your draft</h2>
            <DiffReview
              before={{ path, content: baseContent }}
              after={{ path, content: draftContent }}
              layout="stacked"
              hideFooter
            />
          </section>
        </div>
      </section>
    );
  }

  const external = kind === "external";
  return (
    <section
      ref={containerRef}
      data-memory-edit-review
      data-reduced-motion-ready="true"
      data-long-content-ready="true"
      aria-label={external ? `Resolve external memory effects for ${path}` : `Review memory edit for ${path}`}
      className="flex h-full min-h-0 min-w-0 flex-col bg-bg-main p-3 sm:p-4"
    >
      {analysisPending && (
        <div role="status" className="mb-3 rounded-lg bg-warn-soft px-3 py-2 text-xs text-ink-soft">
          Memory analysis is unavailable. Saving now records this edit as pending for later reconciliation.
        </div>
      )}
      {external && (
        <div role="status" className="mb-3 rounded-lg bg-warn-soft px-3 py-2 text-xs text-ink-soft">
          The page is already current. Resolve only the proposed memory effects.
        </div>
      )}
      <div className="min-h-0 min-w-0 flex-1">
        <DiffReview
          before={{ path, content: baseContent }}
          after={{ path, content: draftContent }}
          operations={operations}
          decisions={decisions}
          onDecision={onDecision}
          onApply={onApply}
          onCancel={onCancel}
          applyLabel={external ? "Resolve memory effects" : analysisPending ? "Save as pending" : "Apply changes"}
          applyDisabled={pending}
          cancelLabel={external ? "Not now" : "Back to edit"}
          layout={stacked ? "stacked" : "split"}
        />
      </div>
      {error && <p role="alert" className="mt-2 text-xs text-bad">{error}</p>}
    </section>
  );
}
