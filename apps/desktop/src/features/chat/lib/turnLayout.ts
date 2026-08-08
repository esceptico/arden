export interface TurnLayoutInput {
  children: TurnLayoutChild[];
  isDone: boolean;
}

export interface TurnLayoutChild {
  id: string;
  role: string | null;
}

export interface TurnLayoutResult {
  workIds: string[];
  afterWorkIds: string[];
  finalAssistantId: string | null;
}

export interface RollingWorkEntry {
  id: string;
  rowCount: number;
}

export interface RollingWorkWindowEntry {
  id: string;
  rowLimit: number;
}

export function turnLayout({
  children,
  isDone,
}: TurnLayoutInput): TurnLayoutResult {
  const childIds = children.map((child) => child.id);
  const lastAssistantId = () => {
    for (let i = children.length - 1; i >= 0; i--) {
      if (children[i].role === "assistant") return children[i].id;
    }
    return null;
  };

  if (!isDone) {
    const workIds = children
      .filter((child) => isLiveWorkRole(child.role))
      .map((child) => child.id);
    return {
      workIds,
      afterWorkIds: children
        .filter((child) => !isLiveWorkRole(child.role))
        .map((child) => child.id),
      finalAssistantId: lastAssistantId(),
    };
  }

  const firstWorkIndex = children.findIndex((child) => isWorkRole(child.role));
  if (firstWorkIndex < 0) {
    return {
      workIds: [],
      afterWorkIds: childIds,
      finalAssistantId: lastAssistantId(),
    };
  }

  // The final answer is the last assistant message that appears after work
  // has started. If activity follows it, keep that trailing area inline so
  // replayed history preserves the original order.
  let finalAssistantIndex = -1;
  for (let i = children.length - 1; i > firstWorkIndex; i--) {
    if (children[i].role === "assistant") {
      finalAssistantIndex = i;
      break;
    }
  }

  if (finalAssistantIndex < 0) {
    return {
      workIds: childIds,
      afterWorkIds: [],
      finalAssistantId: null,
    };
  }

  if (finalAssistantIndex < children.length - 1) {
    return {
      workIds: childIds.slice(0, finalAssistantIndex),
      afterWorkIds: childIds.slice(finalAssistantIndex),
      finalAssistantId: children[finalAssistantIndex].id,
    };
  }

  return {
    workIds: childIds.filter((_, index) => index !== finalAssistantIndex),
    afterWorkIds: [children[finalAssistantIndex].id],
    finalAssistantId: children[finalAssistantIndex].id,
  };
}

function isWorkRole(role: string | null): boolean {
  return role === "activity";
}

function isLiveWorkRole(role: string | null): boolean {
  return role === "activity" || role === "reasoning";
}

/** Keep one rolling row budget across reasoning and tool groups. */
export function rollingWorkWindow(
  entries: RollingWorkEntry[],
  maxRows: number,
): RollingWorkWindowEntry[] {
  let remaining = maxRows;
  const visible: RollingWorkWindowEntry[] = [];

  for (let index = entries.length - 1; index >= 0 && remaining > 0; index -= 1) {
    const entry = entries[index];
    const rowLimit = Math.min(Math.max(1, entry.rowCount), remaining);
    visible.unshift({ id: entry.id, rowLimit });
    remaining -= rowLimit;
  }

  return visible;
}
