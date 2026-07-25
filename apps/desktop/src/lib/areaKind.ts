import type { AreaAsk } from "@/api/areas";

/** One vocabulary for an ask's kind, shared by Home's FocusRow and the
 *  room's focused request deck so an ask presents its kind identically in
 *  both places.
 *  A quiet uppercase text label — no severity dot (the surface was peppered
 *  with dots; the word carries the meaning and reads in any theme). */
export const ASK_KIND: Record<AreaAsk["kind"], { label: string }> = {
  notify: { label: "fyi" },
  question: { label: "question" },
  review: { label: "review" },
};

/** The server writes compact asks as “headline — context”. Keep that
 * hierarchy identical wherever an ask appears instead of splitting copy in
 * each surface. */
export function splitAreaAsk(text: string): { title: string; detail: string | null } {
  const splitAt = text.indexOf(" — ");
  if (splitAt === -1) return { title: text, detail: null };
  const detail = text.slice(splitAt + 3);
  return {
    title: text.slice(0, splitAt),
    detail: detail ? detail.charAt(0).toUpperCase() + detail.slice(1) : null,
  };
}
