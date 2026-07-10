import type { AreaAsk } from "@/api/areas";

/** One vocabulary for an ask's kind, shared by Home's FocusRow and the
 *  room's AskCard so an ask presents its kind identically in both places.
 *  A quiet uppercase text label — no severity dot (the surface was peppered
 *  with dots; the word carries the meaning and reads in any theme). */
export const ASK_KIND: Record<AreaAsk["kind"], { label: string }> = {
  notify: { label: "fyi" },
  question: { label: "question" },
  review: { label: "review" },
};
