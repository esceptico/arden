export interface ReasoningStep {
  /** null — prose with no heading (full chain-of-thought models). */
  label: string | null;
  body: string;
}

/** Line-leading `**Title**` — the shape OpenAI reasoning summaries arrive in.
 *  The marker must open a line, so bold used mid-sentence never splits. */
const STEP_LABEL = /(?:^|\n)[ \t]*\*\*([^*\n]+?)\*\*[ \t]*/g;

/** A trailing `**Half-typed` with no close yet is the next label mid-stream —
 *  surfaced as its own step so the timeline types instead of flashing literal
 *  asterisks while the title streams in. */
const PARTIAL_LABEL = /(?:^|\n)[ \t]*\*\*([^*\n]*)$/;

/** OpenAI emits `**Title**\n\n<!-- -->` for a part it has a heading but no
 *  body for. Markdown renders the comment as nothing, so keeping it would
 *  leave a step that measures as "has a body" while showing none. */
const BODY_PLACEHOLDER = /^<!--\s*-->$/;

/** A title glued to the previous part: a bold run that starts mid-line but
 *  ends its line anyway. Providers that never separate their summary parts
 *  produce exactly this, and without the break it renders as inline bold at
 *  the tail of the previous step instead of starting its own. A bold run used
 *  mid-sentence is followed by more text on the same line, so it is untouched. */
const GLUED_LABEL = /(?<=[^\n])(\*\*[^*\n]+\*\*)(?=\n|$)/g;

export function parseReasoningSteps(rawContent: string): ReasoningStep[] {
  const content = rawContent.replace(GLUED_LABEL, "\n\n$1");
  const pattern = new RegExp(STEP_LABEL.source, "g");
  const steps: ReasoningStep[] = [];
  let label: string | null = null;
  let cursor = 0;

  const push = (stepLabel: string | null, rawBody: string) => {
    const body = BODY_PLACEHOLDER.test(rawBody) ? "" : rawBody;
    if (stepLabel !== null || body) steps.push({ label: stepLabel, body });
  };

  for (let match = pattern.exec(content); match; match = pattern.exec(content)) {
    push(label, content.slice(cursor, match.index).trim());
    label = match[1].trim();
    cursor = pattern.lastIndex;
  }

  let tail = content.slice(cursor);
  const partial = PARTIAL_LABEL.exec(tail);
  if (partial) tail = tail.slice(0, partial.index);
  push(label, tail.trim());
  if (partial) steps.push({ label: partial[1], body: "" });
  return steps;
}
