import { useMemo } from "react";
import { ArrowRight, Bot } from "@/components/icons";
import clsx from "clsx";
import { useStore, type ActivityItem } from "@/stores";
import { activityItemStatus, friendlyAgentLabel, isAgent } from "@/lib/agent";
import { ICON } from "@/lib/icons";
import { childAgentTreeDetail } from "@/features/chat/lib/toolViewer";

export function ActivityTree({
  descendants,
  rootId,
  rootDepth,
}: {
  descendants: ActivityItem[];
  rootId: string;
  rootDepth: number;
}) {
  const setViewing = useStore((s) => s.setViewingTool);
  const childrenByParent = useMemo(() => {
    const map = new Map<string, ActivityItem[]>();
    for (const it of descendants) {
      if (!it.parentToolId) continue;
      const arr = map.get(it.parentToolId) ?? [];
      arr.push(it);
      map.set(it.parentToolId, arr);
    }
    return map;
  }, [descendants]);

  return (
    <div className="rounded-[10px] border border-line-soft bg-surface overflow-hidden">
      <ActivityTreeBranch
        parentId={rootId}
        baseDepth={rootDepth + 1}
        childrenByParent={childrenByParent}
        onPick={setViewing}
      />
    </div>
  );
}

function ActivityTreeBranch({
  parentId,
  baseDepth,
  childrenByParent,
  onPick,
}: {
  parentId: string;
  baseDepth: number;
  childrenByParent: Map<string, ActivityItem[]>;
  onPick: (item: ActivityItem) => void;
}) {
  const kids = childrenByParent.get(parentId);
  if (!kids || kids.length === 0) return null;
  return (
    <ul className="m-0 p-0 list-none">
      {kids.map((child) => (
        <ActivityTreeNode
          key={child.id}
          item={child}
          baseDepth={baseDepth}
          childrenByParent={childrenByParent}
          onPick={onPick}
        />
      ))}
    </ul>
  );
}

function ActivityTreeNode({
  item,
  baseDepth,
  childrenByParent,
  onPick,
}: {
  item: ActivityItem;
  baseDepth: number;
  childrenByParent: Map<string, ActivityItem[]>;
  onPick: (item: ActivityItem) => void;
}) {
  const indent = ((item.depth ?? baseDepth) - baseDepth) * 16 + 12;
  const agent = isAgent(item);
  const label = agent ? item.displayName ?? friendlyAgentLabel(item.kind) : item.kind;
  const detail = agent ? childAgentTreeDetail(item) : item.target;
  const running = activityItemStatus(item) === "ongoing";
  return (
    <li className="m-0 p-0">
      <button
        type="button"
        onClick={() => onPick(item)}
        style={{ paddingLeft: indent }}
        className="app-row flex items-center gap-2 w-full pr-3 py-1.5 text-left bg-transparent border-0 text-ink-soft min-w-0"
      >
        {agent ? (
          <span
            aria-hidden
            className="grid place-items-center w-[16px] h-[16px] rounded-[4px] bg-accent-soft text-accent-strong shrink-0"
          >
            <Bot size={ICON.XS} strokeWidth={2} />
          </span>
        ) : (
          <ArrowRight size={ICON.XS} strokeWidth={2} className="text-whisper shrink-0" />
        )}
        <span
          className={clsx(
            "text-sm shrink-0",
            agent ? "font-medium text-ink-soft" : "font-mono text-ink-soft",
          )}
        >
          {label}
        </span>
        {detail && (
          <span
            className={clsx(
              "truncate min-w-0 flex-1 text-xs",
              agent ? "text-faint" : "text-faint font-mono",
            )}
          >
            {detail}
          </span>
        )}
        {running && (
          <span className="text-2xs uppercase tracking-[var(--tracking-caps)] text-faint shrink-0">
            running
          </span>
        )}
      </button>
      <ActivityTreeBranch
        parentId={item.id}
        baseDepth={baseDepth}
        childrenByParent={childrenByParent}
        onPick={onPick}
      />
    </li>
  );
}
