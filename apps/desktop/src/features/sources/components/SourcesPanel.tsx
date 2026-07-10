import { useMemo, useState, type ComponentType } from "react";
import {
  CalendarDays,
  ChevronRight,
  ExternalLink,
  FileText,
  Globe,
  Library,
  Link2,
  Mail,
  MessageSquare,
} from "lucide-react";
import { EmptyState } from "@/components/ui/EmptyState";
import { ICON } from "@/lib/icons";
import { sourceInspectorSelection } from "@/features/sources/lib/sourceInspector";
import {
  groupInspectedSources,
  type SourceActionGroup,
  type SourceCallGroup,
  type SourceProviderGroup,
} from "@/features/sources/lib/sourceGroups";
import { useStore, type ActivityItem, type SourceRef } from "@/stores";
import { browserOpenableSourceUrl } from "@/stores/sourceRefs";

type ShowToolCall = (toolCall: ActivityItem) => void;

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
  const groups = useMemo(
    () => groupInspectedSources(selection.sources),
    [selection.sources],
  );

  if (groups.length === 0) {
    return (
      <div className="grid min-h-[120px] place-items-center">
        <EmptyState size="sm" icon={Library}>
          No sources for this turn.
        </EmptyState>
      </div>
    );
  }

  return (
    <GroupedSources
      key={selection.turnId ?? "latest"}
      groups={groups}
      onShowCall={setViewingTool}
    />
  );
}

function GroupedSources({
  groups,
  onShowCall,
}: {
  groups: SourceProviderGroup[];
  onShowCall: ShowToolCall;
}) {
  return (
    <div className="flex flex-col">
      {groups.map((group) => (
        <ProviderSection key={group.key} group={group} onShowCall={onShowCall} />
      ))}
    </div>
  );
}

function ProviderSection({
  group,
  onShowCall,
}: {
  group: SourceProviderGroup;
  onShowCall: ShowToolCall;
}) {
  const Icon = providerIcon(group.provider);
  return (
    <section className="border-b border-line-soft py-2 last:border-b-0">
      <header className="flex min-w-0 items-center gap-2.5 px-2 py-2">
        <span
          aria-hidden
          className="grid size-7 shrink-0 place-items-center rounded-lg bg-surface-soft text-faint"
        >
          <Icon size={ICON.SM} strokeWidth={1.75} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-ink">{group.provider}</span>
          <span className="block text-xs text-muted">
            {countLabel(group.callCount, "call")} · {countLabel(group.sourceCount, "source")}
          </span>
        </span>
      </header>
      <div className="ml-4 border-l border-line-soft pl-2">
        {group.actions.map((action) => (
          <ActionDisclosure key={action.key} action={action} onShowCall={onShowCall} />
        ))}
      </div>
    </section>
  );
}

function ActionDisclosure({
  action,
  onShowCall,
}: {
  action: SourceActionGroup;
  onShowCall: ShowToolCall;
}) {
  const [open, setOpen] = useState(true);
  return (
    <section className="py-0.5">
      <button
        type="button"
        aria-expanded={open}
        aria-label={`${open ? "Collapse" : "Expand"} source action ${action.label}`}
        onClick={() => setOpen((value) => !value)}
        className="app-row flex w-full min-w-0 items-center gap-1.5 rounded-md px-1.5 py-1.5 text-left hover:bg-surface-soft/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <ChevronRight
          aria-hidden
          size={ICON.XS}
          className={`shrink-0 text-faint transition-transform duration-fast ${open ? "rotate-90" : ""}`}
        />
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink">{action.label}</span>
        <span className="shrink-0 text-xs text-muted">
          {countLabel(action.callCount, "call")} · {countLabel(action.sourceCount, "source")}
        </span>
      </button>
      {open && (
        <div className="ml-3 border-l border-line-soft pl-1.5">
          {action.calls.map((call) => (
            <CallDisclosure key={call.key} call={call} onShowCall={onShowCall} />
          ))}
        </div>
      )}
    </section>
  );
}

function CallDisclosure({
  call,
  onShowCall,
}: {
  call: SourceCallGroup;
  onShowCall: ShowToolCall;
}) {
  const [open, setOpen] = useState(call.sourceCount === 1);
  return (
    <div className="py-0.5">
      <button
        type="button"
        aria-expanded={open}
        aria-label={`${open ? "Collapse" : "Expand"} source call ${call.target}`}
        onClick={() => setOpen((value) => !value)}
        className="app-row flex w-full min-w-0 items-center gap-1.5 rounded-md px-1.5 py-1.5 text-left hover:bg-surface-soft/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <ChevronRight
          aria-hidden
          size={ICON.XS}
          className={`shrink-0 text-faint transition-transform duration-fast ${open ? "rotate-90" : ""}`}
        />
        <span className="min-w-0 flex-1 truncate text-xs text-ink-soft">{call.target}</span>
        <span className="shrink-0 text-xs text-muted">{countLabel(call.sourceCount, "source")}</span>
      </button>
      {open && <SourceRows call={call} onShowCall={onShowCall} />}
    </div>
  );
}

function SourceRows({
  call,
  onShowCall,
}: {
  call: SourceCallGroup;
  onShowCall: ShowToolCall;
}) {
  const [showAll, setShowAll] = useState(false);
  const visibleSources = showAll ? call.sources : call.sources.slice(0, 5);
  const hiddenCount = call.sources.length - visibleSources.length;
  return (
    <div className="ml-3 flex flex-col gap-0.5 py-0.5">
      {visibleSources.map((source) => (
        <SourceRow
          key={sourceKey(source)}
          source={source}
          toolCall={call.toolCall}
          onShowCall={onShowCall}
        />
      ))}
      {hiddenCount > 0 && (
        <button
          type="button"
          onClick={() => setShowAll(true)}
          className="app-row rounded-md px-2 py-1.5 text-left text-xs font-medium text-muted hover:bg-surface-soft/60 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          Show {hiddenCount} more
        </button>
      )}
    </div>
  );
}

function SourceRow({
  source,
  toolCall,
  onShowCall,
}: {
  source: SourceRef;
  toolCall?: ActivityItem;
  onShowCall: ShowToolCall;
}) {
  const openUrl = browserOpenableSourceUrl(source.url);
  return (
    <div className="app-row flex min-w-0 items-start gap-2 rounded-md px-2 py-1.5 hover:bg-surface-soft/60">
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-medium text-ink">{source.title}</span>
        <span className="block truncate text-xs text-muted">{secondaryIdentity(source, openUrl)}</span>
      </span>
      {openUrl && (
        <a
          href={openUrl}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Open ${source.title} in browser`}
          className="grid size-6 shrink-0 place-items-center rounded-md text-faint transition-colors hover:bg-surface-soft hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <ExternalLink aria-hidden size={ICON.XS} strokeWidth={2} />
        </a>
      )}
      {toolCall && (
        <button
          type="button"
          onClick={() => onShowCall(toolCall)}
          aria-label={`Show tool call for ${source.title}`}
          className="shrink-0 rounded-md px-1.5 py-1 text-xs font-medium text-muted transition-colors hover:bg-surface-soft hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          Show call
        </button>
      )}
    </div>
  );
}

function providerIcon(provider: string): ComponentType<{
  size?: number;
  strokeWidth?: number;
}> {
  const normalized = provider.toLowerCase();
  if (normalized === "slack") return MessageSquare;
  if (normalized === "gmail") return Mail;
  if (normalized === "calendar") return CalendarDays;
  if (normalized === "filesystem" || normalized === "file") return FileText;
  if (normalized === "web") return Globe;
  return Link2;
}

function countLabel(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

function secondaryIdentity(source: SourceRef, openUrl: string | undefined): string {
  if (openUrl) {
    try {
      const hostname = new URL(openUrl).hostname.replace(/^www\./, "");
      if (hostname) return hostname;
    } catch {
      // Browser-openable URLs are checked at the store boundary. Keep the
      // opaque identity if hostname parsing still fails.
    }
  }
  return `${source.kind} · ${source.ref}`;
}

function sourceKey(source: SourceRef): string {
  return `${source.provider.length}:${source.provider}${source.ref}`;
}
