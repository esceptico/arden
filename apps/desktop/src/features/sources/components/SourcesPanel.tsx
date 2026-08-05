import { useMemo, type ComponentType } from "react";
import {
  CalendarDays,
  FileText,
  Globe,
  Library,
  Link2,
  Mail,
  MessageSquare,
} from "@/components/icons";
import { EmptyState } from "@/components/ui/EmptyState";
import { ICON } from "@/lib/icons";
import { sourceInspectorSelection } from "@/features/sources/lib/sourceInspector";
import {
  groupInspectedSources,
  type SourceProviderGroup,
} from "@/features/sources/lib/sourceGroups";
import { useStore, type ActivityItem, type SourceRef } from "@/stores";
import { browserOpenableSourceUrl } from "@/stores/sourceRefs";

type ShowToolCall = (toolCall: ActivityItem) => void;

export function SourcesPanel() {
  const sourceRefsRevision = useStore((state) => state.sourceRefsRevision);
  const sourceTurnId = useStore((state) => state.sourceTurnId);
  const setViewingTool = useStore((state) => state.setViewingTool);
  const showReasoning = useStore((state) => state.prefs.showReasoning);
  const selection = useMemo(
    () => {
      const { messages, order } = useStore.getState();
      return sourceInspectorSelection({ messages, order, sourceTurnId, showReasoning });
    },
    [sourceRefsRevision, sourceTurnId, showReasoning],
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
  const sourceCount = groups.reduce((count, group) => count + group.sourceCount, 0);
  return (
    <section className="dp-peek-section">
      <div className="dp-peek-section-head">
        Sources <span>{sourceCount}</span>
      </div>
      {groups.map((group) => (
        <ProviderSection key={group.key} group={group} onShowCall={onShowCall} />
      ))}
    </section>
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
  const sources = group.actions.flatMap((action) =>
    action.calls.flatMap((call) =>
      call.sources.map((source) => ({
        key: `${call.key}\u0000${sourceKey(source)}`,
        source,
        toolCall: call.toolCall,
      })),
    ),
  );

  return (
    <section className="source-group">
      <div className="source-group-title">
        <span aria-hidden>
          <Icon size={ICON.SM} strokeWidth={1.75} />
        </span>
        <span>{providerLabel(group.provider)}</span>
        <span className="phase-count">{group.sourceCount}</span>
      </div>
      <div className="source-call">
        {sources.map(({ key, source, toolCall }) => (
          <SourceRow key={key} source={source} toolCall={toolCall} onShowCall={onShowCall} />
        ))}
      </div>
    </section>
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
  if (openUrl) {
    return (
      <a
        href={openUrl}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={`Open ${source.title} in browser`}
        className="source-link"
      >
        {source.title}
      </a>
    );
  }
  if (toolCall) {
    return (
      <button
        type="button"
        onClick={() => onShowCall(toolCall)}
        aria-label={`Show tool call for ${source.title}`}
        className="source-link"
      >
        {source.title}
      </button>
    );
  }
  return (
    <span className="source-link">{source.title}</span>
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

function providerLabel(provider: string): string {
  switch (provider.toLowerCase()) {
    case "memory": return "Memory";
    case "gmail": return "Gmail";
    case "calendar": return "Calendar";
    case "filesystem":
    case "file": return "Workspace";
    case "web": return "Web";
    case "slack": return "Slack";
    default: return provider;
  }
}

function sourceKey(source: SourceRef): string {
  return `${source.provider.length}:${source.provider}${source.ref}`;
}
