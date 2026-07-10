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
