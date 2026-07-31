/** A back/forward cursor over visited locations.
 *
 *  Generic over the location type: the Memory vault stores page positions,
 *  the app shell stores AppDestinations. Both want the same semantics —
 *  pushing truncates the forward branch, revisiting the current location is
 *  a no-op, and the tail is capped. */
export class NavigationHistory<T> {
  private entries: T[] = [];
  private cursor = -1;

  constructor(
    private readonly sameDestination: (left: T, right: T) => boolean,
    private readonly limit = 100,
  ) {
    if (!Number.isInteger(limit) || limit < 1) throw new Error("Navigation history limit must be positive");
  }

  get length() { return this.entries.length; }
  get canBack() { return this.cursor > 0; }
  get canForward() { return this.cursor >= 0 && this.cursor < this.entries.length - 1; }
  get current() { return this.cursor < 0 ? null : this.entries[this.cursor] ?? null; }

  /** Read-only snapshot of visited locations, most-recent-first. Does not
   *  mutate the cursor or entries — for surfaces (like the quick switcher)
   *  that want recency ordering without touching back/forward state. */
  locations(): readonly T[] {
    return [...this.entries].reverse();
  }

  push(location: T) {
    const current = this.current;
    if (current && this.sameDestination(current, location)) return current;
    this.entries = this.entries.slice(0, this.cursor + 1);
    this.entries.push(location);
    if (this.entries.length > this.limit) this.entries.shift();
    this.cursor = this.entries.length - 1;
    return location;
  }

  replaceCurrent(location: T) {
    if (this.cursor < 0) return this.push(location);
    this.entries[this.cursor] = location;
    return location;
  }

  back() {
    if (!this.canBack) return null;
    this.cursor -= 1;
    return this.entries[this.cursor] ?? null;
  }

  forward() {
    if (!this.canForward) return null;
    this.cursor += 1;
    return this.entries[this.cursor] ?? null;
  }
}
