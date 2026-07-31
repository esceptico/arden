import { useEffect, useState } from "react";
import { readMemoryArtifactDetail } from "@/api/memoryArtifacts";
import { useStore, getState } from "@/stores";
import { Button } from "@/components/ui/Button";
import { IconButton } from "@/components/ui/IconButton";
import { PeekSurface } from "@/components/workspace/PeekSurface";
import { Markdown } from "@/components/ui/Markdown";
import { X } from "@/components/icons";
import { ICON } from "@/lib/icons";
import { formatRelativePast } from "@/lib/format";

type Phase =
  | { kind: "loading" }
  | { kind: "ready"; title: string; content: string; updated: string | null }
  | { kind: "error"; message: string };

/**
 * The area's page, read in place. The room only ever showed the path as dead
 * text, so the one artifact the custodian actually writes was unreachable
 * without hunting for it in Memory.
 *
 * Read-only on purpose: editing belongs to Memory, and this peek hands over
 * to exactly this page there rather than dropping the user at the README.
 */
export function AreaPagePeek({
  path,
  open,
  onClose,
}: {
  path: string;
  open: boolean;
  onClose: () => void;
}) {
  const openMemory = useStore((s) => s.openMemory);
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });

  useEffect(() => {
    if (!open) return;
    let live = true;
    setPhase({ kind: "loading" });
    readMemoryArtifactDetail(getState().config, path)
      .then(({ artifact }) => {
        if (!live) return;
        setPhase({
          kind: "ready",
          title: artifact.title,
          content: artifact.content,
          updated: artifact.updatedAt ?? null,
        });
      })
      .catch((error: unknown) => {
        if (!live) return;
        setPhase({
          kind: "error",
          message: error instanceof Error ? error.message : "Couldn’t read this page.",
        });
      });
    return () => {
      live = false;
    };
  }, [open, path]);

  return (
    <PeekSurface
      open={open}
      onClose={onClose}
      ariaLabel={`${path} preview`}
      className="area-page-peek surface-peek"
      closeOnOutsidePointerDown
      outsidePointerDownIgnoreSelector="[data-area-page-owner]"
    >
      <header className="area-page-peek__head">
        <span>
          <b>{phase.kind === "ready" ? phase.title : path}</b>
          <small>
            {path}
            {phase.kind === "ready" && phase.updated
              ? ` · edited ${formatRelativePast(phase.updated)} ago`
              : ""}
          </small>
        </span>
        <IconButton aria-label="Close" onClick={onClose} size="sm">
          <X size={ICON.SM} aria-hidden />
        </IconButton>
      </header>

      <div className="area-page-peek__body scroll-fade" tabIndex={0}>
        {phase.kind === "loading" ? (
          <p className="area-page-peek__state" role="status">Reading the page…</p>
        ) : phase.kind === "error" ? (
          <p className="area-page-peek__state">{phase.message}</p>
        ) : phase.content.trim() ? (
          <Markdown content={phase.content} codeChrome={false} />
        ) : (
          <p className="area-page-peek__state">
            The page exists but is still empty. The custodian fills it as it learns.
          </p>
        )}
      </div>

      <footer className="area-page-peek__foot">
        <Button
          variant="secondary"
          size="sm"
          onClick={() => {
            onClose();
            openMemory(path);
          }}
        >
          Open in Memory
        </Button>
      </footer>
    </PeekSurface>
  );
}
