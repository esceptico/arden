import type { Role } from "@/stores";
import { isHiddenTurnBoundary } from "@/lib/messageVisibility";

export type MessageSegment = {
  turnId: string | null;
  userId: string | null;
  childIds: string[];
};

export function messageSegments({
  ids,
  roles,
  metaFlags = [],
  visibleIds,
}: {
  ids: string[];
  roles: (Role | null)[];
  metaFlags?: boolean[];
  visibleIds: string[];
}): MessageSegment[] {
  const visible = new Set(visibleIds);
  const out: MessageSegment[] = [];
  let current: MessageSegment | null = null;
  const flush = () => {
    if (current && (current.userId !== null || current.childIds.length > 0)) out.push(current);
    current = null;
  };

  for (let i = 0; i < ids.length; i += 1) {
    const id = ids[i];
    const role = roles[i];

    if (isHiddenTurnBoundary({ role, isMeta: metaFlags[i] })) {
      flush();
      current = { turnId: id, userId: null, childIds: [] };
      continue;
    }
    if (!visible.has(id)) continue;

    if (role === "user") {
      flush();
      current = { turnId: id, userId: id, childIds: [] };
    } else if (role === "status" || role === "error") {
      flush();
      out.push({ turnId: null, userId: null, childIds: [id] });
    } else {
      if (!current) current = { turnId: null, userId: null, childIds: [] };
      current.childIds.push(id);
    }
  }

  flush();
  return out;
}
