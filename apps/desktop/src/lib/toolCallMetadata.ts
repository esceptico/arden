export const DISPLAY_TITLE_ARG = "_display_title";

export interface ProjectedToolCallArguments {
  args: string;
  displayTitle?: string;
}

/** Split presentation metadata from behavior arguments after a complete JSON
 * object arrives. Partial streaming input stays untouched until it is valid. */
export function projectToolCallArguments(rawArgs: string | undefined): ProjectedToolCallArguments {
  const args = rawArgs ?? "";
  try {
    const parsed = JSON.parse(args || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return { args };
    if (!Object.hasOwn(parsed, DISPLAY_TITLE_ARG)) return { args };

    const record = parsed as Record<string, unknown>;
    const rawTitle = record[DISPLAY_TITLE_ARG];
    const behaviorArgs = Object.fromEntries(
      Object.entries(record).filter(([key]) => key !== DISPLAY_TITLE_ARG),
    );
    const displayTitle =
      typeof rawTitle === "string" && rawTitle.trim().length > 0
        ? rawTitle.trim()
        : undefined;
    return {
      args: JSON.stringify(behaviorArgs),
      ...(displayTitle ? { displayTitle } : {}),
    };
  } catch {
    return { args };
  }
}
