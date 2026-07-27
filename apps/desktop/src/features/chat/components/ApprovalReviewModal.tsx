import { useStore } from "@/stores";
import { respondToApproval } from "@/actions/approvals";
import { Button } from "@/components/ui/Button";
import { CodeWell } from "@/components/ui/CodeWell";
import { SystemSheet } from "@/components/ui/SystemSheet";

function ApprovalPatch({ patch }: { patch: string }) {
  return (
    <CodeWell
      data-approval-diff
      aria-label="Proposed diff"
      padded={false}
      className="arden-code-well--diff"
    >
      {patch.split("\n").map((line, index) => {
        const added = line.startsWith("+") && !line.startsWith("+++");
        const removed = line.startsWith("-") && !line.startsWith("---");
        return (
          <span
            key={`${index}:${line}`}
            className={`block px-3 whitespace-pre-wrap ${added ? "bg-ok-soft" : removed ? "bg-bad-soft" : ""}`}
          >
            {line || " "}
          </span>
        );
      })}
    </CodeWell>
  );
}

/** Diff/preview review for a pending approval. Opens when the banner's
 *  Review button is clicked. Approve/Reject actions live here too so the
 *  user doesn't have to dismiss the modal first. */
export function ApprovalReviewModal() {
  const reviewing = useStore((s) => s.reviewingApprovalToolId);
  const approval = useStore((s) =>
    reviewing ? s.pendingApprovals.find((a) => a.toolId === reviewing) ?? null : null,
  );
  const close = useStore((s) => s.setReviewingApproval);
  const toolItem = useStore((s) => {
    if (!reviewing) return null;
    for (const message of s.messages.values()) {
      const item = message.activity?.items.find((candidate) => candidate.id === reviewing);
      if (item) return item;
    }
    return null;
  });
  const viewTool = useStore((s) => s.setViewingTool);

  const title = approval?.diff ? "Review file changes" : "Review tool call";

  return (
    <SystemSheet
      open={!!approval}
      onClose={() => close(null)}
      title={title}
      ariaLabel={approval ? `Review ${approval.toolName}` : "Review approval"}
      status="Awaiting approval"
      statusTone="warning"
      closeLabel="Close review"
      footer={approval && (
        <>
          <Button
            variant="quiet"
            onClick={() => void respondToApproval(approval.toolId, false)}
          >
            Deny
          </Button>
          <Button
            variant="primary"
            onClick={() => void respondToApproval(approval.toolId, true)}
          >
            {approval.diff ? "Approve changes" : "Approve"}
          </Button>
        </>
      )}
    >
      {approval && (
        <>
          {approval.diff && (
            <section
              className="arden-review-summary"
              data-has-action={toolItem ? "true" : undefined}
            >
              <div className="min-w-0">
                <strong className="block truncate text-base font-semibold text-ink">{approval.toolName}</strong>
                <p className="mt-1 text-base text-muted">
                  {approval.path ? "Writes one file. No external message is sent." : "Reviews the proposed change before it runs."}
                </p>
              </div>
              {toolItem && (
                <Button onClick={() => viewTool(toolItem)}>
                  Inspect tool call
                </Button>
              )}
            </section>
          )}
          <div className={approval.diff ? "mt-4 min-h-0" : "min-h-0"}>
            {approval.diff ? (
              <ApprovalPatch patch={approval.diff} />
            ) : approval.preview ? (
              <CodeWell>
                {approval.preview}
              </CodeWell>
            ) : (
              <div className="py-6 text-sm text-muted italic">
                No diff or preview available.
              </div>
            )}
          </div>
        </>
      )}
    </SystemSheet>
  );
}
