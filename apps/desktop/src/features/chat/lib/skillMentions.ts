import type { SkillDescriptor } from "@/api/types";

/** Split content on /skill-name mentions (start of text or after
 *  whitespace, matching the composer + server rule) so each mention can
 *  render as an inline token exactly where it sits in the sentence.
 *  Returns null when no known skill is mentioned — callers keep their
 *  plain-text path untouched for every other string. */
export function splitSkillMentions(
  content: string,
  skills: SkillDescriptor[],
): Array<string | SkillDescriptor> | null {
  const re = /(?:^|(?<=\s))\/([a-z][a-z0-9-]{0,47})(?![\w/-])/g;
  const segments: Array<string | SkillDescriptor> = [];
  let cursor = 0;
  let found = false;
  for (const match of content.matchAll(re)) {
    const skill = skills.find((s) => s.name === match[1]);
    if (!skill) continue;
    found = true;
    if (match.index! > cursor) segments.push(content.slice(cursor, match.index));
    segments.push(skill);
    cursor = match.index! + match[0].length;
  }
  if (!found) return null;
  if (cursor < content.length) segments.push(content.slice(cursor));
  return segments;
}
