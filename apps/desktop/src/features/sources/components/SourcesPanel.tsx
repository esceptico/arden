import { useMemo, type ComponentType } from "react";
import {
  CalendarDays,
  ExternalLink,
  FileText,
  Globe,
  Library,
  Link2,
  Mail,
  MessageSquare,
} from "lucide-react";
import { useStore, type SourceRef } from "@/stores";
import { EmptyState } from "@/components/ui/EmptyState";
import { ICON } from "@/lib/icons";
import { sourceInspectorSelection } from "@/features/sources/lib/sourceInspector";
import { browserOpenableSourceUrl } from "@/stores/sourceRefs";

export function SourcesPanel() {
  const sourceRefsRevision = useStore((state) => state.sourceRefsRevision);
  const sourceTurnId = useStore((state) => state.sourceTurnId);
  const setViewingTool = useStore((state) => state.setViewingTool);
  const selection = useMemo(
    () => {
      const { messages, order } = useStore.getState();
      return sourceInspectorSelection({ messages, order, sourceTurnId });
    },
    [sourceRefsRevision, sourceTurnId],
  );

  if (selection.sources.length === 0) {
    return (
      <div className="grid min-h-[120px] place-items-center">
        <EmptyState size="sm" icon={Library}>
          No sources for this turn.
        </EmptyState>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-0.5">
      {selection.sources.map(({ source, toolCall }) => {
        const Icon = providerIcon(source);
        const openUrl = browserOpenableSourceUrl(source.url);
        const content = (
          <>
            <span
              aria-hidden
              className="grid size-7 shrink-0 place-items-center rounded-lg bg-surface-soft text-faint"
            >
              <Icon size={ICON.SM} strokeWidth={1.75} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium text-ink">{source.title}</span>
              <span className="block truncate text-xs text-muted">{secondaryIdentity(source, openUrl)}</span>
            </span>
          </>
        );

        return (
          <div
            key={sourceKey(source)}
            className="app-row flex min-w-0 items-start gap-2.5 rounded-lg px-2 py-2 hover:bg-surface-soft/60"
          >
            {content}
            {openUrl && (
              <a
                href={openUrl}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={`Open ${source.title} in browser`}
                className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-md text-faint transition-colors hover:bg-surface-soft hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <ExternalLink aria-hidden size={ICON.XS} strokeWidth={2} />
              </a>
            )}
            {toolCall && (
              <button
                type="button"
                onClick={() => setViewingTool(toolCall)}
                aria-label={`Show tool call for ${source.title}`}
                className="mt-0.5 shrink-0 rounded-md px-1.5 py-1 text-xs font-medium text-muted transition-colors hover:bg-surface-soft hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                Show call
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

function providerIcon(source: SourceRef): ComponentType<{
  size?: number;
  strokeWidth?: number;
}> {
  const provider = source.provider.toLowerCase();
  if (provider === "slack") return MessageSquare;
  if (provider === "gmail") return Mail;
  if (provider === "calendar") return CalendarDays;
  if (provider === "filesystem" || provider === "file") return FileText;
  if (provider === "web" || source.url) return Globe;
  return Link2;
}

function secondaryIdentity(source: SourceRef, openUrl: string | undefined): string {
  if (openUrl) {
    try {
      const hostname = new URL(openUrl).hostname.replace(/^www\./, "");
      if (hostname) return `${source.provider} · ${hostname}`;
    } catch {
      // URLs are normalized at the store boundary; keep the opaque identity
      // if the browser parser still cannot extract a hostname.
    }
  }
  return `${source.provider} · ${source.kind} · ${source.ref}`;
}

function sourceKey(source: SourceRef): string {
  return `${source.provider.length}:${source.provider}${source.ref}`;
}
