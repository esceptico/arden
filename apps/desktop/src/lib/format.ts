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

/** "for 11h" — how long something still open has been running. A live lane
 *  can't stamp its own item with a past-recency label ("11h ago"): the item
 *  is the present, and only its duration is in the past. */
export function formatElapsed(value: string): string {
  return `for ${formatRelativePast(value)}`;
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

/** Compact disk-size label ("812 KB", "4.3 MB", "1.9 GB"). */
export function formatBytes(bytes: number): string {
  if (bytes < 1024 ** 2) return `${Math.round(bytes / 1024)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

/** "job-applications" → "Job applications" — display form of a page slug. */
export function humanizeSlug(slug: string): string {
  const spaced = slug.replace(/[-_]+/g, " ").trim();
  return spaced ? spaced[0].toUpperCase() + spaced.slice(1) : slug;
}
