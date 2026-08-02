import { useCallback, useEffect, useState } from "react";
import { CornerDownLeft, MessageSquareText } from "@/components/icons";
import { AnimatePresence, motion } from "motion/react";
import { useStore, type ApprovalState } from "@/stores";
import { respondToAllApprovals, respondToApproval } from "@/actions/approvals";
import { ICON } from "@/lib/icons";
import { EASE_OUT, MOTION, originFromEvent } from "@/lib/tokens/motion";
import { Collapse } from "@/components/ui/Collapse";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { IconButton } from "@/components/ui/IconButton";
import { getApprovalFeedbackDraft, setApprovalFeedbackDraft } from "@/lib/approvalFeedbackDraft";

// Cap visible stack to 2 cards. The front card already shows "1 of N" when
// there are more pending, so a third sliver doesn't add information — it
// just adds a GPU blend layer and lifts z-index pressure.
const STACK_OPACITY = [1, 0.5];
const STACK_Y = [0, -6];
const STACK_SCALE_STEP = 0.035;

/** Encodes which way the front card should leave on dismiss. The store
 *  removes the approval synchronously (optimistic), so AnimatePresence
 *  picks up the exit animation immediately — we just need to tell it
 *  WHICH direction encodes the user's intent. Right = approve (forward,
 *  shipped), left = reject (back, rolled away). Null = passive removal
 *  (e.g. the server canceled the approval) → neutral fade. */
type ExitReason = "approve" | "reject" | null;

interface CardCustom {
  index: number;
  visible: boolean;
  reason: ExitReason;
}

/** Variants form is required because we need per-card custom data to flow
 *  into the exit transition (motion's inline `exit` prop is statically
 *  typed and doesn't accept function-form animations). `show` reads
 *  `{ index, visible }` for stack-position; `exit` reads `{ index, reason }`
 *  for direction-encoded dismissal of the front card. */
const stackVariants = {
  initial: { opacity: 0, scale: 0.97, y: 8 },
  show: ({ index, visible }: CardCustom) => ({
    opacity: visible ? STACK_OPACITY[index] : 0,
    scale: 1 - index * STACK_SCALE_STEP,
    y: visible ? STACK_Y[index] : STACK_Y[STACK_OPACITY.length - 1],
  }),
  exit: ({ index, reason }: CardCustom) => {
    if (index !== 0) return { opacity: 0, scale: 0.96, y: 4 };
    if (reason === "approve") return { opacity: 0, scale: 1, x: 32, y: -4 };
    if (reason === "reject") return { opacity: 0, scale: 0.97, x: -32, y: 4 };
    return { opacity: 0, scale: 0.96, y: 4 };
  },
};

/** First non-empty, non-diff-noise line of `text`, truncated to ~max chars. */
function snippet(text: string, max = 160): string {
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    if (line.startsWith("---") || line.startsWith("+++") || line.startsWith("@@")) continue;
    return line.length <= max ? line : line.slice(0, max).trimEnd() + "…";
  }
  return "";
}

/** Compact single-line rendering for unstructured tool previews. */
function approvalSnippet(approval: ApprovalState): string {
  if (approval.preview) return snippet(approval.preview);
  if (approval.diff) return snippet(approval.diff);
  return "";
}

/** Detect a "structured preview" the backend wrote as `Key: value` rows
 *  optionally followed by a free-text body (separated by a blank line).
 *  Used by create_automation / create_skill so the approval card can
 *  render headers + a prominent body section rather than a single
 *  truncated line. */
interface StructuredPreview {
  fields: { key: string; value: string }[];
  body: { label: string; text: string } | null;
}

function parseStructuredPreview(text: string): StructuredPreview | null {
  if (!text) return null;
  const lines = text.split("\n");
  const fields: { key: string; value: string }[] = [];
  let cursor = 0;
  // Accumulate leading `Key: value` lines. Stop on a blank line, which
  // separates the field block from any body that follows.
  while (cursor < lines.length) {
    const line = lines[cursor];
    if (!line.trim()) break;
    const m = line.match(/^([A-Z][A-Za-z _-]{0,30}):\s*(.*)$/);
    if (!m) break;
    fields.push({ key: m[1].trim(), value: m[2].trim() });
    cursor++;
  }
  if (fields.length === 0) return null;
  // Skip the blank separator(s) and look for an optional body — a
  // single label line ("Prompt:" / "Body:" / ...) followed by the
  // remaining content as one block.
  while (cursor < lines.length && !lines[cursor].trim()) cursor++;
  let body: StructuredPreview["body"] = null;
  if (cursor < lines.length) {
    const labelMatch = lines[cursor].match(/^([A-Z][A-Za-z _-]{0,30}):\s*$/);
    if (labelMatch && cursor + 1 < lines.length) {
      body = { label: labelMatch[1], text: lines.slice(cursor + 1).join("\n").trim() };
    } else {
      // No label — treat everything that's left as a generic body.
      body = { label: "Detail", text: lines.slice(cursor).join("\n").trim() };
    }
  }
  return { fields, body };
}

/** Card-deck stack of approvals. Front card is full-size + interactive,
 *  up to two behind cards peek out as smaller, dimmer slivers above.
 *  Beyond that, count rolls into the front card's bulk actions. */
export function ApprovalBanner() {
  const approvals = useStore((s) => s.pendingApprovals);
  const reviewingId = useStore((s) => s.reviewingApprovalToolId);
  // Direction encoder for the next exit. AnimatePresence's `custom` prop
  // propagates this to each child's exit function so they animate the
  // matching way. Cleared in onExitComplete (or overwritten by the next
  // action, whichever comes first).
  const [exitReason, setExitReason] = useState<ExitReason>(null);

  const dismissWith = useCallback(
    (reason: ExitReason, run: () => void | Promise<void>) => {
      setExitReason(reason);
      // Defer the store update to the next frame so React commits the
      // exitReason render first — that updates each card's `custom` prop,
      // which is what AnimatePresence reads when the card unmounts. A
      // microtask isn't enough because React's render is scheduled, not
      // synchronous; rAF guarantees we're past the commit phase.
      requestAnimationFrame(() => {
        void run();
      });
    },
    [],
  );

  // Cmd/Ctrl+Enter approves the front card from anywhere — including
  // when the composer is focused. Plain Enter in the composer is
  // reserved for sending a message (which routes to "reject all with
  // this text as feedback" when approvals are pending). Skip when the
  // Review modal is open.
  useEffect(() => {
    if (approvals.length === 0) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Enter") return;
      if (!(e.metaKey || e.ctrlKey)) return;
      if (e.altKey || e.shiftKey) return;
      if (reviewingId) return;
      e.preventDefault();
      e.stopPropagation();
      const toolId = approvals[0].toolId;
      dismissWith("approve", () => respondToApproval(toolId, true));
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [approvals, reviewingId, dismissWith]);

  if (approvals.length === 0) return null;
  // Hide the banner deck entirely while the Review modal is open. The
  // modal carries the same toolName + full preview/diff, so showing the
  // banner stack behind it just creates a confusing double-surface. The
  // shared overlay stack now owns their relative depth.
  if (reviewingId) return null;

  return (
    <section className="board-approval-stack px-7 pt-2 pb-3" aria-label="Approval required">
      <div className="board-approval-stack__lane mx-auto max-w-[760px]">
        {/* grid + grid-area="stack" makes every child share one cell —
            the container sizes to the largest child (the front card)
            and every card overlaps in the same space. Only the front
            is interactive; back cards are decorative slivers. Headroom
            for the slivers is constant so the front card doesn't jump
            when a second approval arrives or the stack drains. */}
        <div className="board-approval-stack__deck grid pt-3.5" style={{ gridTemplateAreas: '"stack"' }}>
          <AnimatePresence
            initial={false}
            custom={exitReason}
            onExitComplete={() => setExitReason(null)}
          >
            {approvals.map((approval, index) => {
              const visible = index < STACK_OPACITY.length;
              const cardCustom: CardCustom = { index, visible, reason: exitReason };
              return (
                <motion.div
                  key={approval.toolId}
                  style={{
                    gridArea: "stack",
                    // Stack order only matters relative to siblings.
                    zIndex: index === 0 ? "var(--z-raised)" : "var(--z-base)",
                    pointerEvents: index === 0 ? "auto" : "none",
                  }}
                  variants={stackVariants}
                  custom={cardCustom}
                  initial="initial"
                  animate="show"
                  exit="exit"
                  transition={{ duration: MOTION.panel, ease: EASE_OUT }}
                >
                  <ApprovalCard
                    approval={approval}
                    interactive={index === 0}
                    totalPending={approvals.length}
                    isFront={index === 0}
                    onDismissWith={dismissWith}
                  />
                </motion.div>
              );
            })}
          </AnimatePresence>
        </div>
      </div>
    </section>
  );
}

/** One row inside the rich approval card. The value is monospace because
 *  almost every field that lands here is a path, an interval, a name, a
 *  schedule label — all things where character alignment helps reading. */
function FieldRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-faint">{label}</dt>
      <dd className="m-0 font-mono text-ink-soft break-words">{value}</dd>
    </>
  );
}

function ApprovalCard({
  approval,
  interactive,
  isFront,
  totalPending,
  onDismissWith,
}: {
  approval: ApprovalState;
  interactive: boolean;
  isFront: boolean;
  totalPending: number;
  onDismissWith: (reason: ExitReason, run: () => void | Promise<void>) => void;
}) {
  const setReviewing = useStore((s) => s.setReviewingApproval);
  const { toolId, toolName, path, diff, preview } = approval;
  const structured = preview ? parseStructuredPreview(preview) : null;
  const previewLine = !structured ? approvalSnippet(approval) : "";
  // Diff opens the modal. So does a structured preview with a long body
  // — keeps the card compact while still letting the user see the full
  // content via Review.
  const longBody = structured?.body && structured.body.text.length > 480;
  const hasReviewable = !!(diff || longBody);
  const showBulk = isFront && totalPending > 1;

  // Deny-with-reason: an inline reason the agent receives as guidance
  // (respondToApproval forwards it as the rejection feedback the backend
  // already turns into "User rejected this action and said: …"). The plain
  // Reject button stays as the instant, no-reason path.
  const [denyOpen, setDenyOpen] = useState(false);
  const [denyReason, setDenyReason] = useState(() => getApprovalFeedbackDraft(toolId));
  const submitDeny = () =>
    onDismissWith("reject", () => respondToApproval(toolId, false, denyReason.trim()));

  return (
    <div
      aria-hidden={!interactive || undefined}
      className="board-approval-card surface-panel surface-radius-md overflow-hidden"
    >
      <header className="px-4 pt-3 pb-2 flex items-baseline gap-2">
        <h3 className="m-0 text-md font-medium text-ink tracking-[var(--tracking-tight)]">
          {structured
            ? "Approve action"
            : <>Approve <span className="font-mono">{toolName}</span>?</>}
        </h3>
        {showBulk && (
          <span className="ml-auto shrink-0 text-xs text-faint tabular-nums">
            1 of {totalPending}
          </span>
        )}
      </header>

      {structured ? (
        // Rich path: render Key/Value fields as a definition list, plus
        // an optional body section (the automation prompt or skill
        // body) below. Body is clamped to ~6 lines on the card; Review
        // opens the full content.
        <div className="px-4 pb-3 grid gap-2.5">
          <div className="text-xs font-mono text-faint">{toolName}</div>
          <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-5 gap-y-1 text-sm">
            {structured.fields.map((f) => (
              <FieldRow key={f.key} label={f.key} value={f.value} />
            ))}
          </dl>
          {structured.body && structured.body.text && (
            <div className="grid gap-1">
              <div className="text-xs font-medium uppercase tracking-[var(--tracking-caps)] text-faint">
                {structured.body.label}
              </div>
              <pre
                className="m-0 font-mono text-xs text-ink-soft whitespace-pre-wrap break-words rounded-[var(--r-house)] border border-line-soft bg-bg-main/30 p-2 max-h-[8.4em] overflow-hidden"
              >
                {structured.body.text}
              </pre>
            </div>
          )}
        </div>
      ) : (
        (path || previewLine) && (
          <dl className="px-4 pb-3 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-6 gap-y-1 text-sm">
            {path && (
              <>
                <dt className="text-faint">Target</dt>
                <dd className="m-0 font-mono text-ink-soft truncate" title={path}>{path}</dd>
              </>
            )}
            {previewLine && (
              <>
                <dt className="text-faint">Content</dt>
                <dd className="m-0 font-mono text-ink-soft truncate" title={previewLine}>{previewLine}</dd>
              </>
            )}
          </dl>
        )
      )}

      <Collapse open={interactive && denyOpen}>
        <div className="flex items-center gap-2 px-3 pb-2">
          <Input
            size="sm"
            autoFocus
            value={denyReason}
            onChange={(e) => {
              setDenyReason(e.target.value);
              setApprovalFeedbackDraft(toolId, e.target.value);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                submitDeny();
              } else if (e.key === "Escape") {
                e.preventDefault();
                setDenyOpen(false);
              }
            }}
            placeholder="Why? — sent to the agent as guidance"
            className="flex-1 min-w-0"
          />
          <Button size="sm" onClick={submitDeny}>
            Deny
          </Button>
        </div>
      </Collapse>

      <footer className="flex flex-wrap items-center gap-2 px-3 py-2 bg-surface-soft/35">
        {hasReviewable && (
          <Button
            variant="ghost"
            size="sm"
            tabIndex={interactive ? 0 : -1}
            onClick={(e) => setReviewing(toolId, originFromEvent(e.currentTarget))}
          >
            Review
          </Button>
        )}
        <span className="ml-auto" />
        {showBulk && (
          <>
            <Button
              variant="ghost"
              size="sm"
              tabIndex={interactive ? 0 : -1}
              onClick={() => onDismissWith("reject", () => respondToAllApprovals(false))}
            >
              Reject all
            </Button>
            <Button
              size="sm"
              tabIndex={interactive ? 0 : -1}
              onClick={() => onDismissWith("approve", () => respondToAllApprovals(true))}
            >
              Approve all
            </Button>
            <span className="w-px h-5 bg-line-soft mx-0.5" aria-hidden />
          </>
        )}
        <IconButton
          tabIndex={interactive ? 0 : -1}
          onClick={() => setDenyOpen((v) => !v)}
          aria-label="Deny with reason"
          aria-expanded={denyOpen}
          title="Deny with reason"
          active={denyOpen}
        >
          <MessageSquareText size={ICON.SM} strokeWidth={2} />
        </IconButton>
        <Button
          size="sm"
          tabIndex={interactive ? 0 : -1}
          onClick={() => onDismissWith("reject", () => respondToApproval(toolId, false))}
        >
          Reject
        </Button>
        <Button
          variant="primary"
          size="sm"
          tabIndex={interactive ? 0 : -1}
          onClick={() => onDismissWith("approve", () => respondToApproval(toolId, true))}
          title="Approve (⌘↩)"
        >
          Approve
          <span className="inline-flex items-center gap-0.5 opacity-70 text-2xs font-mono leading-none">
            ⌘
            <CornerDownLeft size={ICON.XS} strokeWidth={2.2} />
          </span>
        </Button>
      </footer>
    </div>
  );
}
