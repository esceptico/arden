import type { Role } from "@/stores";
import { isHiddenTurnBoundary } from "@/lib/messageVisibility";

export type MessageSegment = { userId: string | null; childIds: string[] };

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

  for (let i = 0; i < ids.length; i += 1) {
    const id = ids[i];
    const role = roles[i];

    if (isHiddenTurnBoundary({ role, isMeta: metaFlags[i] })) {
      if (current) out.push(current);
      current = null;
      continue;
    }
    if (!visible.has(id)) continue;

    if (role === "user") {
      if (current) out.push(current);
      current = { userId: id, childIds: [] };
    } else if (role === "status" || role === "error") {
      if (current) {
        out.push(current);
        current = null;
      }
      out.push({ userId: null, childIds: [id] });
    } else {
      if (!current) current = { userId: null, childIds: [] };
      current.childIds.push(id);
    }
  }

  if (current) out.push(current);
  return out;
}
