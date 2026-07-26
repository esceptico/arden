/** Compact relative-past label ("12m", "3h", "2d", "5mo"). */
export function formatRelativePast(value: string): string {
  const delta = Date.now() - new Date(value).getTime();
  const minutes = Math.max(1, Math.floor(delta / 60_000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d`;
  return `${Math.floor(days / 30)}mo`;
}

/** "6h ago" — for surfaces where a bare duration can't be told apart from
 *  a future one (activity lists mixing past and scheduled entries). */
export function formatRelativePastAgo(value: string): string {
  return `${formatRelativePast(value)} ago`;
}

/** Compact relative-future label ("in 12m", "in 3h", "in 2d"). Clamps an
 *  already-due moment to "soon" — the scheduler fires on its next tick. */
export function formatRelativeFuture(value: string): string {
  const delta = new Date(value).getTime() - Date.now();
  if (delta <= 0) return "soon";
  const minutes = Math.max(1, Math.floor(delta / 60_000));
  if (minutes < 60) return `in ${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `in ${hours}h`;
  return `in ${Math.floor(hours / 24)}d`;
}

/** "job-applications" → "Job applications" — display form of a page slug. */
export function humanizeSlug(slug: string): string {
  const spaced = slug.replace(/[-_]+/g, " ").trim();
  return spaced ? spaced[0].toUpperCase() + spaced.slice(1) : slug;
}
