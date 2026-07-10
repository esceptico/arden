import type { SourceRef, UiMessage } from "@/stores/types";

const PROVIDER_MAX_CHARS = 64;
const KIND_MAX_CHARS = 64;
const REF_MAX_CHARS = 2048;
const TITLE_MAX_CHARS = 256;
const URL_MAX_CHARS = 4096;
const SOURCE_REFS_MAX = 50;

export function normalizeSourceRefs(raw: unknown, toolCallId?: string): SourceRef[] {
  if (!Array.isArray(raw)) return [];

  const normalized: SourceRef[] = [];
  const seen = new Set<string>();
  for (const value of raw) {
    if (!isRecord(value)) continue;
    const provider = trimmedString(value.provider);
    const kind = trimmedString(value.kind);
    const ref = trimmedString(value.ref);
    const title = trimmedString(value.title);
    if (
      !provider ||
      codePointLength(provider) > PROVIDER_MAX_CHARS ||
      !kind ||
      codePointLength(kind) > KIND_MAX_CHARS ||
      !ref ||
      codePointLength(ref) > REF_MAX_CHARS ||
      !title
    ) {
      continue;
    }
    if (hasHttpCredentials(ref)) continue;
    const safeTitle = hasHttpCredentials(title) ? ref : title;

    const identity = sourceIdentity(provider, ref);
    if (seen.has(identity)) continue;

    const url = safeUrl(value.url);
    normalized.push({
      provider,
      kind,
      ref,
      title: truncateCodePoints(safeTitle, TITLE_MAX_CHARS),
      ...(url ? { url } : {}),
      ...(toolCallId === undefined ? {} : { toolCallId }),
    });
    seen.add(identity);
    if (normalized.length === SOURCE_REFS_MAX) break;
  }
  return normalized;
}

export function sourceRefsFromToolResultData(data: unknown, toolCallId: string): SourceRef[] {
  return isRecord(data) ? normalizeSourceRefs(data.source_refs, toolCallId) : [];
}

export function mergeSourceRefs(
  first: readonly SourceRef[] | undefined,
  second: readonly SourceRef[] | undefined,
): SourceRef[] | undefined {
  if (first === undefined && second === undefined) return undefined;
  const merged: SourceRef[] = [];
  const seen = new Set<string>();
  for (const source of [...(first ?? []), ...(second ?? [])]) {
    const identity = sourceIdentity(source.provider, source.ref);
    if (seen.has(identity)) continue;
    seen.add(identity);
    merged.push(source);
    if (merged.length === SOURCE_REFS_MAX) break;
  }
  return merged;
}

export function sourceRefsForTurn(
  messages: ReadonlyMap<string, UiMessage>,
  childIds: readonly string[],
): SourceRef[] {
  const refs: SourceRef[] = [];
  const seen = new Set<string>();
  for (const childId of childIds) {
    const activity = messages.get(childId)?.activity;
    if (!activity) continue;
    for (const item of activity.items) {
      for (const source of item.sourceRefs ?? []) {
        const identity = sourceIdentity(source.provider, source.ref);
        if (seen.has(identity)) continue;
        seen.add(identity);
        refs.push({ ...source, toolCallId: item.id });
      }
    }
  }
  return refs;
}

function safeUrl(value: unknown): string | undefined {
  const url = trimmedString(value);
  if (!url || codePointLength(url) > URL_MAX_CHARS) return undefined;
  const validationCandidate = url.replace(/[\r\n\t]/g, "").replace(/^[\u0000-\u0020]+/, "");
  const separator = validationCandidate.indexOf("://");
  const scheme = separator > 0 ? validationCandidate.slice(0, separator).toLowerCase() : "";
  if (scheme !== "http" && scheme !== "https") return undefined;

  const rawAuthority = authority(validationCandidate, separator + 3);
  if (!rawAuthority || !hasSafeNormalizedAuthority(rawAuthority) || rawAuthority.includes("@")) {
    return undefined;
  }
  const hostname = hostnameFromAuthority(rawAuthority);
  if (!hostname) return undefined;
  return url;
}

export function browserOpenableSourceUrl(value: unknown): string | undefined {
  const url = trimmedString(value);
  if (!url) return undefined;
  try {
    const parsed = new URL(url);
    if (
      (parsed.protocol !== "http:" && parsed.protocol !== "https:") ||
      !parsed.hostname ||
      parsed.username ||
      parsed.password
    ) {
      return undefined;
    }
    return url;
  } catch {
    return undefined;
  }
}

function hasHttpCredentials(value: string): boolean {
  try {
    const parsed = new URL(value);
    return (
      (parsed.protocol === "http:" || parsed.protocol === "https:") &&
      Boolean(parsed.username || parsed.password)
    );
  } catch {
    return false;
  }
}

function codePointLength(value: string): number {
  return Array.from(value).length;
}

function truncateCodePoints(value: string, max: number): string {
  return Array.from(value).slice(0, max).join("");
}

function authority(url: string, start: number): string {
  const endOffset = url.slice(start).search(/[/?#]/);
  return endOffset < 0 ? url.slice(start) : url.slice(start, start + endOffset);
}

function hostnameFromAuthority(value: string): string | undefined {
  if (value.startsWith("[")) {
    const end = value.indexOf("]");
    if (end <= 1) return undefined;
    const hostname = value.slice(1, end);
    if (/^v[0-9a-f]+\..+$/i.test(hostname)) return hostname;
    try {
      new URL(`http://[${hostname}]/`);
      return hostname;
    } catch {
      return undefined;
    }
  }
  if (value.includes("[") || value.includes("]")) return undefined;
  return value.split(":", 1)[0] || undefined;
}

function hasSafeNormalizedAuthority(value: string): boolean {
  const delimiterFree = value.replace(/[@:#?]/g, "");
  const normalized = delimiterFree.normalize("NFKC");
  return normalized === delimiterFree || !/[/?#@:]/.test(normalized);
}

function sourceIdentity(provider: string, ref: string): string {
  return `${provider.length}:${provider}${ref}`;
}

function trimmedString(value: unknown): string | undefined {
  return typeof value === "string" ? value.trim() : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
